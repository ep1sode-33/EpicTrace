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
import signal
import time
import urllib.error
import urllib.request
from dataclasses import dataclass

from epictrace.asr.config import AsrConfig

_log = logging.getLogger("epictrace.asr.worker")

# 后端本地回调地址(同 Plan 8 shell 的 _post_event,不绕前端)。
_BACKEND = "http://127.0.0.1:8765"
# worker 内部把 system_audio 源映射成 StreamState/事件里的 "device"(spec 用 mic/device 二元)。
_SOURCE_TO_CHANNEL = {"mic": "mic", "system_audio": "device"}
_TICK_INTERVAL = 0.5  # 秒:两路交替循环每轮间隔
_MUTE_POLL_INTERVAL = 1.0  # 秒:轮询后端软静音集的间隔(比 0.5s tick 慢,避免每轮打网络)
_IDLE_FLUSH_SECS = 1.5  # 秒:某路 available_seconds 停止增长这么久 = 停顿/IDLE → flush 一次短尾(FIX 3)


def active_channels(started: set[str], muted_sources: list[str]) -> set[str]:
    """由已起的 worker 通道集 + 前端静音源 id 列表,算出仍活跃(应被读取/转写/落 wav)的通道。

    纯函数(可单测):前端源 id(mic/system_audio)经 _SOURCE_TO_CHANNEL 映射成 worker 通道
    (mic/device),从已起通道集里剔除被静音的;未起的源即便在静音列表里也无副作用。
    """
    muted_channels = {_SOURCE_TO_CHANNEL[s] for s in muted_sources if s in _SOURCE_TO_CHANNEL}
    return {ch for ch in started if ch not in muted_channels}


def _fetch_muted(session_id: int) -> list[str] | None:
    """GET 后端某 session 的软静音集;失败回 None(FIX A:网络抖动不应被误读成「空集 = 全恢复」,
    调用方据 None 保留上次已知静音集)。"""
    url = f"{_BACKEND}/api/capture/sessions/{session_id}/asr-mute"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        muted = data.get("muted", [])
        return list(muted) if isinstance(muted, list) else []
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        _log.warning("ASR worker GET asr-mute failed: %s", e)
        return None


def apply_mute_transition(prev_active: set[str], next_active: set[str],
                          sources: dict, written: dict, loop) -> None:
    """处理活跃集变化里「转静音」的通道(FIX A)。纯函数(可单测,loop/source 用鸭子类型)。

    某路 active→muted 时,光把它从 loop 源集剔除不够:源仍在攒 PCM,而 written[ch] 不前进、
    ASR 游标也不前进 → unmute 时静音区间会被回填进 wav 并从旧游标重转。故对每个「转静音」通道:
    - written[ch] ← 当前样本数(read() 长度):跳过将静音的 backlog,unmute 后只写「现在起」的尾巴;
    - loop.skip_channel_to(ch, available_seconds()):两个 ASR 游标跳到现在,静音区间不被转写。
    仅对「转静音」(prev_active 有、next_active 无)的通道动作;转活跃(unmute)无副作用。"""
    newly_muted = prev_active - next_active
    for ch in newly_muted:
        src = sources.get(ch)
        if src is None:
            continue
        try:
            written[ch] = src.read().shape[0]
        except Exception as e:  # noqa: BLE001 — 读失败不应拖垮静音切换
            _log.warning("ASR mute: advance wav offset for %s failed: %s", ch, e)
        loop.skip_channel_to(ch, src.available_seconds())


@dataclass(frozen=True)
class WorkerArgs:
    session_id: int
    sources: list[str]
    staging_dir: str
    model: str
    config: AsrConfig
    cache_dir: str | None = None


def parse_args(argv: list[str]) -> WorkerArgs:
    """解析 worker 命令行(纯函数,可单测)。argv 不含程序名(同 argparse 习惯)。

    --config 是路由经 SettingsService 解析好的完整 ASR 设置 JSON,回程成 AsrConfig(vad/阈值/
    force_confirm_after 等非默认值都生效,FIX D);无 --config 时落 model 单字段(其余默认)。
    --cache-dir 是 ASR 模型缓存目录(与 provisioner 同一路径,FIX 2);无则落 None(用默认)。
    """
    p = argparse.ArgumentParser(prog="epictrace.asr.worker")
    p.add_argument("--session", dest="session", type=int, required=True)
    p.add_argument("--staging", dest="staging", required=True)
    p.add_argument("--model", dest="model", default="large-v3")
    p.add_argument("--config", dest="config", default=None)
    p.add_argument("--cache-dir", dest="cache_dir", default=None)
    p.add_argument("--sources", dest="sources", nargs="+", required=True)
    ns = p.parse_args(argv)
    if ns.config:
        cfg = AsrConfig.from_dict(json.loads(ns.config))
    else:
        cfg = AsrConfig(model=ns.model)
    return WorkerArgs(session_id=ns.session, sources=list(ns.sources),
                      staging_dir=ns.staging, model=ns.model, config=cfg,
                      cache_dir=ns.cache_dir)


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
    # audio_offset = 该段绝对起始秒(与落盘 wav 同一时间轴),供引用回跳定位音频位置(FIX A);
    # start/end 与词级时间戳均为会话绝对时间(StreamLoop 已把 slice-相对时间平移回绝对)。
    meta = {
        "source": seg.source,
        "audio_offset": seg.start,
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


def _build_engine(cfg: AsrConfig, cache_dir: str):
    """用解析好的完整 AsrConfig 构建 faster-whisper 引擎。子进程内执行,真模型。

    compute_type 由 cfg 决定(STEP 3):默认 int8_float32(CPU 精度优于纯 int8),可切 int8/float32。
    """
    from faster_whisper import WhisperModel

    from epictrace.asr.engine import FasterWhisperEngine

    whisper = WhisperModel(cfg.model, download_root=cache_dir, compute_type=cfg.compute_type)
    return FasterWhisperEngine(whisper, cfg)


def _wav_path(staging_dir: str, channel: str) -> str:
    """每次拉起用唯一文件名 audio-{channel}-{秒级时间戳}.wav,避免 pause(=停+重启)覆盖
    暂停前的音频(FIX F)。OrganizeService 按 audio-*.wav glob,所有分段文件都会入库。"""
    return f"{staging_dir}/audio-{channel}-{int(time.time())}.wav"


def _shutdown(sources: dict, wavs: dict) -> None:
    """优雅收尾(可单测):停所有源、关所有 wav 文件句柄。各步独立兜底,一处失败不漏其余。"""
    for s in sources.values():
        try:
            s.stop()
        except Exception as e:  # noqa: BLE001
            _log.warning("ASR source stop failed: %s", e)
    for w in wavs.values():
        try:
            w.close()
        except Exception as e:  # noqa: BLE001
            _log.warning("ASR wav close failed: %s", e)


def main(argv: list[str] | None = None) -> int:
    import sys
    from pathlib import Path

    from epictrace.asr.provisioner import detect_asr_model
    from epictrace.config import AppConfig

    args = parse_args(argv if argv is not None else sys.argv[1:])
    cfg = args.config
    app_config = AppConfig()
    # 缓存目录优先用路由透传的 --cache-dir(与 provisioner 就绪检测同一路径,FIX 2);
    # 缺省回退到本进程的 AppConfig.asr_model_dir(默认数据目录场景)。
    cache_dir = Path(args.cache_dir) if args.cache_dir else app_config.asr_model_dir
    # fail-fast(FIX 1 防御纵深):server 端已门控,但 worker 仍自检模型真在缓存里——
    # 缺则报错退出,绝不让 WhisperModel 自动去下载 ~3GB 阻塞(用户最怕的静默卡死)。
    # 这步在加载 soundfile/audio_sources 等重依赖之前,缺模型时连音频库都不碰。
    if not detect_asr_model(cache_dir, cfg.model):
        _log.error("ASR worker: model %s not present in %s, exiting (no auto-download)",
                   cfg.model, cache_dir)
        _post(f"/api/capture/sessions/{args.session_id}/events",
              {"kind": "note", "payload": "语音模型未就绪,本次未启动转录", "meta": {"asr_error": True}})
        return 1

    import soundfile as sf

    from epictrace.asr.audio_sources import (
        SAMPLE_RATE,
        MicSource,
        SystemAudioSource,
    )
    from epictrace.asr.loop import StreamLoop

    cache_dir.mkdir(parents=True, exist_ok=True)

    # SIGTERM(supervisor stop 发出)→ 置停止标志,主循环检测后退出,finally 兜底 flush + 关 wav。
    stop_flag = {"stop": False}

    def _on_sigterm(_signum, _frame):  # noqa: ANN001
        stop_flag["stop"] = True

    signal.signal(signal.SIGTERM, _on_sigterm)

    # FIX E:源启动、wav 打开、模型加载、主循环全部包进同一个 try/finally,任何一步抛错都走
    # 统一收尾 _shutdown(sources, wavs)——绝不因 wav 打开或模型加载失败而泄漏已起的源/wav 句柄。
    # loop 在模型加载后才建,此前为 None;收尾里用到 loop 处先判空。
    sources: dict[str, object] = {}
    wavs: dict[str, object] = {}
    loop = None
    # active:当前活跃(应被读取/转写/落 wav)的通道集;muted:上次已知静音源 id(FIX A:
    # poll 失败回 None 时保留它,不误恢复)。两者在 finally 收尾(跳过静音通道)前就需可见,故前置。
    active: set[str] = set()
    muted: list[str] = []
    written: dict[str, int] = {}
    try:
        # 冷启动(STEP 2):先起音源 + 开 wav,让 RingBuffer 从 session 打开那一刻就开始攒 PCM、
        # wav 立刻开录;模型加载(可能数秒)期间的说话不再丢失。RingBuffer/base_offset 保住绝对
        # 时间,配合 STEP 1 的有界滑窗,首批 tick 会在窗口内追上开局攒下的 backlog。
        # 起选中音源。worker 内部用 mic/device 二元通道命名(system_audio → device)。
        for src in args.sources:
            channel = _SOURCE_TO_CHANNEL.get(src)
            if channel is None:
                continue
            if src == "mic":
                # 用户在设置里选的输入设备索引(None = 系统默认);Feature A 让弱/错默认麦克风可换。
                s = MicSource(device=cfg.input_device, rms_normalize_enabled=cfg.rms_normalize)
            else:
                # 系统内录 helper 二进制路径(随 app 构建到 data_dir/bin;Task 12)。
                helper = str(app_config.data_dir / "bin" / "epictrace-sysaudio")
                s = SystemAudioSource(helper, rms_normalize_enabled=cfg.rms_normalize)
            try:
                s.start()
            except Exception as e:  # noqa: BLE001 — 某路起不来不拖垮其余源
                _log.error("ASR source %s failed to start: %s", src, e)
                continue
            sources[channel] = s
            # 原始音频边录边追加落唯一文件名 wav(16k mono),供回放/重转写(pause 不覆盖,FIX F)。
            wavs[channel] = sf.SoundFile(_wav_path(args.staging_dir, channel), mode="w",
                                         samplerate=SAMPLE_RATE, channels=1, subtype="FLOAT")

        if not sources:
            _log.error("ASR worker: no audio source started, exiting")
            return 1

        # 启动诊断:采集已起、PCM 正在攒 —— 在(可能耗时的)模型加载之前就让真机终端确认。
        print(f"[EpicTrace ASR] worker 启动(采集已起,加载模型中): session={args.session_id} "
              f"sources={list(sources.keys())} model={cfg.model}", flush=True)

        # 采集已 live,RingBuffer 开始攒 PCM;现在才加载模型 + 建 StreamLoop(STEP 2)。
        # 若 _build_engine 在此抛错,外层 finally 仍会停源 + 关 wav(FIX E)。
        engine = _build_engine(cfg, str(cache_dir))
        loop = StreamLoop(
            engine, cfg,
            on_confirmed=lambda seg: _post_confirmed(args.session_id, seg),
            on_partial=lambda seg: _post_partial(args.session_id, seg),
        )
        # 当前活跃通道集(软静音逻辑维护):初始全部已起的通道。源对象始终存活,
        # 静音只是把对应通道从「被读取/转写/落 wav」中剔除(loop.set_sources 仅喂活跃源)。
        active = set(sources.keys())
        loop.set_sources(dict(sources))

        written = {ch: 0 for ch in sources}
        last_diag = time.time()
        last_mute_poll = time.time()
        # 短尾排空(FIX 3):某路 available_seconds 停止增长 ~1.5s = 说话停顿/IDLE;此时若仍有
        # 未被正常 tick(≥1s 门)处理的短尾/partial,调一次 flush_channel 把它确认掉。
        # 每路记上次 available + 该值上次变化的时刻;只在「转 IDLE」那一刻 flush 一次(下方
        # flushed_idle 防同一段静默里反复 flush)。
        last_available = {ch: 0.0 for ch in sources}
        last_growth_at = {ch: time.time() for ch in sources}
        flushed_idle = {ch: False for ch in sources}
        while not stop_flag["stop"]:
            # 周期性轮询软静音集(比 0.5s tick 慢):重算活跃通道,变化才更新 loop 的源集。
            # 软静音的通道不被读取/落 wav(下方按 active 过滤),且不喂 loop(不被转写)。
            now = time.time()
            if now - last_mute_poll >= _MUTE_POLL_INTERVAL:
                last_mute_poll = now
                # FIX A:poll 失败回 None → 保留上次已知静音集(网络抖动不应误恢复全部)。
                fetched = _fetch_muted(args.session_id)
                if fetched is not None:
                    muted = fetched
                next_active = active_channels(set(sources.keys()), muted)
                if next_active != active:
                    # FIX A:对「转静音」的通道推进 wav 写游标 + ASR 游标,使静音区间既不回填进
                    # wav 也不在 unmute 后被重转(光从 loop 源集剔除不够,源仍在攒)。
                    apply_mute_transition(active, next_active, sources, written, loop)
                    active = next_active
                    loop.set_sources({ch: sources[ch] for ch in active})
            for channel, s in sources.items():
                if channel not in active:
                    continue  # 软静音:不读、不落 wav
                pcm = s.read()
                # 增量把新样本写入 wav(read 返回全量,只写未写过的尾巴)。
                if pcm.shape[0] > written[channel]:
                    wavs[channel].write(pcm[written[channel]:])
                    written[channel] = pcm.shape[0]
            loop.tick()
            # 短尾排空:逐活跃路看 available_seconds 是否还在长。长 → 复位 IDLE 标志;
            # 停长 ≥_IDLE_FLUSH_SECS 且本段静默尚未 flush → flush 一次(收掉短句+停顿的尾段)。
            now = time.time()
            for channel, s in sources.items():
                if channel not in active:
                    continue
                avail = s.available_seconds()
                if avail > last_available[channel] + 1e-6:
                    last_available[channel] = avail
                    last_growth_at[channel] = now
                    flushed_idle[channel] = False
                elif not flushed_idle[channel] and now - last_growth_at[channel] >= _IDLE_FLUSH_SECS:
                    flushed_idle[channel] = True
                    # 只 flush 这一路转 IDLE 的短尾(FIX D);绝不因它空闲就强制确认另一路
                    # mid-utterance 的 pending partial(此前 loop.flush() 全路 flush 的副作用)。
                    loop.flush_channel(channel)
            # 每 5s 打印每路采集时长 + 近段 RMS 能量:近零能量 = 没收到声音(权限/设备),
            # 而非转写问题——让「mic 寄」在终端一眼可诊断。
            now = time.time()
            if now - last_diag >= 5.0:
                last_diag = now
                for channel, s in sources.items():
                    if channel not in active:
                        print(f"[EpicTrace ASR] {channel}: 已软静音(不转写/不落 wav)", flush=True)
                        continue
                    buf = s.read()
                    if buf.size:
                        # 用 RAW(归一化前)输入电平诊断:读 ring buffer 拿的是归一化后值
                        # (恒 ~0.1),无法暴露弱麦;recent_input_rms() 反映真实输入电平(FIX 1)。
                        rms = s.recent_input_rms()
                        hint = " (近零能量 → 检查麦克风/录音权限或输入设备)" if rms < 1e-3 else ""
                        print(f"[EpicTrace ASR] {channel}: 已采 {buf.size / SAMPLE_RATE:.1f}s, "
                              f"近段输入 RMS={rms:.5f}{hint}", flush=True)
                    else:
                        print(f"[EpicTrace ASR] {channel}: 尚无音频(检查权限/设备)", flush=True)
            time.sleep(_TICK_INTERVAL)
    except KeyboardInterrupt:
        pass
    finally:
        # 收尾排空短尾(FIX 3):停止前最后一句若 <1s 未处理,正常 tick 永不会转 → 这里
        # loop.flush() 把每路残留短尾/partial 强制确认一次(on_confirmed 会 POST 回后端)。
        # loop 可能为 None(模型加载前就抛错,FIX E)→ 跳过 flush。
        if loop is not None:
            try:
                loop.flush()
            except Exception as e:  # noqa: BLE001 — 收尾 flush 失败不应拦截停源/关 wav
                _log.warning("ASR final loop flush failed: %s", e)
        # 退出前再 flush 一次每路未写尾巴,然后停源 + 关 wav(确保 wav 收尾完整)。
        # FIX A:跳过当前静音的通道——静音期间攒的音频不应在收尾时被回填进 wav。
        for channel, s in sources.items():
            if channel not in active:
                continue
            try:
                pcm = s.read()
                last = written.get(channel, 0)
                if pcm.shape[0] > last:
                    wavs[channel].write(pcm[last:])
                    written[channel] = pcm.shape[0]
            except Exception as e:  # noqa: BLE001
                _log.warning("ASR final wav flush failed: %s", e)
        _shutdown(sources, wavs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
