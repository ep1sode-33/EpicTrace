from __future__ import annotations

from epictrace.asr.config import AsrConfig, auto_language
from epictrace.asr.text_normalize import ChineseSimplifier
from epictrace.asr.types import TranscriptSegment, WordTiming


class FasterWhisperEngine:
    """封装 faster-whisper 的单窗口转写。model 注入(测试用假件;生产 = WhisperModel)。"""

    def __init__(self, model, config: AsrConfig) -> None:
        self._model = model
        self._cfg = config
        # 繁体→简体规整器(单例,构建一次):large-v3+zh 常吐繁体,在产文本处统一转简,
        # 既根治可读性,又让下游 HallucinationFilter 的简体精确表能命中(见 text_normalize)。
        self._simplify = ChineseSimplifier()

    def transcribe_window(self, pcm, *, prefix: str,
                          source: str, language: str | None = None) -> list[TranscriptSegment]:
        cfg = self._cfg
        # 与 asr-streaming-tuning-notes §4「绝不手动切 buffer」的分歧:那条经验来自 WhisperKit
        # (用 clipTimestamps 让引擎内部 seek)。faster-whisper 1.2.1 可安全手动切片 —— 而且它
        # **仅在 clip_timestamps=="0" 时跑 VAD**(transcribe.py:`if vad_filter and clip_timestamps=="0"`),
        # 传任何绝对 clip 都会静默关掉 VAD。故本引擎只接收**已切好的 slice**(调用方 StreamLoop
        # 负责切片 + 把返回的 slice-相对时间戳平移回绝对时间),clip_timestamps 恒为 "0" 让 VAD 生效。
        #
        # 上下文策略(词级 agreement):prefix = 上轮 anchor 词文本(WordChannel.anchor_text),
        # 当 initial_prompt 喂回,给模型这一窗的前文上下文(对齐参考产品 prefixTokens=lastAgreedWords)。
        # 仅 1~少数字,偏置极小但稳定滑窗交界处逐字 hypothesis,是词级 agreement 收敛的关键。
        # condition_on_previous_text 仍关(cfg.condition_prev=False),避免把整轮历史文本回注放大漂移。
        # (此前段数确认设计曾刻意不 seed initial_prompt;换词级 agreement 后 anchor-prefix 是算法一部分。)
        segments, _info = self._model.transcribe(
            pcm,
            language=auto_language(language or cfg.language),
            task="transcribe",
            clip_timestamps="0",
            initial_prompt=prefix or None,
            word_timestamps=True,
            suppress_blank=True,
            # 全温度阶梯(回到引擎原生默认):弱音命中 log_prob/compression 阈触发 fallback 时,
            # 截到 2 级(旧的流式延迟取舍)温度余量不足、易困在贪心陷阱吐繁体/碎片/复读。本管线是
            # 手动切片 + 有界滑窗(非逐秒重转),偶发多级 fallback 成本可控。
            temperature=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
            compression_ratio_threshold=cfg.compression_ratio,
            log_prob_threshold=cfg.log_prob,
            no_speech_threshold=cfg.no_speech,
            vad_filter=cfg.vad,
            # min_speech_duration_ms:VAD 不放行 <该值的极短碎块(近静音幻觉的源头),
            # speech_pad_ms 默认仍向两侧扩边,正常短句照过。
            vad_parameters={"threshold": cfg.vad_threshold,
                            "min_speech_duration_ms": cfg.vad_min_speech_ms},
            hallucination_silence_threshold=cfg.halluc_silence,
            repetition_penalty=cfg.repetition_penalty,
            no_repeat_ngram_size=cfg.no_repeat_ngram,
            condition_on_previous_text=cfg.condition_prev,
        )
        # 繁体→简体规整(逐 word 独立转 + 整段 text 转):word 数不变、start/end 原样保留,
        # 时间戳对齐不破;且在喂 StreamState/HallucinationFilter 之前,简体过滤表得以命中。
        out: list[TranscriptSegment] = []
        for s in segments:
            words = [WordTiming(word=self._simplify.convert(w.word), start=w.start, end=w.end)
                     for w in (getattr(s, "words", None) or [])]
            out.append(TranscriptSegment(
                text=self._simplify.convert(s.text), start=s.start, end=s.end, source=source,
                words=words, confirmed=False))
        return out
