from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class AsrConfig:
    model: str = "large-v3"            # large-v3 / distil-large-v3 / medium / small
    language: str = "zh"
    vad: bool = True
    vad_threshold: float = 0.5
    no_speech: float = 0.6
    log_prob: float = -1.0
    compression_ratio: float = 2.4
    repetition_penalty: float = 1.0
    no_repeat_ngram: int = 0
    condition_prev: bool = False
    halluc_silence: float | None = 2.0
    force_confirm_after: int = 4
    stall_seek_seconds: float = 0.8
    rms_normalize: bool = True
    halluc_filter_enabled: bool = True
    input_device: int | None = None   # sounddevice 输入设备索引;None = 系统默认输入
    # 有界滑窗(STEP 1):每轮喂引擎的切片最多回看这么多秒——切片起点夹在
    # max(游标, tail-window_seconds, 缓冲头)。游标落后 tail 超过 window_seconds 时软强制
    # 确认最早 pending 段推进游标,避免长 session 把整段未确认音频反复重转(成本爆炸 + 漂移)。
    window_seconds: float = 28.0

    _VALID_MODELS = ("large-v3", "distil-large-v3", "medium", "small")

    @classmethod
    def from_dict(cls, d: dict) -> "AsrConfig":
        base = asdict(cls())
        for k, v in (d or {}).items():
            if k in base:
                base[k] = v
        base.pop("_VALID_MODELS", None)
        return cls(**base)

    def to_dict(self) -> dict:
        return asdict(self)
