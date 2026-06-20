"""ASR worker 子进程入口(`python -m epictrace.asr.worker`)。

后端 AsrSupervisor 在 session 选了 mic/system_audio 源时 Popen 拉起本进程。**架构(2026-06-18):
纯录音器**——`_LIVE_TRANSCRIPTION=False` 时本进程只起选中音源(MicSource / SystemAudioSource)、把
PCM 追加落 staging/audio-{source}-{ts}.wav,**不加载模型、不做实时转写**;转写全交给停录后的一次性
重转(retranscribe,mlx 完整 large-v3)。下方 StreamLoop / 实时转写那套代码保留但仅在
_LIVE_TRANSCRIPTION=True(休眠的 live 模式)走;supervisor 传的 --model/--cache-dir 也仅 live 模式用。

**动态音源 + 模型生命周期**(2026-06-17 增强):音源不再固定在启动时。worker 周期性轮询后端
「期望开启的音源集」(asr-source),reconcile 出本轮要启动 / 要停止的通道:启用某源 = 开麦 /
起 helper + 开新 wav(中途也能开开始没勾的源);关闭某源 = 停采集 + 关其 wav。**所有源都关且
持续 _IDLE_UNLOAD_SECS 秒 → worker 自行退出**,模型随进程释放(再开由 supervisor 重启 worker,
开局 backlog 由冷启动 catch-up 补转)。这把「中途开源」「关源真停采集」「全关久了省内存」统一。

重依赖(soundfile / audio_sources / mlx_whisper / faster-whisper)都在 **函数体内懒导入**,使本模块
能被安全 import 而不拖入音频/模型库——测试只 import 同包的纯逻辑模块。argv 解析抽成可单测的纯函数。
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
_SOURCE_POLL_INTERVAL = 1.0  # 秒:轮询后端「期望开启音源集」的间隔(比 0.5s tick 慢,避免每轮打网络)
_IDLE_FLUSH_SECS = 0.9  # 秒:某路 available_seconds 停止增长这么久 = 停顿/IDLE → flush 一次短尾(FIX 3)
# (1.5→0.9:孤立短句要等这么久才在自然停顿处被收掉;缩短提高短句收取率。flush 走同一确认门,
#  幻觉/重复仍被挡;额外分段经前端 joinSegments 无缝拼接,显示上不可见。)
# 秒:全部音源关闭且持续这么久 → worker 自退,模型随进程释放(用户要求 1 分钟)。短暂关再开时
# worker 仍在跑、模型温存(秒恢复);只有久关才真正释放内存。
_IDLE_UNLOAD_SECS = 60.0


def desired_channels(enabled_sources: list[str]) -> set[str]:
    """前端期望开启的源 id 列表(mic/system_audio)→ worker 通道集(mic/device)。

    纯函数(可单测):未知源 id 忽略。worker 据此 reconcile 出要启动/停止的通道。
    """
    return {_SOURCE_TO_CHANNEL[s] for s in enabled_sources if s in _SOURCE_TO_CHANNEL}


def reconcile_channels(desired: set[str], started: set[str]) -> tuple[set[str], set[str]]:
    """据期望开启的通道集 vs 已启动的通道集,算出本轮 (要启动, 要停止) 的通道。纯函数(可单测)。"""
    return (desired - started, started - desired)


def idle_exit_due(idle_since: float | None, now: float, timeout: float) -> bool:
    """无任何已启动音源且持续 timeout 秒 → 该自退(模型随进程释放)。纯函数(可单测)。

    idle_since = 最近一次「变为无音源」的时刻;None 表示当前有音源(不计时)。"""
    return idle_since is not None and (now - idle_since) >= timeout


def _fetch_enabled(session_id: int) -> list[str] | None:
    """GET 后端某 session 的「期望开启音源集」;失败回 None。

    回 None(非 [])供调用方据此保留上次已知集合:网络抖动不应被误读成「空集 = 全部关闭」,
    否则会误停所有源、甚至触发空闲自退。"""
    url = f"{_BACKEND}/api/capture/sessions/{session_id}/asr-source"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        enabled = data.get("enabled", [])
        return list(enabled) if isinstance(enabled, list) else []
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        _log.warning("ASR worker GET asr-source failed: %s", e)
        return None


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
    --sources 是**初始**期望开启的源(中途可经控制通道增删)。
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


MLX_MODEL = "mlx-community/whisper-large-v3-mlx"  # mlx 模型(完整 large-v3)
# 实时预览开关(决策 2026-06-18:去掉 live 流式 + turbo,worker 退化成纯录音器,转写全交给停录后的
# 一次性重转 retranscribe)。False = 录制期间只录 wav、不建引擎、不转写(loop 保持 None,既不加载模型
# 也不烧 GPU,根除 live 静音幻觉)。流式那套代码(loop/word_channel/agreement/mlx 引擎)保留但不被
# 调用,便于日后需要再开。改 True 即恢复 live。
_LIVE_TRANSCRIPTION = False


def _mlx_ready(repo: str = MLX_MODEL) -> bool:
    """mlx-whisper 可用且模型已缓存:Apple Silicon + 包已装 + 权重在 HF 缓存(不触发自动下 1.5GB)。

    可被测试 monkeypatch。CTranslate2/faster-whisper 在 Mac 上无 Metal 后端只能 CPU(~1x 实时);
    mlx 跑 Apple GPU(~10x),故可用就优先 mlx 做 live 推理。"""
    import importlib.util
    import platform
    import sys as _sys
    from pathlib import Path

    if platform.machine() != "arm64" or _sys.platform != "darwin":
        return False
    if importlib.util.find_spec("mlx_whisper") is None:
        return False
    flat = "models--" + repo.replace("/", "--")
    snaps = Path.home() / ".cache" / "huggingface" / "hub" / flat / "snapshots"
    try:
        return snaps.is_dir() and any(snaps.iterdir())
    except OSError:
        return False


def _build_engine(cfg: AsrConfig, cache_dir: str):
    """构建 live 转写引擎(**仅休眠的 live 模式 _LIVE_TRANSCRIPTION=True 才调用**;纯录音器不用)。
    优先 mlx-whisper(Apple GPU,完整 large-v3)——CTranslate2 在 Mac 无 Metal 后端只能 CPU;mlx 可用
    则跑 GPU。mlx 不可用(非 Apple Silicon/未装/模型未缓存)回退 faster-whisper(CPU)。子进程内执行,真模型。"""
    if _mlx_ready():
        from epictrace.asr.mlx_engine import MlxWhisperEngine

        _log.info("ASR live engine: mlx-whisper (Apple GPU) %s", MLX_MODEL)
        return MlxWhisperEngine(cfg, MLX_MODEL)

    from faster_whisper import WhisperModel

    # 预防退出期 `leaked semaphore` 告警(良性,OS 会回收):tqdm 在 WhisperModel/snapshot_download
    # 时会惰性创建一把 multiprocessing RLock(具名信号量),SIGTERM 停 worker 时可能被 resource_tracker
    # 报成 leaked。预置 mp_lock=None 让 tqdm 跳过创建(其 __init__ 会过滤 None lock)。
    # 注:真机那条告警更可能源自 faster-whisper 连带 import 的 torch(其退出清理的已知良性告警,
    # 无法干净规避);本预置只消除 tqdm 这一可能来源,零功能风险。
    try:
        from tqdm.std import TqdmDefaultWriteLock
        TqdmDefaultWriteLock.mp_lock = None
    except Exception:  # noqa: BLE001 — tqdm 内部结构变动则忽略(仅影响一条良性告警)
        pass

    from epictrace.asr.engine import FasterWhisperEngine

    _log.info("ASR live engine: faster-whisper (CPU) %s", cfg.model)
    whisper = WhisperModel(cfg.model, download_root=cache_dir, compute_type=cfg.compute_type)
    return FasterWhisperEngine(whisper, cfg)


def _wav_path(staging_dir: str, channel: str) -> str:
    """每次启动某通道用唯一文件名 audio-{channel}-{毫秒级时间戳}.wav,避免重启/再启用覆盖之前的
    音频(FIX F)。**毫秒**而非秒:快速 pause→resume(supervisor stop+restart)若落在同一秒会重名
    覆盖前一段(静默丢音);毫秒粒度杜绝。retranscribe.session_offsets 解析该毫秒戳算段在会话时间线
    的偏移(/1000 转秒)。OrganizeService 按 audio-*.wav glob,所有分段都入库。"""
    return f"{staging_dir}/audio-{channel}-{int(time.time() * 1000)}.wav"


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
    import os
    import sys
    from pathlib import Path

    # 关 HF tokenizers 的 rayon 并行(短文本逐窗 tokenize 无可感知收益),消除其退出期线程池
    # 相关的资源泄漏告警;须在 faster-whisper/tokenizers 被 import 之前设。
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    # 关 HF hub 的「Fetching N files」进度条:每次 transcribe 都打一行,纯噪声(模型已缓存、瞬时命中)。
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")

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
    # 纯录音模式(_LIVE_TRANSCRIPTION=False):录制不需要任何模型,跳过模型 fail-fast —— 转写在
    # 停录后的一次性重转里做,模型就绪由 API 门(start_session 检 mlx 完整 v3)把关。仅 live 模式才自检。
    if _LIVE_TRANSCRIPTION and not _mlx_ready() and not detect_asr_model(cache_dir, cfg.model):
        _log.error("ASR worker: no engine model present (mlx not ready & faster-whisper %s "
                   "absent in %s), exiting (no auto-download)", cfg.model, cache_dir)
        _post(f"/api/capture/sessions/{args.session_id}/events",
              {"kind": "note", "payload": "语音模型未就绪,本次未启动转录", "meta": {"asr_error": True}})
        return 1

    import soundfile as sf

    from epictrace.asr.audio_sources import (
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
    sources: dict[str, object] = {}      # 当前已启动的源(channel -> source)
    wavs: dict[str, object] = {}         # 各通道的 wav 句柄
    written: dict[str, int] = {}         # 各通道已写入 wav 的样本数(增量写尾巴)
    # 短尾 idle-flush 跟踪(逐通道):上次 available、其上次增长时刻、本段静默是否已 flush。
    last_available: dict[str, float] = {}
    last_growth_at: dict[str, float] = {}
    flushed_idle: dict[str, bool] = {}
    loop = None

    def _open_channel(ch: str) -> bool:
        """启动某通道:开麦/起 helper + 开新 wav + 注册到 loop(重置其游标)。

        成功 True;源起不来回 False(已记日志,不拖垮其余)。幂等:已起则直接 True。
        loop 尚未建时(冷启动:先起源后加载模型)跳过 loop 注册,待 loop 建好统一 set_sources。"""
        if ch in sources:
            return True
        # **录音永远 raw(rms_normalize_enabled=False)**:单遍架构下 wav 是事实来源(停录后一次性转写
        # 就转它)。逐帧 RMS 归一化会把安静的底噪/静音段也拉到 ~0.1 → 录出来全程等响、满是嘶嘶噪声
        # (真机实测:很大杂音、不像正常麦克风),且喂给 Whisper 更差。故事实来源永远忠实记录;
        # cfg.rms_normalize 仅休眠的 live 路径才可能用。诊断 recent_input_rms 仍取归一化前电平,不受影响。
        if ch == "mic":
            # 用户在设置里选的输入设备索引(None = 系统默认);Feature A 让弱/错默认麦克风可换。
            s = MicSource(device=cfg.input_device, rms_normalize_enabled=False)
        else:  # device → 系统内录 helper(随 app 构建到 data_dir/bin;Task 12)
            helper = str(app_config.data_dir / "bin" / "epictrace-sysaudio")
            s = SystemAudioSource(helper, rms_normalize_enabled=False)
        try:
            s.start()
        except Exception as e:  # noqa: BLE001 — 某路起不来不拖垮其余
            _log.error("ASR channel %s failed to start: %s", ch, e)
            return False
        sources[ch] = s
        # 原始音频边录边追加落唯一文件名 wav(48k mono float32,源自身的 sample_rate),供回放/重转写
        # (每次启用都是新分段,FIX F)。停录后一次性转写再降采样到 16k 喂 Whisper(见 retranscribe)。
        wavs[ch] = sf.SoundFile(_wav_path(args.staging_dir, ch), mode="w",
                                samplerate=s.sample_rate, channels=1, subtype="FLOAT")
        written[ch] = 0
        now = time.time()
        last_available[ch] = 0.0
        last_growth_at[ch] = now
        flushed_idle[ch] = False
        if loop is not None:
            # 新源带的是全新 ring buffer(base_offset 从 0 起)→ 重置该路游标对齐,再更新 loop 源集。
            loop.reset_channel(ch)
            loop.set_sources(dict(sources))
        return True

    def _close_channel(ch: str) -> None:
        """停某通道:flush 其转写短尾 + flush wav 尾 + 关 wav + 停源 + 从 loop/跟踪表移除。
        幂等(未起 = no-op)。"""
        if ch not in sources:
            return
        # 拆源前先把该路在飞的短尾/partial 转写确认掉(on_confirmed 会 POST):用户在句子说到
        # 一半点「关闭」时,不丢这最后几个字。flush_channel 在该路仍属 loop._sources 时调用。
        if loop is not None:
            try:
                loop.flush_channel(ch)
            except Exception as e:  # noqa: BLE001 — 收尾 flush 失败不应拦截停源/关 wav
                _log.warning("ASR channel %s flush on close failed: %s", ch, e)
        s = sources.pop(ch, None)
        if s is None:
            return
        w = wavs.pop(ch, None)
        if w is not None:
            try:
                pcm = s.read()
                last = written.get(ch, 0)
                if pcm.shape[0] > last:
                    w.write(pcm[last:])
            except Exception as e:  # noqa: BLE001
                _log.warning("ASR channel %s final wav flush failed: %s", ch, e)
            try:
                w.close()
            except Exception as e:  # noqa: BLE001
                _log.warning("ASR channel %s wav close failed: %s", ch, e)
        try:
            s.stop()
        except Exception as e:  # noqa: BLE001
            _log.warning("ASR channel %s stop failed: %s", ch, e)
        written.pop(ch, None)
        last_available.pop(ch, None)
        last_growth_at.pop(ch, None)
        flushed_idle.pop(ch, None)
        if loop is not None:
            loop.set_sources(dict(sources))

    try:
        # 冷启动(STEP 2):先起初始期望开启的源 + 开 wav,让 RingBuffer 从 session 打开那一刻就
        # 开始攒 PCM、wav 立刻开录;模型加载(可能数秒)期间的说话不再丢失。RingBuffer/base_offset
        # 保住绝对时间,配合 STEP 1 的有界滑窗,首批 tick 会在窗口内追上开局攒下的 backlog。
        for src_id in args.sources:
            ch = _SOURCE_TO_CHANNEL.get(src_id)
            if ch is not None:
                _open_channel(ch)

        if not sources:
            _log.error("ASR worker: no audio source started, exiting")
            return 1

        if _LIVE_TRANSCRIPTION:
            # 启动诊断:采集已起、PCM 正在攒 —— 在(可能耗时的)模型加载之前就让真机终端确认。
            _engine_label = f"mlx-whisper(Apple GPU) {MLX_MODEL}" if _mlx_ready() else f"faster-whisper(CPU) {cfg.model}"
            print(f"[EpicTrace ASR] worker 启动(采集已起,加载模型中): session={args.session_id} "
                  f"sources={list(sources.keys())} engine={_engine_label}", flush=True)
            # 采集已 live,RingBuffer 开始攒 PCM;现在才加载模型 + 建 StreamLoop。
            # 若 _build_engine 在此抛错,外层 finally 仍会停源 + 关 wav(FIX E)。
            engine = _build_engine(cfg, str(cache_dir))
            loop = StreamLoop(
                engine, cfg,
                on_confirmed=lambda seg: _post_confirmed(args.session_id, seg),
                on_partial=lambda seg: _post_partial(args.session_id, seg),
            )
            loop.set_sources(dict(sources))
        else:
            # 纯录音模式:不建引擎/不转写,只录 wav;转写交给停录后的一次性重转。
            print(f"[EpicTrace ASR] worker 启动(纯录音模式,停录后一次性转写): "
                  f"session={args.session_id} sources={list(sources.keys())}", flush=True)

        # 上次已知「期望开启集」(前端源 id);poll 失败回 None 时保留它,不误停所有源。
        enabled: list[str] = list(args.sources)
        # 已就 PERMISSION_DENIED 警告过的通道(只提示一次)。注意:helper 的 PERMISSION_DENIED 是
        # 「~10s 近零能量」启发式(系统音频录制无公开权限查询 API),**合法静音也会触发**,故绝不
        # 据此永久关源(会误杀「用户只是安静了一会」);静音不产生幻觉/不烧 GPU 由 loop 的能量门兜。
        perm_warned: set[str] = set()
        # idle_since = 最近一次「变为无音源」的时刻;有音源时为 None(不计空闲超时)。
        idle_since: float | None = None
        last_diag = time.time()
        last_poll = time.time()
        while not stop_flag["stop"]:
            now = time.time()
            # 周期性轮询期望开启集 → reconcile 增删音源(中途开/关任意源,含开始没勾的)。
            if now - last_poll >= _SOURCE_POLL_INTERVAL:
                last_poll = now
                fetched = _fetch_enabled(args.session_id)
                if fetched is not None:
                    enabled = fetched
                desired = desired_channels(enabled)
                to_start, to_stop = reconcile_channels(desired, set(sources.keys()))
                for ch in to_stop:
                    _close_channel(ch)
                for ch in to_start:
                    _open_channel(ch)
            # 权限被拒提示(helper 近零能量启发式置位):只警告、**不关源**(启发式会误判合法静音)。
            # 再加一道闸:仅当此刻该路**仍近零能量**才警告——若声音已经进来(如视频晚开几秒),说明权限
            # 没问题,纯属开头静音的误报,不打扰。真被拒时持续静音,既不幻觉也不烧 GPU(loop 能量门兜)。
            for channel, s in list(sources.items()):
                if (channel not in perm_warned and getattr(s, "permission_denied", False)
                        and getattr(s, "recent_input_rms", lambda: 0.0)() < 1e-3):
                    perm_warned.add(channel)
                    print(f"[EpicTrace ASR] {channel}: 系统内录约 10s 近零能量(可能未授权,也可能只是还没出声)。"
                          f"若确无声音:到 系统设置→隐私与安全性→屏幕录制 勾选本 app 后【重启 app】。", flush=True)
            # 读 + 增量落 wav(所有已起的源都是活跃的;关掉的已被 _close_channel 停掉移除)。
            for channel, s in list(sources.items()):
                pcm = s.read()
                if pcm.shape[0] > written[channel]:
                    wavs[channel].write(pcm[written[channel]:])
                    written[channel] = pcm.shape[0]
            # live 转写(纯录音模式 loop=None → 跳过):转一轮 + 短尾 idle-flush。
            if loop is not None:
                loop.tick()
                # 短尾排空(FIX 3):逐路看 available_seconds 是否还在长。长 → 复位 IDLE 标志;
                # 停长 ≥_IDLE_FLUSH_SECS 且本段静默尚未 flush → flush 一次(收掉短句+停顿的尾段)。
                now = time.time()
                for channel, s in list(sources.items()):
                    avail = s.available_seconds()
                    if avail > last_available[channel] + 1e-6:
                        last_available[channel] = avail
                        last_growth_at[channel] = now
                        flushed_idle[channel] = False
                    elif not flushed_idle[channel] and now - last_growth_at[channel] >= _IDLE_FLUSH_SECS:
                        flushed_idle[channel] = True
                        loop.flush_channel(channel)
            # 空闲自退:无任何已起音源且持续 _IDLE_UNLOAD_SECS → 退出循环(模型随进程释放)。
            # 短暂全关再开时 worker 仍在跑、模型温存(秒恢复);只有久关才真正退出省内存。
            now = time.time()
            if not sources:
                if idle_since is None:
                    idle_since = now
                elif idle_exit_due(idle_since, now, _IDLE_UNLOAD_SECS):
                    # 退出前做一次最终确认轮询,闭合「最后一刻重开某源」竞态:这一刻 is_running 可能
                    # 还返回 True 让后端不重启 worker,若此时正好有源被重新启用而 worker 却退了,
                    # 就会「开关亮着却无转写」。最终轮询发现 desired 非空 → 放弃退出(下一轮 poll 起源)。
                    final = _fetch_enabled(args.session_id)
                    if final is not None and desired_channels(final):
                        idle_since = None
                    else:
                        print(f"[EpicTrace ASR] 所有音源关闭超过 {int(_IDLE_UNLOAD_SECS)}s,worker 退出"
                              f"释放模型(session={args.session_id});再开将自动重启 worker", flush=True)
                        break
            else:
                idle_since = None
            # 每 5s 打印每路采集时长 + 近段 RMS 能量:近零能量 = 没收到声音(权限/设备),
            # 而非转写问题——让「mic 寄」在终端一眼可诊断。无音源时报模型温存中。
            now = time.time()
            if now - last_diag >= 5.0:
                last_diag = now
                if not sources:
                    print("[EpicTrace ASR] 无活跃音源(全部关闭,模型温存中)", flush=True)
                for channel, s in list(sources.items()):
                    buf = s.read()
                    if buf.size:
                        # 用 RAW(归一化前)输入电平诊断:读 ring buffer 拿的是归一化后值
                        # (恒 ~0.1),无法暴露弱麦;recent_input_rms() 反映真实输入电平(FIX 1)。
                        rms = s.recent_input_rms()
                        hint = " (近零能量 → 检查麦克风/录音权限或输入设备)" if rms < 1e-3 else ""
                        print(f"[EpicTrace ASR] {channel}: 已采 {buf.size / s.sample_rate:.1f}s, "
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
        # 退出前再 flush 一次每路未写尾巴(仍在 sources 的——被 _close_channel 关掉的已写完移除),
        # 然后停源 + 关 wav(确保 wav 收尾完整)。
        for channel, s in list(sources.items()):
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
