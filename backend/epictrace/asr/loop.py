from __future__ import annotations

from collections.abc import Callable

import numpy as np

from epictrace.asr.config import AsrConfig
from epictrace.asr.hallucination import HallucinationFilter
from epictrace.asr.types import TranscriptSegment, WordTiming
from epictrace.asr.word_channel import WordChannel

# 全管线统一 16kHz(= audio_sources.SAMPLE_RATE);此处本地常量,避免 loop 为一个数字 import
# audio_sources 而把 sounddevice/PortAudio 拖进纯逻辑/测试。
_SAMPLE_RATE = 16000
_MIN_FLUSH_TAIL = 0.2  # 秒:flush 时未处理音频超过它才值得转(收短尾;低于正常 chunk 门)
# 能量门(关键):切片 RMS 低于它视为近静音 → 跳过转写。mlx 无内建 VAD,静音/弱噪喂进去会
# 脑补水印幻觉(如「优优独播剧场」)且空烧 GPU;系统内录被拒时 tap 吐纯零(RMS=0)更是如此。
# 1e-3 ≈ 数字静音/底噪,正常语音(含轻声 ~1e-2)远高于它,不会误跳真实语音。仅在 rms_normalize
# 关(默认)时切片是原始电平、门最准;开归一化时该门会偏松(用户自担弱麦取舍)。
_SILENCE_RMS = 1e-3


def _slice_rms(pcm) -> float:
    """切片音频的 RMS 能量。pcm 为 np.ndarray(真源)或可转 float32 的 bytes;空/不可解释 → 放行。"""
    if isinstance(pcm, np.ndarray):
        a = pcm
    else:
        try:
            a = np.frombuffer(bytes(pcm), dtype=np.float32)
        except (TypeError, ValueError):
            return 1.0  # 无法解释为音频 → 不门控(放行,交给下游)
    if a.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(a.astype(np.float64)))))


class StreamLoop:
    """单引擎在 mic/device 间逐轮交替(ASR 笔记 §5)。纯逻辑:音源对象由调用方喂(set_sources)。

    确认核心 = **词级 prefix-agreement + 锚定滑窗**(WordChannel,移植自参考产品生产算法):
    每轮选「未扫描音频更多」的一路,切片**锚在该路已确认边界**(`last_agreed - slice_padding`,
    长 `max_slice`),交引擎转写(引擎返回 slice-相对时间),把词平移 +slice_start 回会话绝对时间,
    喂 WordChannel 做逐词 LCP 确认。窗口锚在已确认边界(非 tail)→ 滑窗本身即冷启动追赶,长 session
    缓冲截断后绝对时间不漂。anchor 词回喂引擎 initial_prompt 维持上下文。
    """

    def __init__(self, engine, config: AsrConfig,
                 on_confirmed: Callable[[TranscriptSegment], None],
                 on_partial: Callable[[TranscriptSegment], None]) -> None:
        self._engine = engine
        self._cfg = config
        self._on_confirmed = on_confirmed
        self._on_partial = on_partial
        # 存住 filter:reset_channel(再启用某源)要拿它重建 WordChannel。
        self._filter = HallucinationFilter(enabled=config.halluc_filter_enabled)
        self._states: dict[str, WordChannel] = {
            "mic": self._new_state("mic"),
            "device": self._new_state("device"),
        }
        self._sources: dict[str, object] = {}

    def _new_state(self, ch: str) -> WordChannel:
        c = self._cfg
        return WordChannel(
            ch, self._filter,
            anchor_words=c.anchor_words, force_confirm_after=c.force_confirm_after,
            slice_padding=c.slice_padding, max_slice=c.max_slice,
            stall_seek=c.stall_seek_seconds)

    def set_sources(self, sources: dict[str, object]) -> None:
        """注入当前可用音源({channel: source});source 须有 base_offset/available_seconds/window_from。"""
        self._sources = sources

    def reset_channel(self, ch: str) -> None:
        """重置某路的 WordChannel(游标归零、清 anchor/partial)。动态启用某源时调用:新启的源带的是
        全新 ring buffer(base_offset 从 0 起、wav 也是新分段文件),旧游标(可能停在几十秒处)会
        与之错位让切片逻辑空转 → 必须把该路游标清回 0,与新源对齐。未知通道 = no-op。"""
        if ch in self._states:
            self._states[ch] = self._new_state(ch)

    def skip_channel_to(self, ch: str, abs_seconds: float) -> None:
        """把某路两游标单调推进到 abs_seconds(FIX A 软静音用):静音区间既不被转写也不卡调度,
        unmute 后从「现在」继续。未知通道 = no-op。"""
        st = self._states.get(ch)
        if st is not None:
            st.skip_to(abs_seconds)

    def _unprocessed(self, channel: str) -> float:
        """该路未扫描秒数 = 绝对末端 - 已扫描游标。用 scanned_end(每轮推到切片末端,含 0 段)而非
        确认游标:静音/VAD 空的源被扫过后未扫描量归零,不再霸占调度器(否则确认游标停滞 → 该源永远
        「未处理量最大」→ 另一源被饿死、同段无限重解码)。"""
        src = self._sources.get(channel)
        if src is None:
            return 0.0
        return max(0.0, src.available_seconds() - self._states[channel].scanned_end)

    def _pick_source(self) -> str | None:
        """选未扫描音频最多、且攒够 chunk_seconds(转写门;CJK=2.0,参考产品实测最优)的一路。"""
        chunk = self._cfg.chunk_seconds
        cand = [(self._unprocessed(ch), ch) for ch in self._sources]
        cand = [(u, ch) for u, ch in cand if u >= chunk]
        if not cand:
            return None
        return max(cand)[1]

    def tick(self) -> None:
        ch = self._pick_source()
        if ch is None:
            return
        self._transcribe_channel(ch)

    def _transcribe_channel(self, ch: str) -> None:
        """转写某路一个锚定滑窗,平移回绝对时间,喂 WordChannel 并 emit。

        切片 = [max(缓冲头, last_agreed - slice_padding), min(tail, slice_start + max_slice)]。
        window_from 返回到缓冲末端 → 按 max_slice 截断(冷启动 backlog 时只解一窗,逐 tick 推进)。
        本轮末尾推进 scanned_end 到切片末端(含 0 段)使调度按未扫描量排序。"""
        src = self._sources[ch]
        st = self._states[ch]
        cfg = self._cfg
        tail = src.available_seconds()
        base = src.base_offset()
        st.clamp_to_base(base)  # 缓冲头滚过已确认位 → 跳游标、清旧 anchor
        slice_start = max(base, st.last_agreed_seconds - cfg.slice_padding)
        slice_end = min(tail, slice_start + cfg.max_slice)
        if slice_end <= slice_start:
            st.mark_scanned(slice_end)
            return
        pcm = src.window_from(slice_start)
        # window_from 给到缓冲末端 → 截到本窗 max_slice 上界(避免冷启动一窗解码整段 backlog)。
        max_samples = int(round((slice_end - slice_start) * _SAMPLE_RATE))
        if max_samples > 0 and hasattr(pcm, "__len__") and len(pcm) > max_samples:
            pcm = pcm[:max_samples]
        # 能量门:近静音切片直接跳过(不喂引擎)——防 mlx 无 VAD 下静音幻觉 + 空烧 GPU。
        # 仍推进 scanned_end(调度按未扫描量排序,静音源不霸占),但不动确认游标(下次有声再转)。
        if _slice_rms(pcm) < _SILENCE_RMS:
            st.mark_scanned(slice_end)
            return
        prefix = st.anchor_text()  # anchor 词喂引擎 initial_prompt 维持上下文
        segs = self._engine.transcribe_window(pcm, prefix=prefix, source=ch)
        abs_words = self._flatten_words(segs, slice_start)
        for c in st.ingest(abs_words, buffer_end=tail):
            self._on_confirmed(c)
        if st.partial is not None:
            self._on_partial(st.partial)
        st.mark_scanned(slice_end)

    @staticmethod
    def _flatten_words(segs: list[TranscriptSegment], offset: float) -> list[WordTiming]:
        """把本轮各段的词级时间戳扁平成一个有序词数组并平移回会话绝对时间。
        某段没有词级时间(引擎极少这样)→ 整段合成一个词,内容不丢(降级到段级)。"""
        words: list[WordTiming] = []
        for s in segs:
            if s.words:
                words.extend(s.words)
            elif s.text and s.text.strip():
                words.append(WordTiming(word=s.text, start=s.start, end=s.end))
        return WordChannel.shift_words(words, offset)

    def flush_channel(self, ch: str) -> None:
        """只排空某一路的短尾(FIX D):若该路有 ANY 未处理音频(> _MIN_FLUSH_TAIL≈0.2s,低于
        正常 chunk 门)就强转一次(_transcribe_channel 不受 chunk 门约束)、平移、ingest;再对该路
        WordChannel.flush() 强制确认残留尾巴并 emit。worker 的 IDLE 检测对「转 IDLE 的那一路」调用
        本方法——只确认它自己的短尾,绝不因一路空闲就强制确认另一路 mid-utterance 的尾巴。
        无未处理音频且无残尾的源是 no-op(故可幂等重复调用)。"""
        if ch not in self._sources:
            return
        if self._unprocessed(ch) > _MIN_FLUSH_TAIL:
            self._transcribe_channel(ch)
        for c in self._states[ch].flush():
            self._on_confirmed(c)

    def flush(self) -> None:
        """排空所有路的短尾(收尾 stop 用):逐路调 flush_channel。"""
        for ch in list(self._sources):
            self.flush_channel(ch)
