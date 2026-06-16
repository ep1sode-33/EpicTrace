from __future__ import annotations

from epictrace.asr.config import AsrConfig
from epictrace.asr.types import TranscriptSegment, WordTiming


class FasterWhisperEngine:
    """封装 faster-whisper 的单窗口转写。model 注入(测试用假件;生产 = WhisperModel)。"""

    def __init__(self, model, config: AsrConfig) -> None:
        self._model = model
        self._cfg = config

    def transcribe_window(self, pcm, *, prefix: str,
                          source: str, language: str | None = None) -> list[TranscriptSegment]:
        cfg = self._cfg
        # 与 asr-streaming-tuning-notes §4「绝不手动切 buffer」的分歧:那条经验来自 WhisperKit
        # (用 clipTimestamps 让引擎内部 seek)。faster-whisper 1.2.1 可安全手动切片 —— 而且它
        # **仅在 clip_timestamps=="0" 时跑 VAD**(transcribe.py:`if vad_filter and clip_timestamps=="0"`),
        # 传任何绝对 clip 都会静默关掉 VAD。故本引擎只接收**已切好的 slice**(调用方 StreamLoop
        # 负责切片 + 把返回的 slice-相对时间戳平移回绝对时间),clip_timestamps 恒为 "0" 让 VAD 生效。
        segments, _info = self._model.transcribe(
            pcm,
            language=language or cfg.language,
            task="transcribe",
            clip_timestamps="0",
            initial_prompt=prefix or None,
            word_timestamps=True,
            suppress_blank=True,
            temperature=[0.0, 0.2],
            compression_ratio_threshold=cfg.compression_ratio,
            log_prob_threshold=cfg.log_prob,
            no_speech_threshold=cfg.no_speech,
            vad_filter=cfg.vad,
            vad_parameters={"threshold": cfg.vad_threshold},
            hallucination_silence_threshold=cfg.halluc_silence,
            repetition_penalty=cfg.repetition_penalty,
            no_repeat_ngram_size=cfg.no_repeat_ngram,
            condition_on_previous_text=cfg.condition_prev,
        )
        out: list[TranscriptSegment] = []
        for s in segments:
            words = [WordTiming(word=w.word, start=w.start, end=w.end)
                     for w in (getattr(s, "words", None) or [])]
            out.append(TranscriptSegment(
                text=s.text, start=s.start, end=s.end, source=source,
                words=words, confirmed=False))
        return out
