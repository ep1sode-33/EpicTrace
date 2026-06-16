"""ASR worker 子进程入口(`python -m epictrace.asr.worker`)。

后端 AsrSupervisor 在 session 选了 mic/system_audio 源时 Popen 拉起本进程,传 session_id +
选中源 + 模型。本进程:provision 好的 faster-whisper 模型 → 起选中音源(MicSource /
SystemAudioSource)→ StreamLoop 在两路间逐轮交替转写;confirmed 段 POST 回后端
/events(kind=transcription),partial POST /partial(内存态);各源同时把 PCM 追加落
staging/audio-{source}.wav(soundfile)。

faster-whisper / sounddevice / soundfile 顶层 import 是有意的:本模块只在子进程实际运行时
被加载执行,绝不在测试收集期被 import(测试只 import 同包的纯逻辑模块)。argv 解析抽成
可单测的纯函数。
"""
from __future__ import annotations

import argparse
import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

_log = logging.getLogger("epictrace.asr.worker")

# 后端本地回调地址(同 Plan 8 shell 的 _post_event,不绕前端)。
_BACKEND = "http://127.0.0.1:8765"
# worker 内部把 system_audio 源映射成 StreamState/事件里的 "device"(spec 用 mic/device 二元)。
_SOURCE_TO_CHANNEL = {"mic": "mic", "system_audio": "device"}
_TICK_INTERVAL = 0.5  # 秒:两路交替循环每轮间隔


@dataclass(frozen=True)
class WorkerArgs:
    session_id: int
    sources: list[str]
    staging_dir: str
    model: str


def parse_args(argv: list[str]) -> WorkerArgs:
    """解析 worker 命令行(纯函数,可单测)。argv 不含程序名(同 argparse 习惯)。"""
    p = argparse.ArgumentParser(prog="epictrace.asr.worker")
    p.add_argument("--session", dest="session", type=int, required=True)
    p.add_argument("--staging", dest="staging", required=True)
    p.add_argument("--model", dest="model", default="large-v3")
    p.add_argument("--sources", dest="sources", nargs="+", required=True)
    ns = p.parse_args(argv)
    return WorkerArgs(session_id=ns.session, sources=list(ns.sources),
                      staging_dir=ns.staging, model=ns.model)


def _post(path: str, body: dict) -> None:
    """POST JSON 回后端;失败只记日志(子进程不应因一次网络抖动崩掉)。"""
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"{_BACKEND}{path}", data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=5).close()
    except (urllib.error.URLError, OSError) as e:
        _log.warning("ASR worker POST %s failed: %s", path, e)


def _post_confirmed(session_id: int, seg) -> None:
    # confirmed 段 → 持久事件 capture_events(kind=transcription,meta 带 source + 词级时间戳)。
    meta = {
        "source": seg.source,
        "start": seg.start,
        "end": seg.end,
        "words": [{"w": w.word, "s": w.start, "e": w.end} for w in seg.words],
    }
    _post(f"/api/capture/sessions/{session_id}/events",
          {"kind": "transcription", "payload": seg.text, "meta": meta})


def _post_partial(session_id: int, seg) -> None:
    # partial → 后端内存态(经 SSE 推 HUD),不落库。
    _post(f"/api/capture/sessions/{session_id}/partial",
          {"source": seg.source, "text": seg.text})


def _build_engine(model: str, cache_dir: str):
    """构建 provision 好的 faster-whisper 引擎(Apple Silicon int8)。子进程内执行,真模型。"""
    from faster_whisper import WhisperModel

    from epictrace.asr.config import AsrConfig
    from epictrace.asr.engine import FasterWhisperEngine

    cfg = AsrConfig(model=model)
    whisper = WhisperModel(model, download_root=cache_dir, compute_type="int8")
    return FasterWhisperEngine(whisper, cfg), cfg


def main(argv: list[str] | None = None) -> int:
    import sys

    import soundfile as sf

    from epictrace.asr.audio_sources import (
        SAMPLE_RATE,
        MicSource,
        SystemAudioSource,
    )
    from epictrace.asr.loop import StreamLoop
    from epictrace.config import AppConfig

    args = parse_args(argv if argv is not None else sys.argv[1:])
    config = AppConfig()
    config.asr_model_dir.mkdir(parents=True, exist_ok=True)
    engine, cfg = _build_engine(args.model, str(config.asr_model_dir))

    # 起选中音源。worker 内部用 mic/device 二元通道命名(system_audio → device)。
    sources: dict[str, object] = {}
    wavs: dict[str, object] = {}
    for src in args.sources:
        channel = _SOURCE_TO_CHANNEL.get(src)
        if channel is None:
            continue
        if src == "mic":
            s = MicSource(rms_normalize_enabled=cfg.rms_normalize)
        else:
            # 系统内录 helper 二进制路径(随 app 构建到 data_dir/bin;Task 12)。
            helper = str(config.data_dir / "bin" / "epictrace-sysaudio")
            s = SystemAudioSource(helper, rms_normalize_enabled=cfg.rms_normalize)
        try:
            s.start()
        except Exception as e:  # noqa: BLE001 — 某路起不来不拖垮其余源
            _log.error("ASR source %s failed to start: %s", src, e)
            continue
        sources[channel] = s
        # 原始音频边录边追加落 staging/audio-{channel}.wav(16k mono),供回放/重转写。
        wav_path = f"{args.staging_dir}/audio-{channel}.wav"
        wavs[channel] = sf.SoundFile(wav_path, mode="w", samplerate=SAMPLE_RATE,
                                     channels=1, subtype="FLOAT")

    if not sources:
        _log.error("ASR worker: no audio source started, exiting")
        return 1

    loop = StreamLoop(
        engine, cfg,
        on_confirmed=lambda seg: _post_confirmed(args.session_id, seg),
        on_partial=lambda seg: _post_partial(args.session_id, seg),
    )

    written = {ch: 0 for ch in sources}
    try:
        while True:
            audio = {"mic": b"", "device": b""}
            pending = {"mic": 0.0, "device": 0.0}
            for channel, s in sources.items():
                pcm = s.read()
                audio[channel] = pcm
                pending[channel] = s.pending_seconds()
                # 增量把新样本写入 wav(read 返回全量,只写未写过的尾巴)。
                if pcm.shape[0] > written[channel]:
                    wavs[channel].write(pcm[written[channel]:])
                    written[channel] = pcm.shape[0]
            loop.set_pending(mic=pending["mic"], device=pending["device"])
            loop.tick(audio=audio)
            time.sleep(_TICK_INTERVAL)
    except KeyboardInterrupt:
        pass
    finally:
        for s in sources.values():
            s.stop()
        for w in wavs.values():
            w.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
