// EpicTrace 启动器入口:GUI(默认,进度窗 + 拉起壳保活)与 headless(打包冒烟/干净账户测试)。
// 启动器保活为父进程 = TCC responsible process 锚点:python/helper 的麦克风、系统内录
// 弹窗与授权都归因到本 .app(设计 §8),所以绝不能 exec 后自退。
import AppKit
import Foundation

// ---- 公共:路径与日志 ----
let fm = FileManager.default
let home = fm.homeDirectoryForCurrentUser
let defaultRuntime = home.appendingPathComponent("Library/Application Support/EpicTrace/runtime")
let logDir = home.appendingPathComponent("Library/Logs/EpicTrace")

func openAppendHandle(_ fileName: String) -> FileHandle? {
    try? fm.createDirectory(at: logDir, withIntermediateDirectories: true)
    let f = logDir.appendingPathComponent(fileName)
    if !fm.fileExists(atPath: f.path) { fm.createFile(atPath: f.path, contents: nil) }
    let h = try? FileHandle(forWritingTo: f)
    _ = try? h?.seekToEnd()
    return h
}

let logHandle = openAppendHandle("bootstrap.log")
let logQueue = DispatchQueue(label: "epictrace.launcher.log")
func writeLog(_ line: String) {
    logQueue.sync {
        if let d = (line + "\n").data(using: .utf8) { try? logHandle?.write(contentsOf: d) }
    }
}

// ---- 参数解析 ----
var args = Array(CommandLine.arguments.dropFirst())
func takeValue(_ flag: String) -> String? {
    guard let i = args.firstIndex(of: flag), i + 1 < args.count else { return nil }
    let v = args[i + 1]
    args.removeSubrange(i ... i + 1)
    return v
}
let headless = args.contains("--headless-provision")
let printPlan = args.contains("--print-plan")
let force = args.contains("--force")
let resourcesOverride = takeValue("--resources")
let runtimeOverride = takeValue("--runtime")
let dataDirOverride = takeValue("--data-dir")

func resolveResources() -> URL {
    if let r = resourcesOverride { return URL(fileURLWithPath: r) }
    if let r = Bundle.main.resourceURL { return r }
    fputs("无法定位 Resources(bundle 外运行请传 --resources)\n", stderr)
    exit(2)
}

// data_dir 解析:--data-dir > EPICTRACE_DATA_DIR > ~/.epictrace,与壳/后端同一优先级语义
// (installHelper 的落点和 shellEnvironment 注入的 EPICTRACE_DATA_DIR 因此恒同源)。
func resolveDataDir() -> URL {
    if let d = dataDirOverride { return URL(fileURLWithPath: d) }
    if let d = ProcessInfo.processInfo.environment["EPICTRACE_DATA_DIR"], !d.isEmpty {
        return URL(fileURLWithPath: (d as NSString).expandingTildeInPath)
    }
    return home.appendingPathComponent(".epictrace")
}

let engine = ProvisionEngine(
    resourcesDir: resolveResources(),
    runtimeDir: runtimeOverride.map { URL(fileURLWithPath: $0) } ?? defaultRuntime,
    dataDir: resolveDataDir(),
    log: { line in
        writeLog(line)
        if headless || printPlan { print(line) }
    },
    progress: { msg in
        writeLog("== " + msg)
        if headless { print("== " + msg) }
        AppState.shared?.setStatus(msg)
    }
)

// ---- headless / print-plan 模式 ----
if printPlan {
    print("resources: \(engine.resourcesDir.path)")
    print("runtime:   \(engine.runtimeDir.path)")
    print("data-dir:  \(engine.dataDir.path)")
    print("provisioned: \(engine.isProvisioned())")
    if let m = try? engine.expectedMarker() {
        print("expect: python \(m.pythonVersion), wheel \(m.wheelSha256.prefix(12))…, lock \(m.lockSha256.prefix(12))…")
    }
    for (k, v) in engine.uvEnvironment().sorted(by: { $0.key < $1.key }) where k.hasPrefix("UV_") {
        print("env \(k)=\(v)")
    }
    exit(0)
}
if headless {
    // 快速路径与 GUI 对齐:marker 命中且未 --force 时秒退(冒烟可重复跑)。
    if !force, engine.isProvisioned() {
        print("already provisioned → \(engine.runtimeDir.path)")
        exit(0)
    }
    do {
        try engine.provision(force: force)
        print("OK provisioned → \(engine.runtimeDir.path)")
        exit(0)
    } catch {
        fputs("供给失败:\(humanMessage(error))\n日志:\(logDir.path)/bootstrap.log\n", stderr)
        exit(1)
    }
}

// ---- GUI 模式 ----
final class AppState: NSObject, NSApplicationDelegate {
    static var shared: AppState?
    var window: NSWindow?
    var statusLabel: NSTextField?
    var shell: Process?
    var shellLaunchedAt: Date?
    /// 自愈梯度游标(设计 §7):0 直接供给 → 1 清缓存重试 → 2 删 runtime 全量重建。
    var escalation = 0

    func setStatus(_ msg: String) {
        DispatchQueue.main.async { self.statusLabel?.stringValue = msg }
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        AppState.shared = self
        installSignalForwarders()
        if engine.isProvisioned() {
            launchShell()
        } else {
            showProgressWindow()
            DispatchQueue.global(qos: .userInitiated).async { self.runProvision() }
        }
    }

    func runProvision() {
        do {
            try engine.provision(escalation: escalation)
            DispatchQueue.main.async {
                self.window?.orderOut(nil)
                self.launchShell()
            }
        } catch {
            DispatchQueue.main.async { self.showError(error) }
        }
    }

    func showProgressWindow() {
        // 供给阶段临时成为普通 app(Dock 出现 EpicTrace);拉起壳后降回 accessory 交还前台。
        NSApp.setActivationPolicy(.regular)
        let w = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 480, height: 120),
                         styleMask: [.titled], backing: .buffered, defer: false)
        w.title = "EpicTrace 首次启动"
        w.center()
        let label = NSTextField(labelWithString: "准备运行环境…")
        label.frame = NSRect(x: 20, y: 66, width: 440, height: 20)
        let bar = NSProgressIndicator(frame: NSRect(x: 20, y: 34, width: 440, height: 20))
        bar.style = .bar
        bar.isIndeterminate = true
        bar.startAnimation(nil)
        w.contentView?.addSubview(label)
        w.contentView?.addSubview(bar)
        w.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        window = w
        statusLabel = label
    }

    func showError(_ error: Error) {
        let alert = NSAlert()
        alert.alertStyle = .critical
        alert.messageText = "EpicTrace 环境准备失败"
        let next = escalation >= 1 ? "重试(全量重建)" : "重试(清缓存)"
        alert.informativeText = "\(humanMessage(error))\n\n日志:~/Library/Logs/EpicTrace/bootstrap.log"
        alert.addButton(withTitle: next)
        alert.addButton(withTitle: "打开日志")
        alert.addButton(withTitle: "退出")
        switch alert.runModal() {
        case .alertFirstButtonReturn:
            escalation = min(escalation + 1, 2)
            if window == nil { showProgressWindow() } else { window?.makeKeyAndOrderFront(nil) }
            DispatchQueue.global(qos: .userInitiated).async { self.runProvision() }
        case .alertSecondButtonReturn:
            NSWorkspace.shared.open(logDir.appendingPathComponent("bootstrap.log"))
            NSApp.terminate(nil)
        default:
            NSApp.terminate(nil)
        }
    }

    func launchShell() {
        NSApp.setActivationPolicy(.accessory)  // 把 Dock 交还给壳(python)进程
        let p = Process()
        p.executableURL = engine.venvPython
        p.arguments = ["-m", "epictrace.shell"]
        p.environment = engine.shellEnvironment()
        // 壳输出不能无声消失:Finder 双击启动无终端,Python 侧 print/异常栈全部
        // append 到 shell.log(stdout/stderr 共用同一 handle)。
        if let shellLog = openAppendHandle("shell.log") {
            p.standardOutput = shellLog
            p.standardError = shellLog
            writeLog("shell 输出重定向 → \(logDir.path)/shell.log")
        }
        p.terminationHandler = { proc in
            writeLog("shell 退出,code=\(proc.terminationStatus)")
            DispatchQueue.main.async { self.handleShellExit(proc) }
        }
        do {
            try p.run()
            shell = p
            shellLaunchedAt = Date()
            writeLog("shell 已拉起 pid=\(p.processIdentifier)")
            watchBackendHealth()
        } catch {
            showError(ProvisionError(stage: "launch", message: error.localizedDescription))
        }
    }

    /// 壳秒退且非 0 = 环境损坏(marker 在但 venv 起不来等):给「重建环境」出口(设计 §4.3)。
    func handleShellExit(_ proc: Process) {
        let uptime = shellLaunchedAt.map { Date().timeIntervalSince($0) } ?? .infinity
        if proc.terminationStatus != 0, uptime < 10 {
            let alert = NSAlert()
            alert.alertStyle = .critical
            alert.messageText = "EpicTrace 启动失败"
            alert.informativeText = "运行环境可能已损坏(退出码 \(proc.terminationStatus))。"
                + "\n\n日志:~/Library/Logs/EpicTrace/bootstrap.log"
            alert.addButton(withTitle: "重建环境")
            alert.addButton(withTitle: "退出")
            if alert.runModal() == .alertFirstButtonReturn {
                escalation = 2
                showProgressWindow()
                DispatchQueue.global(qos: .userInitiated).async { self.runProvision() }
                return
            }
        }
        NSApp.terminate(nil)
    }

    /// 超时兜底(设计 §4.1/§11):30s 内后端未就绪且壳还活着 → 报错提示(不自动换端口)。
    func watchBackendHealth() {
        DispatchQueue.global(qos: .utility).async {
            let deadline = Date().addingTimeInterval(30)
            let url = URL(string: "http://127.0.0.1:8765/api/health")!
            while Date() < deadline {
                if (try? Data(contentsOf: url)) != nil { return }
                Thread.sleep(forTimeInterval: 1.0)
            }
            guard let shell = self.shell, shell.isRunning else { return }
            writeLog("后端 30s 未就绪(壳仍在运行)")
            DispatchQueue.main.async {
                let alert = NSAlert()
                alert.alertStyle = .warning
                alert.messageText = "后端未在 30 秒内就绪"
                alert.informativeText = "端口 8765 可能被其它程序占用。"
                    + "\n\n日志:~/Library/Logs/EpicTrace/bootstrap.log"
                alert.addButton(withTitle: "继续等待")
                alert.addButton(withTitle: "退出")
                if alert.runModal() == .alertSecondButtonReturn {
                    shell.terminate()
                    NSApp.terminate(nil)
                }
            }
        }
    }

    func installSignalForwarders() {
        for sig in [SIGTERM, SIGINT] {
            signal(sig, SIG_IGN)
            let src = DispatchSource.makeSignalSource(signal: sig, queue: .main)
            src.setEventHandler {
                self.shell?.terminate()
                NSApp.terminate(nil)
            }
            src.resume()
            signalSources.append(src)
        }
    }
}

var signalSources: [DispatchSourceSignal] = []
let app = NSApplication.shared
let delegate = AppState()
app.delegate = delegate
app.run()
