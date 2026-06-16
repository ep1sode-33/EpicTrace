#!/usr/bin/env python3
"""离线 ASR 评测脚本(手动跑,不进 CI):喂一段样例 wav,模拟流式管线,打印确认 transcript +
被 HallucinationFilter 丢弃的段。

用途:为**弱音 / 讲座 / 长停顿**音频调参留口(vad/阈值/force_confirm_after/RMS 归一化…),
便于改 AsrConfig 后跑同一段音频对比 confirmed 文本 + 幻觉丢弃情况(后续接 Langfuse 评测计划)。

它复刻 worker 的流式喂入节奏(`StreamLoop` 的逐轮 tick + clip_timestamps seek),但只跑
**单源**(默认 "mic"),并在 HallucinationFilter 上挂钩记录哪些段因「精确串 / 子串 / 最近 N 去重」
被丢弃——真实管线里这些段静默消失,这里把它们打印出来供调参。

懒导入 faster_whisper(脚本手动跑,真模型;绝不在测试收集期被 import——故置于 scripts/、
无 test_ 前缀)。

用法:
    cd backend
    ./.venv/bin/python scripts/asr_eval.py <path/to/sample.wav> [--source mic|device]
        [--model large-v3] [--window 0.5] [--no-vad] [--no-rms]

输出:每轮 tick 选源 + clip_start;确认段(source/start-end/text);末尾 partial;
最后汇总 confirmed 全文 + 被丢弃的幻觉段(text + 原因)。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

# 纯逻辑模块顶层 import 安全(不拉 faster_whisper);引擎/模型在 main 里懒构造。
from epictrace.asr.config import AsrConfig
from epictrace.asr.hallucination import HallucinationFilter
from epictrace.asr.loop import StreamLoop
from epictrace.asr.types import TranscriptSegment

SAMPLE_RATE = 16000  # 全管线统一 16kHz mono float32(与 audio_sources.SAMPLE_RATE 一致)


class _RecordingFilter(HallucinationFilter):
    """包一层:在判定幻觉 / 去重时记录被丢弃的文本与原因,供评测打印。

    真实管线里这些段静默消失(StreamState 不 emit),评测要把它们暴露出来才好调参。
    StreamState 调 is_hallucination / is_duplicate 来决定是否确认,这里截下命中。
    """

    def __init__(self, **kw) -> None:
        super().__init__(**kw)
        self.dropped: list[tuple[str, str]] = []  # (text, reason)

    def is_hallucination(self, text: str) -> bool:
        hit = super().is_hallucination(text)
        if hit:
            self.dropped.append((text.strip(), "hallucination"))
        return hit

    def is_duplicate(self, text: str, recent: list[str]) -> bool:
        hit = super().is_duplicate(text, recent)
        if hit:
            self.dropped.append((text.strip(), "duplicate"))
        return hit


def _load_wav_16k_mono(path: Path) -> np.ndarray:
    """读 wav → downmix 到 16kHz mono float32(soundfile 懒导入)。非 16k 简单线性重采样。"""
    import soundfile as sf  # 懒导入:脚本手动跑环境才需要

    data, sr = sf.read(str(path), dtype="float32", always_2d=True)
    mono = data.mean(axis=1)  # 多声道取均值降单声道
    if sr != SAMPLE_RATE:
        # 简单线性重采样(评测够用;生产由 helper / sounddevice 在 16k 采)。
        n_out = int(round(mono.shape[0] * SAMPLE_RATE / sr))
        x_old = np.linspace(0.0, 1.0, num=mono.shape[0], endpoint=False)
        x_new = np.linspace(0.0, 1.0, num=n_out, endpoint=False)
        mono = np.interp(x_new, x_old, mono).astype(np.float32)
    return mono


def _build_engine(cfg: AsrConfig):
    """构建真 faster-whisper 引擎(Apple Silicon int8)。懒导入:仅脚本运行时拉重依赖。"""
    from faster_whisper import WhisperModel

    from epictrace.asr.engine import FasterWhisperEngine
    from epictrace.config import AppConfig

    cache_dir = AppConfig().asr_model_dir
    cache_dir.mkdir(parents=True, exist_ok=True)
    model = WhisperModel(cfg.model, download_root=str(cache_dir), compute_type="int8")
    return FasterWhisperEngine(model, cfg)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="asr_eval", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("wav", help="样例 wav 路径")
    p.add_argument("--source", default="mic", choices=["mic", "device"],
                   help="模拟的单一音源通道(默认 mic)")
    p.add_argument("--model", default=None, help="覆盖模型(默认 AsrConfig 默认 large-v3)")
    p.add_argument("--window", type=float, default=0.5,
                   help="每轮喂入推进的秒数(模拟 worker 的 tick 间隔,默认 0.5)")
    p.add_argument("--no-vad", action="store_true", help="关 VAD(对比静音段处理)")
    p.add_argument("--no-rms", action="store_true", help="关喂模型前 RMS 归一化")
    ns = p.parse_args(argv if argv is not None else sys.argv[1:])

    wav_path = Path(ns.wav)
    if not wav_path.is_file():
        print(f"找不到 wav:{wav_path}", file=sys.stderr)
        return 2

    overrides: dict = {}
    if ns.model:
        overrides["model"] = ns.model
    if ns.no_vad:
        overrides["vad"] = False
    if ns.no_rms:
        overrides["rms_normalize"] = False
    cfg = AsrConfig.from_dict(overrides)

    print(f"[asr_eval] 加载 {wav_path}(model={cfg.model}, vad={cfg.vad}, "
          f"rms={cfg.rms_normalize}, source={ns.source})…", file=sys.stderr)
    pcm = _load_wav_16k_mono(wav_path)
    if cfg.rms_normalize:
        from epictrace.asr.audio_sources import rms_normalize

        pcm = rms_normalize(pcm)
    total_secs = pcm.shape[0] / SAMPLE_RATE
    print(f"[asr_eval] 时长 {total_secs:.1f}s,{pcm.shape[0]} 样本", file=sys.stderr)

    print("[asr_eval] 构建 faster-whisper 引擎(首次会下载模型)…", file=sys.stderr)
    engine = _build_engine(cfg)

    # 单源 StreamLoop:用挂钩的 _RecordingFilter 替换 loop 内部的过滤器(两源共享一个),
    # 这样评测能看到被丢弃的幻觉/重复段。其余确认纪律(StreamState)完全走真实逻辑。
    confirmed: list[TranscriptSegment] = []
    last_partial: dict[str, TranscriptSegment | None] = {"seg": None}
    loop = StreamLoop(
        engine, cfg,
        on_confirmed=confirmed.append,
        on_partial=lambda seg: last_partial.__setitem__("seg", seg),
    )
    rec_filter = _RecordingFilter(enabled=cfg.halluc_filter_enabled)
    # StreamLoop 在 __init__ 里给每源建了 StreamState(共享一个 filter)。替换为挂钩 filter:
    for st in loop._states.values():  # noqa: SLF001 — 评测脚本,刻意复用内部以观测丢弃
        st._filter = rec_filter  # noqa: SLF001

    # 模拟流式:逐轮把「到目前为止」的全量 PCM 当作滚动 buffer 喂入(同 worker:read() 返回全量,
    # clip_timestamps 在全量内 seek);pending = 未确认秒数。每 window 秒推进一轮。
    channel = ns.source
    step = max(1, int(ns.window * SAMPLE_RATE))
    cursor = step
    tick_no = 0
    while True:
        cursor = min(cursor, pcm.shape[0])
        rolling = pcm[:cursor]
        st = loop._states[channel]  # noqa: SLF001
        pending = (rolling.shape[0] / SAMPLE_RATE) - st.last_confirmed_end
        loop.set_pending(**{channel: max(pending, 0.0),
                            "device" if channel == "mic" else "mic": 0.0})
        before = len(confirmed)
        loop.tick(audio={channel: rolling,
                         "device" if channel == "mic" else "mic": np.empty(0, dtype=np.float32)})
        tick_no += 1
        for seg in confirmed[before:]:
            print(f"  tick#{tick_no:>3} clip_start={st.last_confirmed_end:6.2f} "
                  f"[{seg.source}] {seg.start:6.2f}-{seg.end:6.2f}  {seg.text}")
        if cursor >= pcm.shape[0]:
            break
        cursor += step

    print("\n==== confirmed transcript ====")
    print("".join(seg.text for seg in confirmed).strip() or "(空)")

    p_seg = last_partial["seg"]
    print("\n==== 末尾 partial(未确认)====")
    print(p_seg.text.strip() if p_seg else "(无)")

    print("\n==== HallucinationFilter 丢弃的段 ====")
    if not rec_filter.dropped:
        print("(无)")
    else:
        for text, reason in rec_filter.dropped:
            print(f"  [{reason}] {text}")

    print(f"\n[asr_eval] 确认 {len(confirmed)} 段,丢弃 {len(rec_filter.dropped)} 段。",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
