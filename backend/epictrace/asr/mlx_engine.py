"""mlx-whisper 引擎(Apple GPU / MLX):faster-whisper(CTranslate2,CPU-only on Mac)的替代。

CTranslate2 在 macOS 上无 Metal/MPS/ANE 后端 → 只能 CPU(~1x 实时,卡天花板)。mlx-whisper 跑在
Apple GPU(MLX),**完整 large-v3 fp16**,且词级时间戳/质量信号齐全(引用回跳不破)。

架构(2026-06-18,单遍):**实际只用 `transcribe_full`** —— 会话停止后整文件一次性转写(条件上下文开
= 跨段连贯,产权威转录,准确度/标点最佳)。`transcribe_window`(流式 per-window)仅供休眠的 live 模式
(worker._LIVE_TRANSCRIPTION=True)调用,平时不走。

mlx_whisper.load_model 内部 lru_cache,同 repo 重复 transcribe 复用已加载模型(不每窗重载)。
"""
from __future__ import annotations

import logging

from epictrace.asr.config import AsrConfig, auto_language
from epictrace.asr.text_normalize import ChineseSimplifier
from epictrace.asr.types import TranscriptSegment, WordTiming

_log = logging.getLogger("epictrace.asr")

# 默认 mlx 模型:**完整 large-v3(非 turbo)**。决策(2026-06-18):去掉实时预览,只做停录后一次性
# 转写,不赶实时 → 用最准的完整 large-v3(turbo 是蒸馏版,准度/标点略逊)。fp16,Apple GPU。
DEFAULT_MLX_MODEL = "mlx-community/whisper-large-v3-mlx"
# 全温度阶梯(弱音 fallback 有余量,同 faster-whisper 引擎)。
_TEMPERATURE = [0.0, 0.2, 0.4, 0.6, 0.8, 1.0]
# 标点偏置 initial_prompt:Whisper 会模仿 prompt 的风格。**turbo 对中文快语流默认几乎不出标点**
# (真机实测 96s/47 段 0 标点);喂一句带标点的中文 prompt 后立刻恢复逗号/句号/问号(实测 0→13)。
# 实测不破坏英文(英文音频喂此 prompt 仍正常转英文 + 自带标点),故 zh-first 下恒用、对 auto 安全。
_PUNCT_PROMPT = "以下是普通话的句子,请保留标点符号。"


class MlxWhisperEngine:
    """mlx-whisper 封装。model_repo 注入(默认完整 large-v3);config 提供阈值/语言。

    mlx_whisper 无内建 VAD(不同于 faster-whisper 的 vad_filter)→ 静音段靠 no_speech/logprob
    阈值 + 文本层 HallucinationFilter 兜。词级时间戳由 word_timestamps=True 提供。
    """

    def __init__(self, config: AsrConfig, model_repo: str = DEFAULT_MLX_MODEL) -> None:
        self._cfg = config
        self._repo = model_repo
        # 繁→简规整:large-v3 同样可能吐繁体,统一转简 + 喂过滤器前生效。
        self._simplify = ChineseSimplifier()

    def transcribe_window(self, pcm, *, prefix: str, source: str,
                          language: str | None = None) -> list[TranscriptSegment]:
        """流式 per-window:条件上下文关(低延迟、避免上轮文本回注漂移),供 StreamLoop 调用。

        prefix = 上轮 anchor 词文本(WordChannel.anchor_text):作 **initial_prompt(前文上下文)** 喂回,
        给模型这一窗的前文上下文。**关键:不用 mlx 的 DecodingOptions.prefix(强制续写)**——实测 mlx 的
        prefix 会让中文输出退化成空格分词且**丢标点/问号**(WhisperKit 的 prefixTokens 无此副作用,mlx 有,
        这正是此前 live 没标点的根因)。收敛改靠词级 agreement + _norm 标点/空格不敏感规整,不靠强制续写。
        condition_on_previous_text 仍关(不回注整轮历史)。initial_prompt 还前置标点偏置句(_PUNCT_PROMPT),
        让中文窗也出标点;anchor 接在其后做续写上下文。"""
        ip = _PUNCT_PROMPT + (prefix or "")
        return self._run(pcm, source=source, language=language,
                         condition_prev=False, initial_prompt=ip)

    def transcribe_full(self, audio, *, source: str,
                        language: str | None = None) -> list[TranscriptSegment]:
        """会话停止时整文件一次性:条件上下文开 = 跨段连贯,产权威转录(准确度大头)。
        audio 可为 numpy float32 16k 数组,或音频文件路径(mlx_whisper 都接受)。
        initial_prompt 用标点偏置句,让密集中文口播也出标点(turbo 默认不出)。"""
        return self._run(audio, source=source, language=language,
                         condition_prev=True, initial_prompt=_PUNCT_PROMPT)

    def _run(self, audio, *, source: str, language: str | None,
             condition_prev: bool, initial_prompt: str | None = None) -> list[TranscriptSegment]:
        import mlx_whisper

        cfg = self._cfg
        r = mlx_whisper.transcribe(
            audio,
            path_or_hf_repo=self._repo,
            language=auto_language(language or cfg.language),
            task="transcribe",
            word_timestamps=True,
            temperature=_TEMPERATURE,
            no_speech_threshold=cfg.no_speech,
            logprob_threshold=cfg.log_prob,
            compression_ratio_threshold=cfg.compression_ratio,
            condition_on_previous_text=condition_prev,
            initial_prompt=initial_prompt,
            verbose=None,
        )
        out: list[TranscriptSegment] = []
        for s in r.get("segments", []):
            words = [
                WordTiming(word=self._simplify.convert(w["word"]),
                           start=float(w["start"]), end=float(w["end"]))
                for w in (s.get("words") or [])
            ]
            out.append(TranscriptSegment(
                text=self._simplify.convert(s["text"]),
                start=float(s["start"]), end=float(s["end"]),
                source=source, words=words, confirmed=False))
        return out
