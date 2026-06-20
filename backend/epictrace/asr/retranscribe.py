"""一次性重转子进程(`python -m epictrace.asr.retranscribe`)。

会话停止后,后端拉起本进程:对 staging 里每个 audio-*.wav 用 mlx-whisper **整文件重转**
(condition_on_previous_text=True → 跨窗上下文,比流式切片连贯准确得多),POST 回后端**替换**该
session 的流式转录事件,产权威转录(入暂存区 + 后续 organize 入库)。

隔离在子进程(同 worker 的理由:避开主进程 milvus/embedder 的 macOS fork 段错误;mlx/Metal 独占)。
faster-whisper/mlx/soundfile 都在函数体内懒导入,测试只 import 纯逻辑(parse/channel 映射)。
"""
from __future__ import annotations

import argparse
import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass

from epictrace.asr.config import AsrConfig

_log = logging.getLogger("epictrace.asr.retranscribe")
_BACKEND = "http://127.0.0.1:8765"


@dataclass(frozen=True)
class RetranscribeArgs:
    session_id: int
    staging_dir: str
    config: AsrConfig
    model: str | None = None  # mlx 模型 repo 覆盖;None = 引擎默认(完整 large-v3)


def parse_args(argv: list[str]) -> RetranscribeArgs:
    p = argparse.ArgumentParser(prog="epictrace.asr.retranscribe")
    p.add_argument("--session", dest="session", type=int, required=True)
    p.add_argument("--staging", dest="staging", required=True)
    p.add_argument("--config", dest="config", default=None)
    p.add_argument("--model", dest="model", default=None)
    ns = p.parse_args(argv)
    cfg = AsrConfig.from_dict(json.loads(ns.config)) if ns.config else AsrConfig()
    return RetranscribeArgs(session_id=ns.session, staging_dir=ns.staging,
                            config=cfg, model=ns.model)


def channel_of(wav_name: str) -> str:
    """从 audio-{channel}-{ts}.wav 文件名解析通道(mic/device);不符合则回 mic(纯函数,可单测)。"""
    parts = wav_name.split("-")
    if len(parts) >= 3 and parts[0] == "audio" and parts[1] in ("mic", "device"):
        return parts[1]
    return "mic"


def wav_timestamp(wav_name: str) -> int | None:
    """从 audio-{channel}-{ts}.wav 解析毫秒级时间戳(worker 用 int(time.time()*1000) 命名);
    解析不出 → None。纯函数,可单测。"""
    stem = wav_name[:-4] if wav_name.endswith(".wav") else wav_name
    parts = stem.split("-")
    if len(parts) >= 3 and parts[-1].isdigit():
        return int(parts[-1])
    return None


def _to_asr_16k(data, sr: int):
    """把录音(48k、可能立体声)降成 Whisper 要的 **16kHz 单声道 float32**:先按声道均值合并单声道,
    再多相重采样(scipy.signal.resample_poly,带抗混叠)。sr 已是 16k → 仅合并声道直接返回。
    numpy/scipy 在函数体内懒导入(纯逻辑测试不强依赖)。可单测。"""
    import numpy as np

    if getattr(data, "ndim", 1) > 1:        # 立体声 → 单声道(声道均值)
        data = data.mean(axis=1)
    out = np.ascontiguousarray(data, dtype=np.float32)
    if int(sr) == 16000:
        return out
    from math import gcd
    from scipy.signal import resample_poly
    g = gcd(16000, int(sr))
    return resample_poly(out, 16000 // g, int(sr) // g).astype(np.float32)


def session_offsets(wav_names: list[str]) -> dict[str, float]:
    """各 wav 相对会话起点(= 最早 wav 时间戳)的**秒**偏移。pause/resume 每段是新 wav(新毫秒戳),
    据此把每段转录放回真实会话时间线位置(否则各段都按段内 0 基偏移堆到开头)。文件名是毫秒戳,
    相减后 /1000 转秒;同一时钟相减,无时区问题。解析不出时间戳的 wav → 偏移 0(降级,不报错)。"""
    tss = {n: wav_timestamp(n) for n in wav_names}
    known = [t for t in tss.values() if t is not None]
    t0 = min(known) if known else 0
    return {n: max(0.0, (t - t0) / 1000.0) if t is not None else 0.0 for n, t in tss.items()}


def _post(path: str, body: dict) -> None:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(f"{_BACKEND}{path}", data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=120).close()
    except (urllib.error.URLError, OSError) as e:
        _log.warning("retranscribe POST %s failed: %s", path, e)


def main(argv: list[str] | None = None) -> int:
    import os
    import sys
    from pathlib import Path

    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")  # 关「Fetching N files」噪声
    args = parse_args(argv if argv is not None else sys.argv[1:])

    import soundfile as sf

    from epictrace.asr.hallucination import HallucinationFilter
    from epictrace.asr.mlx_engine import DEFAULT_MLX_MODEL, MlxWhisperEngine

    engine = MlxWhisperEngine(args.config, args.model or DEFAULT_MLX_MODEL)
    # 整文件重转同样过文本层幻觉过滤:一次性 condition_prev=True 在弱音尾巴仍可能退化复读
    # (实测「undenunden…×N」「是是是…」),否则会进权威转录入库。空文本/幻觉段丢弃,其余 clean。
    hf = HallucinationFilter(enabled=args.config.halluc_filter_enabled)
    segments: list[dict] = []
    # 按文件名排序保证稳定顺序(pause/resume 产生的分段 wav 带时间戳,自然升序)。
    wav_paths = sorted(Path(args.staging_dir).glob("audio-*.wav"))
    # 各 wav 在会话时间线上的起点偏移(pause/resume 分段必须各自平移,否则都堆到开头)。
    offsets = session_offsets([p.name for p in wav_paths])
    for wav in wav_paths:
        if not wav.is_file():
            continue
        ch = channel_of(wav.name)
        wav_off = offsets.get(wav.name, 0.0)
        try:
            data, sr = sf.read(str(wav), dtype="float32")
            audio16k = _to_asr_16k(data, sr)   # 录音是 48k(可能立体声)→ 降成 16k mono 喂 Whisper
        except Exception as e:  # noqa: BLE001 — 单个 wav 读/重采样失败不拖垮其余
            _log.warning("retranscribe read %s failed: %s", wav, e)
            continue
        try:
            segs = engine.transcribe_full(audio16k, source=ch)
        except Exception as e:  # noqa: BLE001 — 单个 wav 转写失败跳过
            _log.warning("retranscribe transcribe %s failed: %s", wav, e)
            continue
        for s in segs:
            if hf.is_hallucination(s.text):
                continue
            text = hf.clean(s.text)
            if not text:
                continue
            # start/end = 会话相对(wav_off + 段内时间)→ 时间线放回真实位置;
            # audio_offset/words = 段内时间(回跳定位用该 wav 内位置)。
            segments.append({
                "source": s.source, "text": text,
                "start": wav_off + s.start, "end": wav_off + s.end,
                "audio_offset": s.start, "wav": wav.name,
                "words": [{"w": w.word, "s": w.start, "e": w.end} for w in s.words],
            })
    # 替换该 session 的流式转录事件(空 segments 也 POST:让后端清掉过渡态/标志)。
    _post(f"/api/capture/sessions/{args.session_id}/transcript", {"segments": segments})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
