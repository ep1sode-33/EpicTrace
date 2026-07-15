// EpicTrace 启动器:供给引擎。uv 全托管 —— python install / venv / pip sync / wheel /
// helper 预置 / provenance 自愈,marker.json 判定是否已就绪。UI 无关,GUI 与 headless 共用。
import CryptoKit
import Foundation

struct ProvisionError: Error, CustomStringConvertible {
    let stage: String
    let message: String
    var description: String { "[\(stage)] \(message)" }
}

/// 首启失败最高频的两类要说人话(设计 §4.3):按错误文本粗分类。
func humanMessage(_ error: Error) -> String {
    let raw = String(describing: error)
    let lower = raw.lowercased()
    if lower.contains("no space left") || lower.contains("enospc") || lower.contains("磁盘") {
        return "磁盘空间不足:首次启动需要约 2GB 可用空间。\n\n\(raw)"
    }
    for hint in ["error sending request", "connection", "network", "timed out",
                 "dns error", "operation not permitted (os error", "tcp connect"] {
        if lower.contains(hint) {
            return "网络不可用或不稳定:首次启动需要联网下载运行环境,请检查网络后重试。\n\n\(raw)"
        }
    }
    return raw
}

/// 判据 = wheel 内容 hash(不是文件名/版本号):开发/验收期版本恒 0.1.0,
/// 同版本重打包 wheel hash 必变 → 自动触发增量 re-sync,helper 也随之更新。
struct Marker: Codable, Equatable {
    let lockSha256: String
    let wheelSha256: String
    let pythonVersion: String
}

final class ProvisionEngine {
    let resourcesDir: URL
    let runtimeDir: URL
    let dataDir: URL
    /// 全量输出(uv stdout/stderr 逐行)→ bootstrap.log;progress = 阶段级人话(UI 状态行)。
    let log: (String) -> Void
    let progress: (String) -> Void

    init(resourcesDir: URL, runtimeDir: URL, dataDir: URL,
         log: @escaping (String) -> Void, progress: @escaping (String) -> Void) {
        self.resourcesDir = resourcesDir
        self.runtimeDir = runtimeDir
        self.dataDir = dataDir
        self.log = log
        self.progress = progress
    }

    // ---- 路径 ----
    var uvBin: URL { resourcesDir.appendingPathComponent("uv") }
    var lockFile: URL { resourcesDir.appendingPathComponent("requirements.lock") }
    var frontendDist: URL { resourcesDir.appendingPathComponent("frontend-dist") }
    var helperSrc: URL { resourcesDir.appendingPathComponent("epictrace-sysaudio") }
    var appIcon: URL { resourcesDir.appendingPathComponent("AppIcon.icns") }
    var venvDir: URL { runtimeDir.appendingPathComponent("venv") }
    var venvPython: URL { venvDir.appendingPathComponent("bin/python") }
    var markerFile: URL { runtimeDir.appendingPathComponent("marker.json") }
    var pythonInstallDir: URL { runtimeDir.appendingPathComponent("python") }

    func pythonVersion() throws -> String {
        guard let s = try? String(contentsOf: resourcesDir.appendingPathComponent("python-version"),
                                  encoding: .utf8)
        else { throw ProvisionError(stage: "resources", message: "python-version 缺失") }
        return s.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    func wheelURL() throws -> URL {
        let files = (try? FileManager.default.contentsOfDirectory(
            at: resourcesDir, includingPropertiesForKeys: nil)) ?? []
        guard let whl = files.first(where: { $0.lastPathComponent.hasSuffix(".whl") })
        else { throw ProvisionError(stage: "resources", message: "Resources 里没有 .whl") }
        return whl
    }

    // ---- uv 状态全隔离(设计 §3.2;ComfyUI 泄漏全局的反面教材) ----
    func uvEnvironment() -> [String: String] {
        var env = ProcessInfo.processInfo.environment
        let r = runtimeDir.path
        env["UV_CACHE_DIR"] = r + "/uv-cache"
        env["UV_PYTHON_INSTALL_DIR"] = r + "/python"
        env["UV_PYTHON_CACHE_DIR"] = r + "/uv-python-cache"
        env["UV_PYTHON_BIN_DIR"] = r + "/python-bin"
        env["UV_TOOL_DIR"] = r + "/uv-tools"
        env["UV_TOOL_BIN_DIR"] = r + "/uv-tool-bin"
        env["UV_NO_CONFIG"] = "1"
        env["UV_MANAGED_PYTHON"] = "1"
        return env
    }

    /// 拉起壳用:uv 隔离变量(MinerU 子调用继承)+ EPICTRACE_* 注入。
    /// 供给已完成 → 维护性 uv 调用禁再下 Python(设计 §3.2);EPICTRACE_DATA_DIR 与
    /// installHelper 用的 dataDir 保持同源(--data-dir 覆盖时 helper 位置才不分叉)。
    func shellEnvironment() -> [String: String] {
        var env = uvEnvironment()
        env["UV_PYTHON_DOWNLOADS"] = "never"
        env["EPICTRACE_PACKAGED"] = "1"
        env["EPICTRACE_DATA_DIR"] = dataDir.path
        env["EPICTRACE_FRONTEND_DIST"] = frontendDist.path
        env["EPICTRACE_UV_BIN"] = uvBin.path
        if FileManager.default.fileExists(atPath: appIcon.path) {
            env["EPICTRACE_APP_ICON"] = appIcon.path
        }
        return env
    }

    // ---- marker ----
    func expectedMarker() throws -> Marker {
        let lock = try Data(contentsOf: lockFile)
        let lockHash = SHA256.hash(data: lock).map { String(format: "%02x", $0) }.joined()
        let wheel = try Data(contentsOf: try wheelURL())
        let wheelHash = SHA256.hash(data: wheel).map { String(format: "%02x", $0) }.joined()
        return Marker(lockSha256: lockHash,
                      wheelSha256: wheelHash,
                      pythonVersion: try pythonVersion())
    }

    func currentMarker() -> Marker? {
        guard let d = try? Data(contentsOf: markerFile) else { return nil }
        return try? JSONDecoder().decode(Marker.self, from: d)
    }

    func isProvisioned() -> Bool {
        guard let cur = currentMarker(), let exp = try? expectedMarker() else { return false }
        return cur == exp && FileManager.default.isExecutableFile(atPath: venvPython.path)
    }

    // ---- 子进程 ----
    @discardableResult
    func run(_ argv: [String], env: [String: String]? = nil, stage: String) throws -> Int32 {
        log("$ " + argv.joined(separator: " "))
        let p = Process()
        p.executableURL = URL(fileURLWithPath: argv[0])
        p.arguments = Array(argv.dropFirst())
        if let env { p.environment = env }
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = pipe
        do { try p.run() } catch {
            throw ProvisionError(stage: stage, message: "无法启动 \(argv[0]): \(error.localizedDescription)")
        }
        // 后台线程同步读到 EOF,替代 readabilityHandler:
        // 1) 子进程退出瞬间滞留在管道缓冲、尚未派发的最后几行不会丢(失败时最有
        //    诊断价值的恰是这几行),读干 EOF 后才取 tail;
        // 2) 子进程存活期间始终有人在读,管道写满不会反压阻塞子进程(经典死锁坑)。
        var tail: [String] = []
        let drained = DispatchSemaphore(value: 0)
        let reading = pipe.fileHandleForReading
        DispatchQueue.global(qos: .utility).async { [log] in
            // 字节域按 \n 分行 + 有损解码:read 边界切在多字节 UTF-8 字符中间时,
            // 残缺尾部留在 buf 等下一个 chunk 拼合,绝不整块丢弃。
            var buf = Data()
            func emit(_ data: Data) {
                guard !data.isEmpty else { return }  // 与旧 split 行为一致:跳过空行
                let line = String(decoding: data, as: UTF8.self)
                log(line)
                tail.append(line)
                if tail.count > 30 { tail.removeFirst() }
            }
            while true {
                let chunk = reading.availableData  // 阻塞读;空 Data = EOF
                if chunk.isEmpty { break }
                buf.append(chunk)
                while let nl = buf.firstIndex(of: 0x0A) {
                    emit(buf.subdata(in: buf.startIndex ..< nl))
                    buf.removeSubrange(buf.startIndex ... nl)
                }
            }
            emit(buf)  // 无换行结尾的最后一段
            drained.signal()
        }
        p.waitUntilExit()
        drained.wait()  // 等读线程见到 EOF,tail 才完整
        guard p.terminationStatus == 0 else {
            throw ProvisionError(stage: stage,
                                 message: "退出码 \(p.terminationStatus)\n" + tail.suffix(12).joined(separator: "\n"))
        }
        return p.terminationStatus
    }

    // ---- 自愈梯度(设计 §7):0=直接供给;1=清 uv 缓存后供给;2=删 runtime 全量重建 ----
    func provision(escalation: Int) throws {
        switch escalation {
        case 1:
            progress("清理下载缓存后重试…")
            _ = try? run([uvBin.path, "cache", "clean"], env: uvEnvironment(), stage: "cache-clean")
            try provision(force: false)
        case 2:
            try provision(force: true)
        default:
            try provision(force: false)
        }
    }

    // ---- 主流程 ----
    func provision(force: Bool = false) throws {
        let fm = FileManager.default
        if force, fm.fileExists(atPath: runtimeDir.path) {
            progress("清理旧运行时,全量重建…")
            try fm.removeItem(at: runtimeDir)
        }
        try fm.createDirectory(at: runtimeDir, withIntermediateDirectories: true)
        let ver = try pythonVersion()
        let env = uvEnvironment()

        // python 补丁版本变更:venv 指向旧解释器,必须重建(sync 不会换 python)。
        if let cur = currentMarker(), cur.pythonVersion != ver,
           fm.fileExists(atPath: venvDir.path) {
            progress("Python 版本变更,重建虚拟环境…")
            try fm.removeItem(at: venvDir)
        }

        progress("安装 Python \(ver)(约 26MB)…")
        try run([uvBin.path, "python", "install", ver], env: env, stage: "python-install")

        if !fm.fileExists(atPath: venvPython.path) {
            progress("创建虚拟环境…")
            try run([uvBin.path, "venv", "--python", ver, venvDir.path], env: env, stage: "venv")
        }

        progress("同步依赖(首次约 2GB,请保持联网)…")
        try run([uvBin.path, "pip", "sync", "--require-hashes",
                 "--python", venvPython.path, lockFile.path], env: env, stage: "pip-sync")

        progress("安装 EpicTrace…")
        try run([uvBin.path, "pip", "install", "--no-deps",
                 "--python", venvPython.path, try wheelURL().path], env: env, stage: "wheel")

        progress("预置系统内录组件…")
        try installHelper()

        progress("修复 Gatekeeper 属性…")
        selfHeal()

        let data = try JSONEncoder().encode(try expectedMarker())
        try data.write(to: markerFile)
        progress("环境就绪。")
    }

    func installHelper() throws {
        let fm = FileManager.default
        let binDir = dataDir.appendingPathComponent("bin")
        try fm.createDirectory(at: binDir, withIntermediateDirectories: true)
        let dst = binDir.appendingPathComponent("epictrace-sysaudio")
        if fm.fileExists(atPath: dst.path) { try fm.removeItem(at: dst) }
        try fm.copyItem(at: helperSrc, to: dst)
        try fm.setAttributes([.posixPermissions: 0o755], ofItemAtPath: dst.path)
    }

    /// provenance/quarantine xattr 递归清除 + 解释器 ad-hoc 重签。
    /// uv#16726:macOS 15.6+/26.x 上 com.apple.provenance + ad-hoc 组合会被内核 SIGKILL
    /// (无日志)或静默挂起;上游未修,自愈必备、幂等、无副作用。若将来观测到 venv 内
    /// torch .so 也中招,把重签范围扩到 venv(backlog,代价是首启多几分钟)。
    func selfHeal() {
        for attr in ["com.apple.provenance", "com.apple.quarantine"] {
            _ = try? run(["/usr/bin/xattr", "-r", "-d", attr, runtimeDir.path], stage: "xattr")
        }
        let fm = FileManager.default
        guard let en = fm.enumerator(at: pythonInstallDir, includingPropertiesForKeys: nil)
        else { return }
        for case let f as URL in en {
            let name = f.lastPathComponent
            let parent = f.deletingLastPathComponent().lastPathComponent
            let isMachO = parent == "bin" || name.hasSuffix(".dylib") || name.hasSuffix(".so")
            if isMachO, fm.isExecutableFile(atPath: f.path) || name.hasSuffix(".dylib") || name.hasSuffix(".so") {
                _ = try? run(["/usr/bin/codesign", "--force", "-s", "-", f.path], stage: "resign")
            }
        }
    }
}
