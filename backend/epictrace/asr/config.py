from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class AsrConfig:
    model: str = "large-v3"            # large-v3 / medium / small(distil-large-v3 是英语专用,不入中文管线)
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
    # RMS 归一化(STEP 4)默认关:常会放大底噪/损失精度。rms_normalize 函数 + RAW
    # recent_input_rms 诊断保留,用户可显式开启,弱麦诊断仍可用。
    rms_normalize: bool = False
    halluc_filter_enabled: bool = True
    input_device: int | None = None   # sounddevice 输入设备索引;None = 系统默认输入
    # 有界滑窗(STEP 1):每轮喂引擎的切片最多回看这么多秒——切片起点夹在
    # max(游标, tail-window_seconds, 缓冲头)。游标落后 tail 超过 window_seconds 时软强制
    # 确认最早 pending 段推进游标,避免长 session 把整段未确认音频反复重转(成本爆炸 + 漂移)。
    window_seconds: float = 28.0
    # CTranslate2 计算精度(STEP 3):int8_float32 在 CPU 上比纯 int8 精度更好(权重 int8、
    # 激活 float32),开销可控;可选 "int8"(最省)/"float32"(最准最慢)。Verify 阶段会
    # 在本机 A/B 选最佳默认。
    compute_type: str = "int8_float32"

    # distil-large-v3 是英语专用模型,会毁掉中文转写 → 不作为用户可选项(STEP 6)。
    _VALID_MODELS = ("large-v3", "medium", "small")
    # CTranslate2 计算精度白名单(FIX H):未知值会让 WhisperModel 加载崩溃,设置层须挡住。
    _VALID_COMPUTE_TYPES = ("int8", "int8_float32", "float32")
    # window_seconds 合理区间(FIX H):<=0 会让切片逻辑除零/退化;过大无意义且拖慢。
    _WINDOW_SECONDS_MIN = 5.0
    _WINDOW_SECONDS_MAX = 120.0

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
