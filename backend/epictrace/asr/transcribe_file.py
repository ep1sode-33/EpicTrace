"""通用单文件转写子进程(`python -m epictrace.asr.transcribe_file`)。

cowork 的 transcribe_audio 工具用:给定任意音频文件路径,整文件转写(mlx-whisper),
结果以 JSON 打印到 stdout:{"segments": [{text, start, end, words}]}。

与 retranscribe 的区别:不绑定 capture session、不 POST 回后端,纯粹「文件进,JSON 出」。
同样隔离在子进程(macOS fork 段错误护栏 + mlx/Metal 独占,见 retranscribe docstring)。
"""
from __future__ import annotations

import argparse
import json
import logging

from epictrace.asr.config import AsrConfig

_log = logging.getLogger("epictrace.asr.transcribe_file")


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="epictrace.asr.transcribe_file")
    p.add_argument("audio", help="音频文件路径(wav/mp3/m4a 等 mlx_whisper 支持的格式)")
    p.add_argument("--config", dest="config", default=None)
    p.add_argument("--model", dest="model", default=None)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    import os
    import sys

    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    args = parse_args(argv if argv is not None else sys.argv[1:])

    import soundfile as sf

    from epictrace.asr.hallucination import HallucinationFilter
    from epictrace.asr.mlx_engine import DEFAULT_MLX_MODEL, MlxWhisperEngine
    from epictrace.asr.retranscribe import _to_asr_16k

    cfg = AsrConfig.from_dict(json.loads(args.config)) if args.config else AsrConfig()
    engine = MlxWhisperEngine(cfg, args.model or DEFAULT_MLX_MODEL)
    hf = HallucinationFilter(enabled=cfg.halluc_filter_enabled)

    try:
        data, sr = sf.read(args.audio, dtype="float32")
    except Exception:  # noqa: BLE001 — soundfile 不认的格式(mp3/m4a)让 mlx 自行解码
        data, sr = None, None
    # soundfile 能读 → 降 16k mono;读不了 → 直接把路径交给 mlx_whisper(自带 ffmpeg 解码)
    audio = _to_asr_16k(data, sr) if data is not None else args.audio

    segs = engine.transcribe_full(audio, source="mic")
    out = []
    for s in segs:
        if hf.is_hallucination(s.text):
            continue
        text = hf.clean(s.text)
        if not text:
            continue
        out.append({
            "text": text, "start": s.start, "end": s.end,
            "words": [{"w": w.word, "s": w.start, "e": w.end} for w in s.words],
        })
    json.dump({"segments": out}, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
