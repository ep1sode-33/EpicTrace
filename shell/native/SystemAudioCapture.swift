// macOS 系统内录 helper:用 Core Audio process tap(macOS 14.2+)捕获全系统输出音频,
// 重采样到 16kHz mono Float32,把裸 PCM(小端 float32 帧)写到 stdout。
//
// 用法:作为独立命令行二进制运行(无参数),Python 侧 Popen 它、从 stdout 读 PCM。
//   - stdout = 裸 PCM 流(little-endian Float32,16000Hz,单声道)——与 SystemAudioSource 的契约一致。
//   - stderr = 诊断日志 + 权限启发式信号 `PERMISSION_DENIED`。
//
// 关键 macOS 坑(均已处理,详见各处注释):
//   - tap 的 ASBD 在采样率上「撒谎」:务必读聚合设备的真实标称采样率做重采样。
//   - 系统音频录制无公开权限查询 API:被拒时 tap 产生静音无报错 → ~10s 近零能量启发式。
//   - 清理顺序:停设备 → 销毁 IO proc → 销毁聚合设备 → 销毁 process tap。
//   - `HALC_ProxyIOContext` / `-10877` 是虚拟音频驱动的非致命警告,不退出。

import Accelerate
import AVFoundation
import CoreAudio
import Darwin
import Foundation

// MARK: - 全局诊断输出(只写 stderr,stdout 留给纯 PCM)

@inline(__always)
private func logErr(_ message: String) {
    FileHandle.standardError.write(Data((message + "\n").utf8))
}

// MARK: - 错误类型

enum CaptureError: Error, CustomStringConvertible {
    case tapCreationFailed(OSStatus)
    case noTapDescription
    case aggregateDeviceFailed(OSStatus)
    case noOutputDevice
    case formatReadFailed
    case ioProcFailed(OSStatus)
    case deviceStartFailed(OSStatus)

    var description: String {
        switch self {
        case .tapCreationFailed(let s): return "process tap creation failed (\(s))"
        case .noTapDescription: return "no tap description available"
        case .aggregateDeviceFailed(let s): return "aggregate device creation failed (\(s))"
        case .noOutputDevice: return "no system output device found"
        case .formatReadFailed: return "failed to read tap audio format"
        case .ioProcFailed(let s): return "IO proc creation failed (\(s))"
        case .deviceStartFailed(let s): return "audio device start failed (\(s))"
        }
    }
}

// MARK: - 输出常量(契约:与 Python SystemAudioSource 对齐)

private let kTargetSampleRate: Double = 16000  // 16kHz
private let kTargetChannels: UInt32 = 1         // mono
// 输出格式 = little-endian Float32 帧。Apple Silicon / Intel 均为小端,
// AVAudioPCMBuffer 的 floatChannelData 即为主机字节序的 Float32 → 直接写 stdout。

// MARK: - 系统音频捕获器

final class SystemAudioCapturer {
    private var tapID = AudioObjectID(kAudioObjectUnknown)
    private var aggregateDeviceID = AudioObjectID(kAudioObjectUnknown)
    private var ioProcID: AudioDeviceIOProcID?
    private var tapDescription: CATapDescription?

    private let processingQueue = DispatchQueue(label: "epictrace.sysaudio.io", qos: .userInitiated)

    // 重采样:手动 stereo→mono 后,AVAudioConverter 把源率→16kHz。
    private var audioConverter: AVAudioConverter?
    private var monoSourceFormat: AVAudioFormat?
    private var monoTargetFormat: AVAudioFormat?

    // 权限启发式:累计已输出样本数 + 近零能量监控(无锁原子访问)。
    private let energyLock = NSLock()
    private var emittedSampleCount: Int = 0
    private var accumulatedAbsEnergy: Double = 0

    private var ioCallbackCount = 0

    // stdout 写入(裸 PCM)。写入失败(下游关闭管道)→ 触发退出。
    private let stdout = FileHandle.standardOutput

    // MARK: 生命周期

    func start() throws {
        try createProcessTap()
        try createAggregateDevice()
        try startIOProc()
        logErr("[sysaudio] capture started")
    }

    /// 清理顺序:停设备 → 销毁 IO proc → 销毁聚合设备 → 销毁 process tap。
    func stop() {
        if let ioProcID, aggregateDeviceID != kAudioObjectUnknown {
            let stopErr = AudioDeviceStop(aggregateDeviceID, ioProcID)
            if stopErr != noErr { logErr("[sysaudio] AudioDeviceStop error: \(stopErr)") }
            let destroyErr = AudioDeviceDestroyIOProcID(aggregateDeviceID, ioProcID)
            if destroyErr != noErr { logErr("[sysaudio] AudioDeviceDestroyIOProcID error: \(destroyErr)") }
        }
        ioProcID = nil

        if aggregateDeviceID != kAudioObjectUnknown {
            let err = AudioHardwareDestroyAggregateDevice(aggregateDeviceID)
            if err != noErr { logErr("[sysaudio] AudioHardwareDestroyAggregateDevice error: \(err)") }
            aggregateDeviceID = AudioObjectID(kAudioObjectUnknown)
        }

        if tapID != kAudioObjectUnknown {
            let err = AudioHardwareDestroyProcessTap(tapID)
            if err != noErr { logErr("[sysaudio] AudioHardwareDestroyProcessTap error: \(err)") }
            tapID = AudioObjectID(kAudioObjectUnknown)
        }

        tapDescription = nil
        audioConverter = nil
    }

    // MARK: process tap

    private func createProcessTap() throws {
        // 全局 stereo tap,排除自身 pid 防自录回授。
        let selfPID = ProcessInfo.processInfo.processIdentifier
        let selfObjectIDs = lookupProcessObjectIDs(for: selfPID)

        let tap: CATapDescription
        if selfObjectIDs.isEmpty {
            tap = CATapDescription(stereoGlobalTapButExcludeProcesses: [])
        } else {
            tap = CATapDescription(stereoGlobalTapButExcludeProcesses: selfObjectIDs)
        }
        tap.uuid = UUID()
        tap.name = "EpicTrace System Audio"
        tap.muteBehavior = .unmuted  // 声音仍正常外放
        tap.isPrivate = true

        tapDescription = tap

        var outTapID = AudioObjectID(kAudioObjectUnknown)
        let err = AudioHardwareCreateProcessTap(tap, &outTapID)
        guard err == noErr else { throw CaptureError.tapCreationFailed(err) }
        tapID = outTapID
    }

    /// 把 pid 翻译成 Core Audio 的 process AudioObjectID(用于排除自身)。
    private func lookupProcessObjectIDs(for pid: pid_t) -> [AudioObjectID] {
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioHardwarePropertyProcessObjectList,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        var size: UInt32 = 0
        guard AudioObjectGetPropertyDataSize(
            AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size
        ) == noErr else { return [] }

        let count = Int(size) / MemoryLayout<AudioObjectID>.size
        guard count > 0 else { return [] }
        var processIDs = [AudioObjectID](repeating: 0, count: count)
        guard AudioObjectGetPropertyData(
            AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size, &processIDs
        ) == noErr else { return [] }

        return processIDs.filter { objectID in
            var procPID: pid_t = 0
            var pidAddress = AudioObjectPropertyAddress(
                mSelector: kAudioProcessPropertyPID,
                mScope: kAudioObjectPropertyScopeGlobal,
                mElement: kAudioObjectPropertyElementMain
            )
            var pidSize = UInt32(MemoryLayout<pid_t>.size)
            guard AudioObjectGetPropertyData(
                objectID, &pidAddress, 0, nil, &pidSize, &procPID
            ) == noErr else { return false }
            return procPID == pid
        }
    }

    // MARK: 聚合设备

    private func createAggregateDevice() throws {
        guard let tapDescription else { throw CaptureError.noTapDescription }
        let outputUID = try systemOutputDeviceUID()

        let aggDesc: [String: Any] = [
            kAudioAggregateDeviceNameKey: "EpicTrace-SystemCapture",
            kAudioAggregateDeviceUIDKey: UUID().uuidString,
            kAudioAggregateDeviceMainSubDeviceKey: outputUID,
            kAudioAggregateDeviceIsPrivateKey: true,
            kAudioAggregateDeviceIsStackedKey: false,
            // false:常开采集时 AudioDeviceStart 不阻塞等待 tap 进程出声。
            kAudioAggregateDeviceTapAutoStartKey: false,
            kAudioAggregateDeviceSubDeviceListKey: [
                [kAudioSubDeviceUIDKey: outputUID]
            ] as [[String: Any]],
            kAudioAggregateDeviceTapListKey: [
                [
                    kAudioSubTapUIDKey: tapDescription.uuid.uuidString,
                    kAudioSubTapDriftCompensationKey: true,
                ] as [String: Any]
            ] as [[String: Any]],
        ]

        var deviceID = AudioObjectID(kAudioObjectUnknown)
        let err = AudioHardwareCreateAggregateDevice(aggDesc as CFDictionary, &deviceID)
        guard err == noErr else { throw CaptureError.aggregateDeviceFailed(err) }
        aggregateDeviceID = deviceID
    }

    private func systemOutputDeviceUID() throws -> String {
        var deviceID = AudioDeviceID(0)
        var size = UInt32(MemoryLayout<AudioDeviceID>.size)
        var address = AudioObjectPropertyAddress(
            mSelector: kAudioHardwarePropertyDefaultSystemOutputDevice,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        let err = AudioObjectGetPropertyData(
            AudioObjectID(kAudioObjectSystemObject), &address, 0, nil, &size, &deviceID
        )
        guard err == noErr else { throw CaptureError.noOutputDevice }

        var uidAddress = AudioObjectPropertyAddress(
            mSelector: kAudioDevicePropertyDeviceUID,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        var uid: Unmanaged<CFString>?
        var uidSize = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
        let uidErr = AudioObjectGetPropertyData(
            deviceID, &uidAddress, 0, nil, &uidSize, &uid
        )
        guard uidErr == noErr, let uidValue = uid?.takeRetainedValue() else {
            throw CaptureError.noOutputDevice
        }
        return uidValue as String
    }

    // MARK: IO proc

    private func startIOProc() throws {
        // 读 tap 的 ASBD(取声道布局 / 交错性;采样率不可信,见下)。
        var formatAddress = AudioObjectPropertyAddress(
            mSelector: kAudioTapPropertyFormat,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        var asbd = AudioStreamBasicDescription()
        var formatSize = UInt32(MemoryLayout<AudioStreamBasicDescription>.stride)
        let formatErr = AudioObjectGetPropertyData(
            tapID, &formatAddress, 0, nil, &formatSize, &asbd
        )
        guard formatErr == noErr, asbd.mSampleRate > 0 else {
            throw CaptureError.formatReadFailed
        }

        let tapRate = asbd.mSampleRate
        let sourceChannels = Int(asbd.mChannelsPerFrame)
        let isNonInterleaved = (asbd.mFormatFlags & kAudioFormatFlagIsNonInterleaved) != 0

        // 🔴 tap ASBD 在采样率上撒谎:IO proc 实际按聚合设备的标称率交付样本。
        // 务必读聚合设备的 kAudioDevicePropertyNominalSampleRate,否则会以错误比率
        // 重采样(半速变调,Whisper 听不懂)。
        var nominalAddr = AudioObjectPropertyAddress(
            mSelector: kAudioDevicePropertyNominalSampleRate,
            mScope: kAudioObjectPropertyScopeGlobal,
            mElement: kAudioObjectPropertyElementMain
        )
        var aggNominalRate: Float64 = 0
        var nominalSize = UInt32(MemoryLayout<Float64>.size)
        let aggRateOK = AudioObjectGetPropertyData(
            aggregateDeviceID, &nominalAddr, 0, nil, &nominalSize, &aggNominalRate
        ) == noErr

        let actualSourceRate: Double
        if aggRateOK, aggNominalRate > 0, aggNominalRate != tapRate {
            logErr("[sysaudio] rate mismatch: tap=\(tapRate)Hz, aggregate=\(aggNominalRate)Hz — using aggregate rate")
            actualSourceRate = aggNominalRate
        } else if aggRateOK, aggNominalRate > 0 {
            actualSourceRate = aggNominalRate
        } else {
            actualSourceRate = tapRate
        }
        logErr("[sysaudio] source rate=\(actualSourceRate)Hz, channels=\(sourceChannels), nonInterleaved=\(isNonInterleaved) → target 16kHz mono")

        // 建 AVAudioConverter:源(actualSourceRate, mono) → 目标(16kHz, mono)。
        // stereo→mono 在 IO 回调里手动做,converter 只负责重采样。
        guard let srcFmt = AVAudioFormat(
            commonFormat: .pcmFormatFloat32, sampleRate: actualSourceRate,
            channels: 1, interleaved: false
        ) else { throw CaptureError.formatReadFailed }
        guard let dstFmt = AVAudioFormat(
            commonFormat: .pcmFormatFloat32, sampleRate: kTargetSampleRate,
            channels: kTargetChannels, interleaved: false
        ) else { throw CaptureError.formatReadFailed }
        guard let converter = AVAudioConverter(from: srcFmt, to: dstFmt) else {
            throw CaptureError.formatReadFailed
        }
        monoSourceFormat = srcFmt
        monoTargetFormat = dstFmt
        audioConverter = converter

        var localIoProcID: AudioDeviceIOProcID?
        let err = AudioDeviceCreateIOProcIDWithBlock(
            &localIoProcID, aggregateDeviceID, processingQueue
        ) { [weak self] _, inInputData, _, _, _ in
            self?.processAudioInput(
                inInputData,
                sourceRate: actualSourceRate,
                sourceChannels: sourceChannels,
                isNonInterleaved: isNonInterleaved
            )
        }
        guard err == noErr, let procID = localIoProcID else {
            throw CaptureError.ioProcFailed(err)
        }
        ioProcID = procID

        let startErr = AudioDeviceStart(aggregateDeviceID, procID)
        guard startErr == noErr else { throw CaptureError.deviceStartFailed(startErr) }
    }

    /// IO 回调:stereo→mono 混音 → AVAudioConverter 重采样到 16kHz → 写 stdout。
    private func processAudioInput(
        _ inInputData: UnsafePointer<AudioBufferList>,
        sourceRate: Double,
        sourceChannels: Int,
        isNonInterleaved: Bool
    ) {
        let abl = inInputData.pointee
        guard abl.mNumberBuffers > 0 else { return }
        let buffer0 = abl.mBuffers
        guard let data0 = buffer0.mData else { return }

        ioCallbackCount += 1

        let frameCount: Int
        if isNonInterleaved {
            frameCount = Int(buffer0.mDataByteSize) / MemoryLayout<Float>.size
        } else {
            frameCount = Int(buffer0.mDataByteSize) / (MemoryLayout<Float>.size * max(1, sourceChannels))
        }
        guard frameCount > 0 else { return }

        // --- stereo → mono ---
        var mono: [Float]
        if isNonInterleaved && sourceChannels >= 2 && abl.mNumberBuffers >= 2 {
            // 非交错:每声道一个 buffer。第二个 buffer 紧跟首个 AudioBuffer 之后。
            let ptr0 = data0.assumingMemoryBound(to: Float.self)
            let ch0 = [Float](UnsafeBufferPointer(start: ptr0, count: frameCount))
            let buffer1Ptr = UnsafeRawPointer(inInputData).advanced(
                by: MemoryLayout<AudioBufferList>.offset(of: \AudioBufferList.mBuffers)!
                    + MemoryLayout<AudioBuffer>.stride
            ).assumingMemoryBound(to: AudioBuffer.self)
            let buffer1 = buffer1Ptr.pointee
            if let data1 = buffer1.mData {
                let ptr1 = data1.assumingMemoryBound(to: Float.self)
                let ch1 = [Float](UnsafeBufferPointer(start: ptr1, count: frameCount))
                mono = [Float](repeating: 0, count: frameCount)
                vDSP_vadd(ch0, 1, ch1, 1, &mono, 1, vDSP_Length(frameCount))
                var half: Float = 0.5
                vDSP_vsmul(mono, 1, &half, &mono, 1, vDSP_Length(frameCount))
            } else {
                mono = ch0
            }
        } else if !isNonInterleaved && sourceChannels >= 2 {
            // 交错:[L,R,L,R,...] → 逐帧取均值。
            let totalSamples = frameCount * sourceChannels
            let ptr0 = data0.assumingMemoryBound(to: Float.self)
            let interleaved = UnsafeBufferPointer<Float>(start: ptr0, count: totalSamples)
            mono = [Float](repeating: 0, count: frameCount)
            for i in 0..<frameCount {
                var sum: Float = 0
                for ch in 0..<sourceChannels {
                    sum += interleaved[i * sourceChannels + ch]
                }
                mono[i] = sum / Float(sourceChannels)
            }
        } else {
            let ptr0 = data0.assumingMemoryBound(to: Float.self)
            mono = [Float](UnsafeBufferPointer(start: ptr0, count: frameCount))
        }

        // --- 重采样到 16kHz ---
        let samples: [Float]
        if sourceRate <= kTargetSampleRate {
            samples = mono
        } else {
            guard let converter = audioConverter,
                  let srcFmt = monoSourceFormat,
                  let dstFmt = monoTargetFormat else { return }

            guard let inputBuffer = AVAudioPCMBuffer(
                pcmFormat: srcFmt, frameCapacity: AVAudioFrameCount(frameCount)
            ) else { return }
            if let channelData = inputBuffer.floatChannelData {
                mono.withUnsafeBufferPointer { src in
                    if let base = src.baseAddress {
                        channelData[0].update(from: base, count: frameCount)
                    }
                }
            }
            inputBuffer.frameLength = AVAudioFrameCount(frameCount)

            let ratio = kTargetSampleRate / sourceRate
            let outputFrameCount = AVAudioFrameCount(ceil(Double(frameCount) * ratio)) + 16
            guard let outputBuffer = AVAudioPCMBuffer(
                pcmFormat: dstFmt, frameCapacity: outputFrameCount
            ) else { return }

            var inputConsumed = false
            var conversionError: NSError?
            let status = converter.convert(to: outputBuffer, error: &conversionError) { _, outStatus in
                if inputConsumed {
                    outStatus.pointee = .noDataNow
                    return nil
                }
                inputConsumed = true
                outStatus.pointee = .haveData
                return inputBuffer
            }
            if status == .error {
                if ioCallbackCount % 100 == 0 {
                    logErr("[sysaudio] AVAudioConverter error: \(conversionError?.localizedDescription ?? "unknown")")
                }
                return
            }
            let outCount = Int(outputBuffer.frameLength)
            guard outCount > 0, let outData = outputBuffer.floatChannelData else { return }
            samples = Array(UnsafeBufferPointer(start: outData[0], count: outCount))
        }

        guard !samples.isEmpty else { return }

        // 权限启发式累计:平均绝对幅度。
        var absSum: Double = 0
        for s in samples { absSum += Double(abs(s)) }
        energyLock.lock()
        emittedSampleCount += samples.count
        accumulatedAbsEnergy += absSum
        energyLock.unlock()

        // --- 写 stdout(裸 little-endian Float32 帧)---
        let byteCount = samples.count * MemoryLayout<Float>.size
        let data = samples.withUnsafeBytes { Data($0.prefix(byteCount)) }
        do {
            try stdout.write(contentsOf: data)
        } catch {
            // 下游(Python 读端)关闭管道 → 优雅退出。
            logErr("[sysaudio] stdout write failed (\(error)) — exiting")
            requestShutdown()
        }
    }

    // MARK: 权限启发式

    /// 采集 ~10s 后近零能量 → 判定系统音频录制权限被拒(无公开查询 API)。
    func checkPermissionAfterDelay() {
        let snapshot: (count: Int, energy: Double) = {
            energyLock.lock()
            defer { energyLock.unlock() }
            return (emittedSampleCount, accumulatedAbsEnergy)
        }()

        let avgEnergy = snapshot.count > 0 ? snapshot.energy / Double(snapshot.count) : 0
        // 近零能量 + 几乎没有样本 → 权限拒绝(被拒时 tap 产生静音、无报错)。
        if avgEnergy < 0.0001 {
            logErr("PERMISSION_DENIED")
        }
    }
}

// MARK: - 信号处理 + 主循环

// 信号处理器只能用 async-signal-safe 的操作:这里只置一个标志位。
private var shouldStop = false
private func requestShutdown() { shouldStop = true }

private func installSignalHandlers() {
    signal(SIGINT) { _ in shouldStop = true }
    signal(SIGTERM) { _ in shouldStop = true }
    // 写已关闭管道会发 SIGPIPE,默认杀进程;忽略它,改由 write 抛错路径处理。
    signal(SIGPIPE, SIG_IGN)
}

func runMain() -> Int32 {
    installSignalHandlers()

    let capturer = SystemAudioCapturer()
    do {
        try capturer.start()
    } catch {
        logErr("[sysaudio] start failed: \(error)")
        return 1
    }

    // 起 10s 后跑一次权限启发式(只查一次,够判定即可)。
    let permissionDeadline = Date().addingTimeInterval(10)
    var permissionChecked = false

    // 主线程轮询停止标志;IO 回调在 processingQueue 上异步推 PCM。
    while !shouldStop {
        if !permissionChecked, Date() >= permissionDeadline {
            capturer.checkPermissionAfterDelay()
            permissionChecked = true
        }
        // 短睡眠避免忙等;RunLoop 给 Core Audio 的属性监听器留出处理机会。
        RunLoop.current.run(until: Date().addingTimeInterval(0.1))
    }

    capturer.stop()
    try? FileHandle.standardOutput.synchronize()
    logErr("[sysaudio] capture stopped")
    return 0
}

exit(runMain())
