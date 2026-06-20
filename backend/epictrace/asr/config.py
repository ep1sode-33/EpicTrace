from __future__ import annotations

from dataclasses import asdict, dataclass


def auto_language(lang: str | None) -> str | None:
    """把 "auto"/""/None 归一成 None(Whisper 据音频自动检测语言);其余语言码原样返回。
    供引擎传给 whisper 用——默认 auto 让中/英/混说都能识别(此前硬 zh 把英语解码成谐音乱码)。"""
    return None if not lang or lang == "auto" else lang


@dataclass(frozen=True)
class AsrConfig:
    model: str = "large-v3"            # large-v3 / medium / small(distil-large-v3 是英语专用,不入中文管线)
    # 默认 auto:Whisper 自动检测语言(中文窗口检 zh、英文窗口检 en),不再硬锁 zh 把英语转成乱码。
    # 想锁定可在设置改 "zh"/"en"(纯中文锁 zh 质量更稳)。
    language: str = "auto"
    vad: bool = True
    vad_threshold: float = 0.5
    # VAD 最短语音块时长(ms):faster-whisper VadOptions 默认 0 = 不丢任何短块,近静音极短 blip
    # 也送进解码器 → Whisper 脑补「谢谢大家」类静音幻觉。设 250 让 VAD 不放行 <250ms 的碎块
    # (speech_pad_ms=400 仍向两侧扩边,正常 0.3~1s 短句照过);弱场真漏短句可真机调回 150。
    vad_min_speech_ms: int = 250
    no_speech: float = 0.6
    log_prob: float = -1.0
    compression_ratio: float = 2.4
    # 轻度复读惩罚(弱音 beam search 易陷复读,如「我会去看你 我会去看你」):1.1 是软惩罚,
    # 降低 loop 概率而不硬禁,对正常语音影响小。**no_repeat_ngram 保持 0**:硬禁 n-gram 会压掉
    # 合法中文重复(「测试测试测试」「对对对」),段内立即重复改由文本层 is_intra_segment_loop 精准兜。
    repetition_penalty: float = 1.1
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
    # 词级 prefix-agreement 锚定滑窗(移植自参考产品生产参数;见 word_channel.py / loop.py):
    # - chunk_seconds:转写门——某路攒够这么多「未扫描」新音频才转一轮。参考产品 CJK 实测 mn=2.0
    #   是最优(bench:1.0s baseline 17 分钟只抓 17 字,2.0s 抓 601 字,35x)。**这是 live 准度大头**。
    # - slice_padding:每轮切片从 last_agreed - 它 起(回看,给模型声学上下文)。
    # - max_slice:切片最长(= 冷启动追赶步长;窗口锚在 last_agreed 而非 tail,本身即追赶)。
    # - anchor_words(tc):LCP 去掉末尾这么多词作 confirmed,留作 anchor。CJK 逐字确认 = 1。
    chunk_seconds: float = 2.0
    slice_padding: float = 2.0
    max_slice: float = 15.0
    anchor_words: int = 1
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
