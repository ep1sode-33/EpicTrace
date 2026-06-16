# EpicTrace ASR 设计(Plan 9:麦克风 + 系统内录 流式转写)

> Plan 9。给 Plan 8 的采集骨架补上**音频转写**——「跳回那一刻」护城河的核心。本期一次做完 **mic 外录 + 系统内录** 两路流式 ASR(用户选择合并一个 plan):faster-whisper 流式转写 + 幻觉过滤 + 段落确认,transcript 段落带**词级时间戳**进 `capture_events`,HUD 时间线实时呈现 confirmed/partial。落实 Plan 8 留下的 `Transcriber` / `AudioSource` 接口缝,启用 HUD 里 disabled 的 🎤外录 / 🔊内录开关。
>
> 动手前必读:`docs/reference/asr-streaming-tuning-notes.md`(**本设计的蓝本**:§2 流式滑窗、§3 自研幻觉过滤、§4 DO/DON'T、§5 macOS 系统音频 Core Audio tap、§7 faster-whisper 映射)、`backend/epictrace/interfaces/transcriber.py` 与 `interfaces/audio.py`(Plan 8 的接口缝,本期落地)、`backend/epictrace/services/capture.py` + `models.py`(`capture_events`)、`backend/epictrace/media/mineru_provisioner.py`(子进程隔离 + 模型 provision 的样板)、`shell/run.py`(原生 + 子进程拉起)、`frontend/src/components/RecordingHud.tsx`(时间线实时呈现)。**前身 Swift 原型的 `SystemAudioCapture.swift` 是系统内录的移植源(本地参考,代号不入库)。**

## 1. 背景与目标

Plan 8 把会话/事件/暂存/时间线/HUD 打通了,但音频只占位(`AudioSource`/`Transcriber` 是空接口缝,HUD 的外录/内录开关 disabled)。本期落地两路流式 ASR:用户在会议/课堂/讲座时,mic(自己 + 环境)与系统音频(线上会议/视频)被实时转写成**带词级时间戳**的 transcript,作为 `capture_events` 进入 session,日后提问能**跳回那一刻**并回放原始音频。

**核心难点**(见 ASR 笔记):① 流式滑窗会重复转录重叠区;② Whisper 在静音/弱音/停顿处脑补幻觉(讲座场景尤甚)。两者都靠**段落确认 + 自研文本滤波 + 绝不拿阈值丢段**解决,不是靠魔法解码参数。

## 2. MVP 边界(本期)

**做**:
- **两路音源**:mic 外录(`sounddevice`)+ 系统内录(移植 Core Audio process tap 为原生 helper);统一重采样到 **16kHz mono Float32**。
- **流式转写**:faster-whisper(CTranslate2),单转写器在 mic/device 两路 buffer 间**逐轮交替**,各自 `StreamState`;滚动窗口 + `clip_timestamps` + `prefix` 续写 + `word_timestamps`。
- **段落确认 + 幻觉过滤**:confirmed 段(落库)/ partial 段(实时显示、不落库);移植 §3 滤波(静音/幻觉串表 + 最近 N 去重 + 连续重复检测)+ 确认纪律(`force_confirm_after` / `stall_seek` / 绝不拿阈值丢段);叠加 faster-whisper 自带 `vad_filter` / `hallucination_silence_threshold` / `repetition_penalty`。
- **进库**:confirmed 段 → `capture_events`(kind=`transcription`,meta 带 source + 词级时间戳 + 音频偏移);原始音频存 staging(`audio-mic.wav` / `audio-device.wav`),供回放跳转 + 日后重转写。
- **模型 provision**:`faster-whisper` 模型首次用到下载 + 状态机/进度(仿 MinerU provisioner);默认 `large-v3`(可配),Apple Silicon int8。
- **可调配置 + 离线评测**:阈值/确认参数做成可调(仿前身 `experimentConfig`);一个轻量离线评测脚本(喂样例音频 → 比对),为弱音/讲座调参留口(接 Langfuse 评测计划)。
- **HUD/UI**:启用 🎤外录 / 🔊内录开关;时间线 StreamView 式呈现(confirmed 固定 + 最后一段 partial,带 mic/device 来源标签)。
- **弱音处理**:喂模型前 RMS 归一化(可配)+ VAD 灵敏度可调。

**不做(延后)**:
- Windows/Linux 系统内录(WASAPI loopback / PipeWire)——本期仅 macOS 内录;mic 的 `sounddevice` 本就跨平台。
- 转写后的二次校对 UI、说话人分离(diarization)、翻译。
- PTT(按键说话)、Companion 配对等前身额外形态。
- 把 transcript 自动喂入 RAG 索引的特殊处理——transcript 作为 session 文本事件,归类入库时与笔记/剪贴板同路(Plan 8 的 OrganizeService 已覆盖文本事件)。

## 3. 进程模型

延续 MinerU 的「重活隔离到子进程」+ Plan 8 的「后端=数据权威 / shell=原生」:

```
[mic: sounddevice]──┐
                    ├─→ 16kHz mono PCM ─→ [ASR 子进程: faster-whisper 流式循环]
[系统内录: 原生 helper(Core Audio tap)]──┘                    │
                                                              ├─ confirmed 段 → POST 后端 /events (kind=transcription)
                                                              └─ partial 段 → POST 后端 /partial (内存态) → SSE → HUD
```

- **ASR 子进程(Python)**:`faster-whisper` 跑这里——**隔离避开 embedder/Milvus 的 macOS fork 段错误**([[macos-embedding-milvus-fork-order]]),且模型重、不该进 API 进程。后端在 session 选了 mic/system_audio 源时 `Popen` 拉起它(传 session_id + 选中源 + 模型配置),停止/暂停时停它。
- **mic 采集**:`sounddevice`(PortAudio)在 ASR 子进程内起一个输入流,16kHz mono。
- **系统内录采集**:**原生 helper 二进制**(移植前身的 Core Audio process tap 实现):建私有聚合设备 + process tap(排除自身、`.unmuted` 让声音照常外放)→ 读**聚合设备真实采样率**(tap ASBD 会撒谎,见笔记 §5)→ `AVAudioConverter` 重采样到 16kHz mono → 把裸 PCM 写 stdout。ASR 子进程 `Popen` 它、从 stdout 读 PCM。**用 Swift 编译成独立二进制**,随 app 分发(dev = 直接 `swiftc` 出的可执行)。
- **回事件**:子进程经 HTTP POST 回后端(`127.0.0.1:8765`),不绕前端(同 Plan 8 shell 的 `_post_event`)。confirmed → 持久事件;partial → 后端内存态 + SSE。

## 4. 音源 → 统一 PCM(单转写器交替)

- 两路各自累积到环形 buffer;ASR 循环每轮**选「未处理音频更多的一路」(>1s 阈值)**处理(笔记 §5 的交替双流),各自维护 `StreamState`。单模型交替:省算力,每路延迟 3–6s,对「上下文捕获层」可接受。
- **弱音**:喂模型前可选 RMS 归一化(把弱音抬到目标 dBFS),增益上限防噪声放大;VAD 灵敏度可调。
- **采集 watchdog**(笔记 §4):「采集已启动但样本数不增长」判为启动失败 → 停掉重启一次 → 仍死则报错(mic 的「假成功」坑)。

## 5. 流式 Transcriber(重定义接口缝)

Plan 8 的 `Transcriber.transcribe(audio_path)`(文件→一次性)**改为流式**。分解成三块(各自可测):

### 5.1 引擎封装 `FasterWhisperEngine`(实现 `Transcriber`)
单次滚动窗口转写:`transcribe_window(pcm, *, clip_start, prefix, language="zh") -> list[TranscriptSegment]`。faster-whisper 参数(映射自笔记 §2,见 §7 映射表):
```python
model.transcribe(
    pcm, language="zh", task="transcribe",
    clip_timestamps=f"{clip_start}",          # 流式命脉:内部 seek,绝不手动切 buffer
    initial_prompt=prefix or None,            # 上轮结尾续写,衔接顺、少重复
    word_timestamps=True,                     # 词级时间戳(护城河 + 段落确认基础)
    suppress_blank=True,
    temperature=[0.0, 0.2],                   # = temperatureFallbackCount 1(回退 1 次)
    compression_ratio_threshold=cfg.compression_ratio,  # 默认 2.4(回退触发器,不当过滤器)
    log_prob_threshold=cfg.log_prob,                    # 默认 -1.0
    no_speech_threshold=cfg.no_speech,                  # 默认 0.6
    vad_filter=cfg.vad,                       # Silero VAD:静音不喂模型 → 讲座停顿少幻觉
    vad_parameters={"threshold": cfg.vad_threshold},
    hallucination_silence_threshold=cfg.halluc_silence, # 跳过易幻觉静音期
    repetition_penalty=cfg.repetition_penalty,          # 解码层压重复
    no_repeat_ngram_size=cfg.no_repeat_ngram,
    condition_on_previous_text=cfg.condition_prev,      # 默认 False:防幻觉沿前文传染
)
```

### 5.2 段落确认 `StreamState`(每源一个)
- `last_confirmed_end`(下一窗 `clip_start`)、`recent_confirmed`(最近 5 条,去重)、`consecutive_repeats`、`rounds_since_progress`。
- 确认策略(笔记 §3.6):一窗返回多段时,**除最后一段外确认并 emit**,最后一段作 partial 显示。
- **确认纪律**:`force_confirm_after`(默认 4 轮没进展强制确认,防弱音卡死)、`stall_seek_seconds`(默认 0.8,卡住推进 seek)。**绝不拿 compression_ratio/no_speech 丢段**(笔记 §4):丢了若 `last_confirmed_end` 推过即永久丢音。

### 5.3 幻觉过滤 `HallucinationFilter`(引擎无关,纯文本)
移植 §3:静音/幻觉精确串表(中英近静音:`谢谢观看`/`请订阅`/`thank you for watching`/`you`…)+ 中文子串幻觉(`请不吝点赞`/`字幕由`…)+ 最近 N 去重(抓「每次略变」的幻觉循环)+ 连续 ≥3 相同 hypothesis → 退出本轮 + 段首标点清洗。**配置可开关/扩展串表**。

## 6. transcript → 事件 + 原始音频

- **confirmed 段 → `capture_events`**:新 kind `transcription`;`payload` = 文本;`meta` = `{source: "mic"|"device", start, end, words: [{w, s, e}...], audio_offset}`。词级时间戳 = 引用跳回精确时刻的根。
- **原始音频**:两路分别落 `staging_dir/audio-mic.wav` / `audio-device.wav`(16kHz mono),边录边追加;session 详情/暂存区可回放(回放 UI 可后续做,本期先存盘 + 记录路径)。归类入库时音频作为文件进 Project(同截图,日后可重转写)。
- **partial**:不落库;后端按 `session_id`+`source` 存最新 partial(内存),经 SSE 推 HUD。

## 7. faster-whisper 参数映射(自笔记 §2;确认可配)

| WhisperKit | faster-whisper | 备注 |
|---|---|---|
| task/language/detectLanguage | `task` / `language`(`"zh"` 或 None) | ✅ 锁中文减幻觉 |
| `clipTimestamps` | `clip_timestamps` | ✅ 流式命脉 |
| `prefixTokens` | `prefix` / `initial_prompt` | ✅ |
| `wordTimestamps` | `word_timestamps` | ✅ |
| `suppressBlank` | `suppress_blank` | ✅ |
| compression/logProb/noSpeech 阈值 | 同名 `*_threshold` | ✅ 同默认;仍是回退触发器,不当过滤器 |
| `temperatureFallbackCount:1` | `temperature=[0.0,0.2]` | ≈ |
| `maxWindowSeek`/`windowClipTime` | 无 | 由我们滚动 buffer 喂入节奏 + clip_timestamps 控 |
| `usePrefillPrompt/Cache`/`skipSpecialTokens` | 无 | 不需要(CTranslate2 内部 prefill;text 本就干净) |
| `firstTokenLogProbThreshold` | 无 | 无等效,影响小 |
| —(白赚) | `vad_filter`/`hallucination_silence_threshold`/`repetition_penalty`/`no_repeat_ngram_size`/`condition_on_previous_text` | 治流式幻觉的额外杠杆 |

## 8. 模型、依赖与 provision

- 新增依赖:`faster-whisper`(带 CTranslate2)、`sounddevice`、`soundfile`(写 wav)。
- **模型 provision**(仿 `mineru_provisioner` 状态机):`faster-whisper` 模型首次用到下载到 HF 缓存;设置里给状态/进度 + 模型大小选择(`large-v3` 默认 / `distil-large-v3` 速度 / `medium` / `small`)。Apple Silicon `compute_type="int8"`(或 `int8_float16`)。
- Swift helper:dev 用 `swiftc` 编译到 `<data_dir>` 或随仓库脚本构建;DMG 阶段打包进 app(后续)。

## 9. 配置(可调)+ 离线评测

- `settings.json` 增 `asr` 对象:`{model, language, vad, vad_threshold, no_speech, log_prob, compression_ratio, repetition_penalty, condition_prev, force_confirm_after, stall_seek_seconds, rms_normalize, halluc_filter_enabled}`,GET/PUT + 校验。默认值 = 笔记基线 + 弱音友好(vad on、condition_prev off、force_confirm 4)。
- **离线评测脚本**:`scripts/asr_eval.py`(或 tests 下 opt-in)——喂一组样例音频(含弱音/讲座/停顿),跑流式管线,输出 transcript + 命中的幻觉过滤,便于调参对比(不进 CI,手动 + 接 Langfuse)。

## 10. HUD / UI 实时呈现

- HUD 的 🎤外录 / 🔊内录**开关启用**(本期前两个真正生效;source 选中才拉起对应采集)。
- 时间线(`RecordingHud` 展开面板 + 采集视图)按 StreamView:transcript 段按时间排,**confirmed 固定 + 当前 partial 暂定态(淡显)**,带来源标签(mic / 内录)。SSE 推 confirmed 事件 + partial。
- transcript 与笔记/剪贴板/截图同在一条时间线(圆点按 source 上色)。

## 11. 权限与降级(macOS)

- **mic**:麦克风权限;首次触发引导;被拒该源标灰 + 提示。
- **系统内录**:无公开 API 查权限,被拒时 tap 产生静音无报错 → **启发式**:采集 ~10s 近零能量 → 判权限拒绝 + 提示去授权「屏幕录制」(笔记 §5)。
- **采样率撒谎**:务必读聚合设备 `kAudioDevicePropertyNominalSampleRate` 重采样(否则半速变调,Whisper 听不懂)。
- **设备热插拔**:mic 设备切换不中断系统音频,仅重启该路转录循环。
- ASR 子进程 / helper 崩溃 → 该源标错 + 可重启,不拖垮 session(其余源/事件照常)。
- opt-in 不变;模型未就绪 → 先下载(可见进度)再转写。

## 12. 错误处理与边界

- 子进程 `Popen` 失败 / helper 不可用(非 macOS)→ 该源降级 + 日志,session 仍可用其余源。
- `HALC_ProxyIOContext` / `-10877`(虚拟音频驱动告警)非致命,不 break 循环(笔记 §4)。
- 暂停:停子进程的喂入(monitors 暂停)+ 计时排除(Plan 8 已有 pause/resume 事件)。
- session 停止:停子进程 + helper,flush 最后的 confirmed 段,finalize 音频 wav。
- 转写落后:每路独立,落后只影响该路延迟,不阻塞事件流。

## 13. 测试策略

- **后端/管线 TDD(全 fake)**:`FakeEngine`(返回预设 segment 序列)驱动 `StreamState` → 测确认/partial 切分、`force_confirm_after`、never-drop;`HallucinationFilter` 单测(喂幻觉串→丢、真实语音→留、最近N去重、连续重复退出);transcript 段 → `capture_events`(kind=transcription、词级时间戳 meta);partial 内存态 + SSE;配置 GET/PUT 校验。注入假 PCM 源测交替选择逻辑。
- **真 faster-whisper / sounddevice / Swift helper / 权限**:opt-in real-model 测 + **真机手测**(弱音/讲座录音验证,配合离线评测脚本调参)。
- 前端 `npm run build`;HUD 实时呈现手测。

## 14. 明确不做(重申)

Win/Linux 系统内录、说话人分离、翻译、转写校对 UI、PTT/Companion、transcript 自动索引特化。
