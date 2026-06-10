# ASR 流式转写:调参与幻觉过滤经验

> **来源**:汇总自一个**早期 Swift 流式转录原型**(基于 WhisperKit **0.17.0**,`https://github.com/argmaxinc/WhisperKit.git`)的代码与笔记。
>
> **给 EpicTrace 的话**:EpicTrace 的 ASR 用 **faster-whisper(Python)**,所以下面 WhisperKit 的 **API 细节不直接照搬**,但三类知识**高价值、可迁移**:
> 1. **§5 macOS 系统音频捕获**(Core Audio process tap / 聚合设备 / 采样率撒谎)——macOS 系统级知识,**与语言/框架无关,直接适用**。
> 2. **§3 幻觉过滤**(靠文本模式 + 重复检测,而非阈值)——概念直接迁移到 faster-whisper。
> 3. **§2 / §4 流式滑窗**(喂完整 buffer + 内部 seek + prefix 续写 + 段落确认)——概念映射到 faster-whisper 的流式做法。
>
> 实现采集层/转写层前先读这份,能省掉大量踩坑时间。

---

## 0. 必须先理解的背景

该原型不是「录一段 → 转一次」。它是**边录边转**:每隔约 1–2 秒就把当前音频缓冲区**重新喂给模型一次**,窗口不断往前滑。

因此下面几乎所有设计都在解决两个核心问题:
1. **重复转录同一段音频**(窗口滑动会反复处理重叠区);
2. **实时流幻觉**(Whisper 在静音/低能量时会胡乱脑补文本)。

理解这一点,参数和 filter 的取舍才说得通。

---

## 1. 依赖与版本

- **SPM 依赖**:`WhisperKit` @ `0.17.0`(pin: `26577ce…`)
- **核心使用文件**(早期原型):
  - `Services/TranscriptionService.swift` — 主流式转录(mic + system audio 交替)
  - `Services/SystemAudioCapture.swift` — 系统音频捕获(Core Audio process tap)
  - `Services/PTTService.swift` — 按键说话(push-to-talk)
- **并存的备选引擎**:`AppleSpeechMicTranscriber.swift` / `AppleSpeechDeviceTranscriber.swift`(Apple 原生 Speech 框架)
- 独立基准工具:`WhisperBench/`(单独依赖 WhisperKit,版本可能不同)

---

## 2. DecodingOptions 参数逐条解析

主转录路径配置见 `TranscriptionService.swift:1284`:

```swift
let options = DecodingOptions(
    task: .transcribe,
    language: autoLanguage ? nil : configuredLanguage,
    temperatureFallbackCount: 1,
    usePrefillPrompt: true,
    usePrefillCache: true,
    detectLanguage: autoLanguage,
    skipSpecialTokens: true,
    wordTimestamps: true,
    maxWindowSeek: Int(12.0 * sampleRate),
    clipTimestamps: [clipStartSeconds],
    windowClipTime: 2.0,
    prefixTokens: prefixTokens,
    suppressBlank: true,
    compressionRatioThreshold: experimentConfig.compressionRatioThresholdValue, // 默认 2.4
    logProbThreshold: experimentConfig.logProbThresholdValue,                   // 默认 -1.0
    firstTokenLogProbThreshold: experimentConfig.firstTokenLogProbThresholdValue,
    noSpeechThreshold: noSpeechThreshold(for: source)                            // 默认 0.6
)
```

阈值不是写死的——外面套了一层 `experimentConfig`,可做 A/B 调参。

### A. 任务与语言

| 参数 | 值 | 含义 | 为什么重要 |
|------|-----|------|-----------|
| `task` | `.transcribe` | 转写而非翻译(`.translate` 会强制输出英文) | 保证中文说中文,不被偷偷翻译 |
| `language` | 固定 or `nil` | 锁定语言 / 自动检测 | **指定语言能显著减少幻觉**;不确定语言时 Whisper 容易脑补 |
| `detectLanguage` | `autoLanguage` | 是否跑语言检测 | 与上一条配套,仅自动模式开启 |

### B. 流式滑窗核心(这个 app 能流式工作的关键)

| 参数 | 值 | 含义 | 为什么重要 |
|------|-----|------|-----------|
| `clipTimestamps` | `[clipStartSeconds]` | 让 WhisperKit **内部 seek 到该时间点**,跳过已确认部分 | ⭐ 整个流式方案的命脉。**绝不手动切 buffer**——见 §4 |
| `prefixTokens` | 动态 | 上一窗口结尾若干 token 作为本次解码前缀 | ⭐ 给模型「续写提示」,窗口边界衔接更顺、少重复 |
| `maxWindowSeek` | `12s × sampleRate` | 限制单窗口 seek 前进距离(约 12s) | 防止单次处理跑太远,控制延迟与漂移 |
| `windowClipTime` | `2.0` | clip 窗口切分节奏 | 决定每次实际处理多大一块新音频 |

### C. 解码加速与条件

| 参数 | 值 | 含义 | 为什么重要 |
|------|-----|------|-----------|
| `usePrefillPrompt` | `true` | 用 prompt token 预填充解码器起始状态 | 输出格式更可控、稳定 |
| `usePrefillCache` | `true` | 缓存复用 prefill 的 KV 计算 | **纯性能**——流式每秒都在解码,必须省算力才跟得上实时 |

### D. 质量控制 / Fallback 三阈值(最容易被误解的一组)

机制:Whisper 默认用 temperature=0 贪心解码;若结果「看起来坏了」,升高 temperature 重试。下面这些是判断「坏没坏」的触发器。

| 参数 | 值 | 含义 |
|------|-----|------|
| `temperatureFallbackCount` | `1` | 最多回退重试 1 次(流式要快,不能反复重试) |
| `compressionRatioThreshold` | `2.4` | 文本 gzip 压缩比 > 2.4 → 判定重复退化,触发回退 |
| `logProbThreshold` | `-1.0` | 整段平均对数概率 < -1.0 → 模型没把握,触发回退 |
| `firstTokenLogProbThreshold` | 可配 | 专看第一个 token 的置信度(开局错会带歪整句) |
| `noSpeechThreshold` | `0.6` | 无语音 token 概率 > 0.6 且置信度低 → 判定静音跳过 |

> ⚠️ **关键认知**:这三个阈值是「触发重试的开关」,**不是「拒绝结果的过滤器」**。
> 真实语音(讲课、安静环境、重复性内容)经常命中这些值。代码**绝不**拿它们丢弃段落——
> 丢弃会真的丢内容,而且若 `lastConfirmedEnd` 推过了被丢的段,那段音频**永久丢失**。
> 真正的幻觉过滤靠 §3 的文本模式匹配 + 重复检测,与这些阈值是两套独立机制。
> 这就是为什么阈值基本用 Whisper 默认值。

### E. 输出清洗

| 参数 | 值 | 含义 | 为什么重要 |
|------|-----|------|-----------|
| `skipSpecialTokens` | `true` | 去掉 `<\|startoftranscript\|>` 等控制 token | **WhisperKit 默认是 `false`**,不设会把原始标记塞进 `segment.text`——经典坑 |
| `suppressBlank` | `true` | 抑制开头空白/空格 token | 每段文本开头干净 |
| `wordTimestamps` | `true` | 经 cross-attention(DTW)算每个词的精确时间戳 | ⭐ 词级 confirmation 策略的基础(见 §3.6) |

### F. 两条路径对比

| | TranscriptionService(流式) | PTTService(按键说话,`PTTService.swift:1590`) |
|---|---|---|
| 参数复杂度 | 全套(含 clip/prefix/wordTimestamps) | 精简:`skipSpecialTokens` / `suppressBlank` / `compressionRatioThreshold=2.4` / `logProbThreshold=-1.0` / `noSpeechThreshold=0.6` |
| 原因 | 需要滑窗 + 词级确认 | 一次性短录音,不需要滑窗逻辑 |

---

## 3. 自研 Filters(WhisperKit 不带,全是 app 在结果之上额外加的)

### 3.1 静音 / 幻觉精确匹配 — `isSilenceOrNoise()`(`TranscriptionService.swift:189`)

硬编码的幻觉串表(`silencePatterns`):

- **静音标记**:`[silence]` `(silence)` `[blank_audio]` `[no speech]` `[inaudible]` `(inaudible)`
- **标注幻觉**:`(indistinct)` `(murmuring)` `(speaking chinese)` `(speaking foreign language)` …
- **英文近静音幻觉**:`thank you (for watching)` / `thanks (for watching)` / `bye` / `goodbye` / `you` / `okay` / `hmm` / `um` / `uh` / `yeah` / `right` …
- **中文近静音幻觉**:`谢谢观看` `谢谢大家` `谢谢收看` `感谢观看` `感谢收看` `感谢大家`
  - 注释强调:这些幻觉报告 `noSpeechProb=0.000` 且 logProb 正常,**所以只能靠文本匹配抓**,阈值过滤不掉。
- 动态规则:`(speaking …)` 任意变体(前缀 `(speaking` + 后缀 `)`)一律命中。

### 3.2 中文子串幻觉 — `chineseHallucinationSubstrings`(substring 匹配)

出现在更长幻觉文本里(如「请不吝点赞 订阅 转发 打赏支持…」),用**子串**而非精确匹配:
`请不吝点赞` `点赞订阅` `订阅转发` `打赏支持` `请订阅` `请点赞` `请转发` `字幕由` `字幕提供` `明镜与点点栏目` …

### 3.3 重复抑制

- 保留最近 **5 条** confirmed 文本(`recentConfirmed` / `recentConfirmedMax = 5`)做去重(`isDuplicateConfirmedText`)。
- 目的:抓那种**每次略有变化**的幻觉循环——单纯精确去重抓不到。

### 3.4 幻觉循环检测

- 连续 **≥3 次**相同 hypothesis(`consecutiveRepeatedHypotheses >= 3`)→ 触发「repeated tail recovery」恢复逻辑。
- `hypothesisSignature()` 把文本归一化为词 token 序列再比对。

### 3.5 语言过滤与清洗

- `shouldDropForLanguage()`:英文模式下丢掉**不含任何 ASCII 字母**的段(抑制跨语言幻觉),但可配置保留含数字的段。
- `sanitizeSegmentText()`:正则去掉开头的标点/符号 `^[\p{P}\p{S}\s]+`(可被 `preserveLeadingSymbolsEnabled` 关闭)。
- `compressionRatio > 3.0 && wordCount >= 6` 作为额外的重复段判定(注意:这是 **3.0**,比 fallback 的 2.4 更保守,仅用于判定而非盲目丢弃)。

### 3.6 段落确认策略(Segment Confirmation)

Whisper 的段落会随更多音频到来而变化。策略:
```swift
if segments.count > 1 {
    let confirmed = segments.dropLast(1)  // 旧段落 → 确认并 emit
    let unconfirmed = segments.suffix(1)  // 最新段落 → 显示为「暂定/partial」
}
```
配合 `wordTimestamps`,实现「最近 N 个词暂定、更早的词确认」的流式 UI,减少闪烁与重复。

---

## 4. 关键教训汇总(DO / DON'T)

### ❌ 不要用 `AudioStreamTranscriber` 做生产流式
它的 `realtimeLoop()` 在捕获**任何**错误(包括 `Task.sleep` 的瞬时 `CancellationError`)时都会 `break`,会静默杀死整个转录会话。
**改为手动控制管线**:
1. `whisperKit.audioProcessor.startRecordingLive(inputDeviceID:)` 指定设备启动采集
2. 自己轮询 `audioProcessor.audioSamples`
3. 对音频窗口调 `whisperKit.transcribe(audioArray:decodeOptions:)`
4. 错误优雅处理(记录并继续,仅 `CancellationError` 才 break)

### ❌ 不要手动切音频 buffer,✅ 用 `clipTimestamps`
手动 `Array(currentBuffer[startSample...])` 会导致:
- 幻觉时间戳把 `lastConfirmedEnd` 推过 buffer → `Range requires lowerBound <= upperBound` 崩溃
- 丢失解码器的前文条件
- 快速说话时漏 chunk
**改为**:喂**完整 buffer** + `clipTimestamps: [lastConfirmedEnd]`,WhisperKit 内部 seek、处理 30s 窗口、返回**绝对时间戳**的段落(无需手动调整)。这正是 `AudioStreamTranscriber` 内部 `transcribeAudioSamples()` 的做法。

### ⚠️ `skipSpecialTokens` 默认 `false`
不设会得到:`<|startoftranscript|><|en|><|transcribe|><|0.00|> Hello…`。**永远显式设 `true`**。

### ❌ 不要用 `compressionRatio`/`noSpeechProb` 在 fallback 之后过滤段落
它们是 **fallback 触发器**,不是拒绝阈值。真实语音常命中。过滤会丢内容;若 `lastConfirmedEnd` 推过被丢段,音频永久丢失。**改为**靠 §3 的文本模式匹配 + 重复滑窗(最近 5 条)+ 「连续 3+ 重复 → 退出 batch」的循环检测。

### 🎤 音频设备选择很关键(macOS)
WhisperKit 默认用系统默认输入设备。若用户装了虚拟音频驱动(BlackHole 等),默认设备可能产生静音/噪声。
**务必**:① Core Audio 枚举设备;② init 时默认 `kAudioHardwarePropertyDefaultInputDevice`;③ 把选中的 `AudioDeviceID` 传给 `startRecordingLive(inputDeviceID:)`。
- macOS 上 `DeviceID` 是 `AudioDeviceID`(UInt32),iOS 上是 `String`。

### 🐕 `startRecordingLive` 可能「假成功」→ 需 watchdog
失败模式:日志显示 `Audio capture started` 并进入转录循环,但 `audioSamples` 永不增长(Core Audio 报 `no device with given ID` / `!dev`,IO unit 绑到了失效设备对象)。
**修复**:把「采集已启动但样本数从不增长」当作启动失败。跑一个**麦克风启动 watchdog**:几秒内无新样本 → 停掉再重启一次 → 仍然死则抛真实错误。等 `Transcribing from …` 日志太晚了。

### ✅ `HALC_ProxyIOContext` / `-10877` 错误非致命
来自虚拟音频驱动的 `kAudioDevicePropertyIOProcStreamUsage` 警告,采集仍正常工作。**不要**因此 break 转录循环。

### 📦 WhisperKit 0.15.x 版本注意(历史经验)
- `WhisperKitConfig`:用 `config.load = false`、`config.download = true`,再单独调 `kit.loadModels()` 以正确校验加载
- 就绪判定:`kit.modelState == .loaded && kit.tokenizer != nil`
- `TranscriptionResult.segments` 内含 `TranscriptionSegment`,有 `.text` `.start` `.end` `.avgLogprob`

---

## 5. 系统音频捕获(macOS,与转写引擎配合)— **直接可迁移到 Python 实现**

设备/系统音频用 `AudioHardwareCreateProcessTap`(macOS 14.2+)捕获,再喂给转写引擎。流程要点:
1. 建 `CATapDescription`(stereo 全局 tap,排除自身进程,`.unmuted` 让声音仍从扬声器播放,`.isPrivate = true`)
2. `AudioHardwareCreateProcessTap()` → tap `AudioObjectID`
3. **读聚合设备的 `kAudioDevicePropertyNominalSampleRate`**(tap ASBD 会撒谎,见下)
4. 建私有聚合设备,`kAudioAggregateDeviceTapListKey` 含 tap UUID
5. `AudioDeviceCreateIOProcIDWithBlock()` → IO 回调拷贝原始样本到处理队列
6. `AVAudioConverter` 从源格式重采样到 **16kHz mono Float32**
7. 累积到 `audioSamples`(与转写引擎 `AudioProcessor` 同模式)

### 关键坑
- **`CATapDescription` 命名**:Swift 初始化器用 `NS_REFINED_FOR_SWIFT`,直接收 `[AudioObjectID]`(非 `[NSNumber]`);`uuid` 是 `UUID`(非 `NSUUID`)。
- **必须用聚合设备**:不能直接从 tap 读音频。
- **静默权限拒绝**:无公开 API 查系统音频录制权限。被拒时 tap 产生静音且无报错。启发式检测:采集 10s 后近零能量 → `permissionDenied = true`。
- **清理顺序**:停设备 → 销毁 IO proc → 销毁聚合设备 → 销毁 process tap。
- **排除自身进程**:把 `processIdentifier`(pid_t) 经 `kAudioHardwarePropertyProcessObjectList` + `kAudioProcessPropertyPID` 翻译成 `AudioObjectID`。
- **`kAudioAggregateDeviceTapAutoStartKey = false`**:用于常开采集;`true` 时 `AudioDeviceStart` 会阻塞直到被 tap 进程出声。
- **🔴 `kAudioTapPropertyFormat` 在采样率上撒谎**:tap ASBD 可能报 48kHz,而聚合设备实际跑在输出设备的标称率(如内置扬声器 96kHz)。IO proc 按**聚合设备**的率交付样本。**务必读聚合设备的 `kAudioDevicePropertyNominalSampleRate`** 做重采样。用 tap 的率会以一半比率重采样 → 半速、变调、Whisper 听不懂。仅当输出设备率 ≠ 48kHz 时才暴露(AirPods 48kHz 没事,内置扬声器 96kHz 中招)。

### 交替双流转录
单个转写模型在 mic buffer 与 system audio buffer 间**逐轮交替**。循环检查两路、选未处理音频更多的一路(>1s 阈值),各自维护 `StreamState` 跑词级对齐逻辑。每路延迟约 3–6s,对「上下文捕获层」可接受。mic 设备切换时设备音频不中断,仅转录循环重启。

参考实现:[AudioCap](https://github.com/insidegui/AudioCap)、[audiotee](https://github.com/makeusabrew/audiotee)。

---

## 6. 速查清单(Cheat Sheet)

**永远要做:**
- [ ] `skipSpecialTokens: true`(默认 false!)
- [ ] 喂完整 buffer + `clipTimestamps`,不手动切
- [ ] 指定输入设备 `AudioDeviceID`
- [ ] 跑麦克风启动 watchdog
- [ ] 系统音频读**聚合设备**的标称采样率
- [ ] 重采样到 16kHz mono Float32
- [ ] 幻觉过滤靠文本模式 + 重复检测

**永远不要做:**
- [ ] 用 `AudioStreamTranscriber` 做生产流式
- [ ] 手动切音频 buffer
- [ ] 用 `compressionRatio`/`noSpeechProb` 阈值丢弃段落
- [ ] 因 `HALC_ProxyIOContext` / `-10877` break 循环
- [ ] 用 tap ASBD 的采样率做重采样

---

## 7. 迁移到 faster-whisper(Python)时的对应关系

| 早期 Swift/WhisperKit 概念 | faster-whisper / Python 对应 |
|---|---|
| `DecodingOptions` 阈值 | `transcribe()` 的 `compression_ratio_threshold` / `log_prob_threshold` / `no_speech_threshold` / `temperature` 列表 |
| `clipTimestamps` 内部 seek | faster-whisper 用 `clip_timestamps` 或自管窗口偏移;流式需自己维护 `lastConfirmedEnd` |
| `prefixTokens` 续写 | `initial_prompt` / `prefix` 参数 |
| `wordTimestamps` | `word_timestamps=True`(返回 word 级 `start`/`end`) |
| 语言锁定减幻觉 | `language="zh"`(别用 auto) |
| §3 自研幻觉过滤 | **照搬概念**:文本模式表 + 最近 N 条去重 + 连续重复检测,在 faster-whisper 输出之上自己实现 |
| `noSpeechProb` 不当过滤阈值 | 同样别用 `no_speech_prob` 丢段;用 VAD(`vad_filter=True`)+ §3 文本过滤 |
| §5 Core Audio tap | **macOS 系统级,直接迁移**;Python 侧可用 soundcard / 自写 PyObjC 调 Core Audio,坑完全一致 |
