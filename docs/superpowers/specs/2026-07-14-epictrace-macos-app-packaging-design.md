# EpicTrace macOS 真·app 打包设计(引导器 + uv 全托管)

日期:2026-07-14
状态:设计已确认(William 拍板三处决策点),待出实施计划
前置:本文档的全部载重技术断言经过 8-agent 调研(2 代码库侦察 + 3 联网调研 + 3 对抗核查),18 条断言 16 条 CONFIRMED、2 条主体成立仅细节修正,关键事实与来源见附录。

## 1. 目标与非目标

**目标**:把现在的 dev 形态(`run.sh` + backend venv + pywebview)打包成一个**签名 + 公证、双击即用**的 `EpicTrace.app`(dmg 分发):

- 用户机器无需预装 Python / Node / Xcode CLT;
- 首启自动供给运行环境(联网),之后启动速度与 dev 形态相当;
- 升级 = 换 dmg,依赖增量同步;环境损坏可自愈;
- dev 工作流(`run.sh`)不受影响,打包版与 dev 共用同一份用户数据与模型缓存。

**非目标(本期不做)**:自动更新(Sparkle)、CI 化构建与 GitHub Releases(将来考虑,见 §11)、Intel 支持(mlx 是 arm64-only)、App Store 上架、崩溃上报。

## 2. 方案选型

三案对比后选**引导器 + uv 全托管**(方案 B):

- **A. PyInstaller/py2app 全冻结**:torch、mlx(metallib)、milvus-lite(内嵌二进制)、opencc/jieba(数据文件)逐个 hook,出名地脆;冻结后无真 `python` 可执行文件,现有 `sys.executable -m epictrace.asr.*` 三层进程链全要改;换来的"自包含"又被"模型反正运行时下载(~7GB)"抵消。否。
- **B. 引导器 .app + uv(选定)**:包内只放引导层,首启由 uv 装 Python 3.11 + venv + 锁定依赖。是代码库中 `MinerUProvisioner` 模式(uv 装 `<data_dir>/.MinerU-venv`)的推广;venv 里有真解释器,ASR/MinerU 子进程模式零改动;dmg 仅 ~100MB。量产先例:ComfyUI Desktop(内嵌 uv)、Datasette Desktop(内嵌 python-build-standalone + 首启建 venv)。
- **C. 现在迁 Tauri**:只换壳,不回答 Python 打包问题(sidecar 仍需冻结或引导二选一)。按原计划留待将来;本设计的启动器逻辑届时可平移。

## 3. 总体架构

### 3.1 .app 内容布局

```
EpicTrace.app/Contents/
├── MacOS/EpicTrace            Swift 启动器(原生进度窗 + 供给 + 拉起壳)
├── Resources/
│   ├── uv                     官方 aarch64 release,用自己的 Developer ID 重签
│   ├── epictrace-<ver>.whl    后端 + 壳(shell 挪进包内,见 §5)
│   ├── requirements.lock      uv pip compile --generate-hashes 产物
│   ├── python-version         钉死的 CPython 补丁版本(如 3.11.15)
│   ├── frontend-dist/         npm run build 产物
│   ├── epictrace-sysaudio     预编译 + 签名的 Swift 系统音频 helper
│   └── licenses/              uv(MIT/Apache-2.0 双许可)等许可文本
└── Info.plist
```

Info.plist 要点:`CFBundleIdentifier` 一经发布**永不更改**(TCC 授权按 bundle id 持久化,改了授权全丢);`NSMicrophoneUsageDescription` 与 `NSAudioCaptureUsageDescription`(中文文案)**必须存在**——TCC 按 responsible process(= 本 .app)查 usage description,缺失时 python 子进程首次开麦会被 TCC 直接杀死而非弹窗;`LSMinimumSystemVersion` 14.4(helper 的 Core Audio process taps 权限类别自 14.4 稳定);`LSArchitecturePriority` arm64-only。

### 3.2 运行时目录(两个根,贴合"事实来源 vs 派生"原则)

- **`~/Library/Application Support/EpicTrace/runtime/`** —— 纯派生、可随时删除重建:
  - `python/`(uv 托管 CPython)、`venv/`、`uv-cache/`、`marker.json`;
  - 启动器调 uv 时设全套隔离环境变量,把 uv 状态完全圈进此目录、不碰用户全局(`~/.cache/uv`、`~/.local/share/uv`、`~/.local/bin` 均不触碰):
    `UV_CACHE_DIR`、`UV_PYTHON_INSTALL_DIR`、`UV_PYTHON_CACHE_DIR`、`UV_PYTHON_BIN_DIR`、`UV_TOOL_DIR`、`UV_TOOL_BIN_DIR`、`UV_NO_CONFIG`、`UV_MANAGED_PYTHON`;供给完成后的维护性调用另加 `UV_PYTHON_DOWNLOADS=never`。
    (ComfyUI Desktop 正是漏了这层隔离,uv 状态泄漏到用户全局目录——引以为鉴。)
- **`~/.epictrace`(data_dir)不变**【决策点 1 已拍板:不迁移】:sqlite / milvus / staging / settings / `.asr-models` / `.MinerU-venv` / `bin/epictrace-sysaudio` 全部原位;HF 缓存 `~/.cache/huggingface` 亦不变。dev 与打包版共用同一份数据和模型,真机测试无缝、零迁移。

## 4. 启动器(Swift)行为

### 4.1 日常路径(marker 匹配)

读 `marker.json`(记录 `app_version` + `lock_hash` + `python_version`),与包内一致 → 直接
`<venv>/bin/python -m epictrace.shell`,等后端 `127.0.0.1:8765` 就绪(健康检查沿用现 shell 内的实现,启动器只负责超时兜底),pywebview 开窗。启动器**保持存活为父进程**直到壳退出(TCC 归因锚点,见 §8),转发 SIGTERM。

### 4.2 首启 / 升级供给(marker 缺失或不匹配)

原生进度窗(一根进度条 + 状态行 + 失败时「重试 / 查看日志」),按序执行:

1. `uv python install <python-version>`——补丁版本钉死在包里,避免用户间漂移;~26MB,官方 CDN(releases.astral.sh,失败自动回落 GitHub),SHA256 由 uv 内嵌元数据校验;
2. `uv venv --python <python-version> <runtime>/venv`;
3. `uv pip sync --require-hashes Resources/requirements.lock`——精确同步:装缺的、卸多余的、逐包 hash 校验、收敛后幂等 no-op,天然支持升级增量;
4. `uv pip install --no-deps Resources/epictrace-<ver>.whl`——app 自身 wheel 不进 lock,依赖只由 lock 决定;
5. 拷贝 `Resources/epictrace-sysaudio` → `<data_dir>/bin/epictrace-sysaudio`(覆盖旧版);
6. **provenance 自愈(必备,幂等)**:对 runtime 下 python 目录 strip `com.apple.provenance` / `com.apple.quarantine` xattr,并对解释器二进制 `codesign --force -s -` ad-hoc 重签(见 §7);
7. 写 `marker.json` → 进入 4.1 日常路径。

marker **只在全部成功后写**:中途失败/断电,下次启动自动重走供给(各步幂等)。

### 4.3 失败处理

- 每步失败:进度窗显示人话错误(区分"断网"/"磁盘满"/"其它")+「重试」;日志追加写 `~/Library/Logs/EpicTrace/bootstrap.log`;
- 环境损坏(venv 存在但 marker 校验失败或 python 起不来):走自愈梯度(§7)。

## 5. 后端 / 壳代码改动(调研钉出的全部断点,共四处 + 搬迁)

1. **frontend dist 定位**(`backend/epictrace/api/app.py:107`):现用 `Path(__file__).parents[3]/frontend/dist`(仓库布局),装成 wheel 后静默失效 → 白屏。改:`AppConfig` 增 `frontend_dist: Path | None`,来源 `EPICTRACE_FRONTEND_DIST` 环境变量(启动器传 Resources 路径);现有相对路径保留作 dev 回退。
2. **MinerU 的 uv 定位**(`backend/epictrace/media/mineru_provisioner.py:244` `shutil.which("uv")`):Finder 启动的 app PATH 只有系统四目录,必失败。构造器已有 `uv_bin` 注入口;`AppConfig` 增 `uv_bin`(来源 `EPICTRACE_UV_BIN`),三个构造点(`api/deps.py:141`、`services/settings.py:256`、`media/__init__.py:38`)接线。打包模式下 MinerU 与主环境共用包内同一个 uv;MinerU venv 的 uv 调用同样带 §3.2 的隔离环境变量。
3. **helper 不再运行时编译**(`shell/run.py:342-366` 依赖用户机器有 swiftc):打包模式下启动器负责拷贝预编译产物(§4.2 步 5),壳的 `_ensure_sysaudio_helper` 检测到 `EPICTRACE_PACKAGED=1` 时跳过 swiftc 路径;dev 模式保留懒编译。
4. **data_dir 可覆盖**:`AppConfig` 增 `EPICTRACE_DATA_DIR` 环境变量覆盖(默认仍 `~/.epictrace`)——打包版不用它,但干净账户首启测试(§9)必需。
5. **shell 搬进包**:`shell/run.py` → `epictrace/shell/`(wheel 现只打 `epictrace*`),入口 `python -m epictrace.shell`;其依赖(uvicorn/pywebview/pyobjc)已全在 wheel 依赖表。`shell/native/` 的 Swift 源随之挪到 `epictrace/shell/native/`(dev 懒编译仍能相对 `__file__` 定位,打包脚本也从这里取源编译),仓库根的 `shell/` 目录退役。

**零改动**:ASR 三层进程链(壳 → `sys.executable -m epictrace.asr.worker` → helper)——venv 有真解释器,`sys.executable` 自动跟随;faster-whisper/BGE/mlx 模型路径全锚在 `Path.home()`,与包位置无关。

## 6. 签名、公证与发布流水线

`scripts/package_app.sh`(本地构建【决策点 3 已拍板:暂不 CI/Release】),步骤:

1. `npm run build`(frontend/dist);
2. `python -m build --wheel`(backend);
3. `uv pip compile pyproject.toml --generate-hashes -o requirements.lock`(锁定,macOS arm64 单平台);
4. `swiftc -O` 编译启动器与 sysaudio helper;
5. 组装 .app(§3.1 布局);
6. **inside-out 逐二进制签名**,一律 `codesign --options runtime --timestamp` + 各自最小 entitlements(ASCII XML):先 `Resources/uv`(官方 release 只有 ad-hoc linker-signed 签名,**必须重签**否则公证必拒)、`epictrace-sysaudio`、再启动器、最后签 .app 整体;**禁用 `--deep`**(Apple 弃用,且会把 entitlements 错误摊给嵌套码);
7. `hdiutil` 打 UDZO 只读压缩 dmg → **dmg 本身也用 Developer ID Application 签**;
8. `xcrun notarytool submit --wait <dmg>`(只公证最外层)→ `xcrun stapler staple <dmg>`;可选增强:先单独公证并 staple .app 本体再打 dmg(离线首启不依赖在线票据查询)。

entitlements:启动器加 `com.apple.security.device.audio-input`(无害保险,权限归因边角);helper 做纯系统音频 tap,无对应 hardened-runtime entitlement、靠 TCC,不碰麦克风则不加 audio-input;启动器**不需要** `disable-library-validation` / `allow-dyld-environment-variables`——hardened runtime 是每进程属性,不遗传给 spawn 出的 python 子进程(已核实,Apple DevForums #120647)。

签名后 .app 内容**绝对不可变**(运行时写包 = 破 seal = Gatekeeper 报 damaged);一切可变状态在 bundle 外(§3.2),本设计天然满足。

## 7. 更新与自愈

- **升级**:新 dmg 拖覆盖 → 启动器 marker 不匹配 → 重走 §4.2(`uv pip sync` 只下变化的包,uv 缓存复用);helper 覆盖拷贝。
- **自愈梯度**(参考 ComfyUI Desktop 验证过的路子):`uv pip sync` 失败 → `uv cache clean`(app 域内)重试 → 仍失败则删 `runtime/` 全量重建;用户手动删/改环境同理收敛。
- **provenance 自愈(必备,非可选)**:已核实 macOS 15.6+/26.x 存在 `com.apple.provenance` xattr + ad-hoc 签名组合导致 uv 托管 python 被内核 SIGKILL(无日志)或静默挂起(Tahoe 变体);**上游 uv 至今未修**(自称修复的 PR #17123 实为 closed 未 merge,截至 uv 0.11.28 无自动重签)。§4.2 步 6 的 strip-xattr + ad-hoc 重签幂等执行,多名用户独立验证有效,同时是对未来 Gatekeeper 政策收紧的对冲。

## 8. 权限(TCC)设计

- **归因**:麦克风(worker 经 PortAudio/CoreAudio)与系统内录(helper 经 Core Audio process taps)的弹窗与授权都沿进程树归因到 responsible process = `EpicTrace.app`(启动器保活为父进程,直接 posix_spawn 子进程、不经 `open`/LaunchServices、不用 disclaim);一次授权,壳/worker/helper 全体共享,升级不丢(bundle id 稳定 + 签名有效)。
- **系统内录走的是"仅系统录音"独立 TCC 类别**(`NSAudioCaptureUsageDescription`,设置面板"录屏与系统录音"下的独立子项;tccutil 服务名实测为 `AudioCapture`):**不占用屏幕录制权限,无 Sequoia 起屏录的周期性重确认弹窗**——helper 用 CATap 而非 ScreenCaptureKit 是权限面上的关键优势,继续保持。
- **截图**(`/usr/sbin/screencapture`)吃屏幕录制 TCC(归因同上);屏录自 15.1 起"大致月度、常用则更少"的重确认只影响截图,不影响录音链路。
- 已核实:签名稳定的构建才会正常弹授权窗(未签名构建连 audio-capture 弹窗都不触发)——这也解释了 dev 形态下系统内录权限的历史怪现象;打包版反而更稳。

## 9. 测试与验收

1. **未签名本地版冒烟**:打包脚本支持 `--no-sign`,产物直接跑(本机已授权过,快速迭代);
2. **签名版真机全流程**:首启供给(掐网试断网路径)、TCC 弹窗归因文案(麦克风/系统内录两类)、ASR 三层进程链、MinerU 供给(确认用包内 uv)、索引 + 引用问答 + 跳回;
3. **干净 macOS 用户账户**模拟他人机器:无 venv、无模型、无 Xcode CLT 的完整首启(`EPICTRACE_DATA_DIR` 指向干净目录再叠加验证);
4. 顺手实测(零成本,补社区结论的官方文档空缺):CFBundleName trick 下 Dock 悬停名/强退对话框是否确实仍显示 "Python";
5. `spctl --assess` 对关键产物自检 + 公证回执确认;
6. **merge gate 照旧**:William 真机测过说合才合。

## 10. 已拍板的决策

| # | 决策 | 结论 | 理由 |
|---|------|------|------|
| 1 | data_dir 是否迁 Application Support | **保持 `~/.epictrace`** | dev/打包版共用数据与模型,零迁移;runtime(纯派生)才进 Application Support |
| 2 | Dock 身份瑕疵 | **MVP 接受**(菜单名 + Dock 图标运行时修好;Dock 悬停名/强退框显示 "Python" 救不了) | 彻底修法(wrapper bundle + dlopen libpython)复杂度不成比例;迁 Tauri 时自然根治 |
| 3 | 分发渠道 | **暂时本地构建自用**,GitHub Releases 将来考虑 | MVP 不做 CI 化;打包脚本保持可 CI 化的形态 |

## 11. 已知限制与后续方向

**已知限制(如实记录)**:

- 固定端口 8765:冲突时全链路(壳/worker/retranscribe 回写)失效;启动器健康检查会报错但不自动换端口(backlog);
- Dock 悬停名/强退框显示 "Python"(决策点 2,有意接受);
- 首启必须联网(依赖 ~1.9GB + 按需模型);无断点续传,失败整文件重试(uv 行为);
- ASR 就绪门把 HF 缓存根硬编码为 `~/.cache/huggingface/hub`(`asr/provisioner.py:17`,先于本设计存在):用户自设 `HF_HOME` 会使就绪门与实际下载位置脱节,本期不动;
- arm64-only(mlx 限制,与现状一致)。

**后续方向**:GitHub Releases + CI 公证;Sparkle 自动更新;迁 Tauri(根治窗口进程身份,启动器供给逻辑可平移);若 Dock 瑕疵先于 Tauri 变得不可忍,wrapper .app + dlopen libpython 是中间态修法(PBS 带 libpython 动态库,py2app/PyInstaller 同款原理)。

## 附录:关键事实与来源(全部经对抗核查)

| 事实 | 来源 |
|------|------|
| uv 官方 macOS 二进制仅 ad-hoc linker-signed(TeamIdentifier=not set),进 .app 必须重签 | 本机 codesign -dvv 实测;astral-sh/uv#14870(open) |
| 公证要求:所有可执行文件 Developer ID 签名 + hardened runtime + timestamp;嵌套第三方码若已由他人 Developer ID 正确签名可保留 | Apple notarizing-macos-software-before-distribution;DevForums #724307(DTS/Quinn) |
| hardened runtime / library validation / entitlements 为每进程属性,fork/exec 不继承(仅 App Sandbox inherit 例外) | Apple DevForums #120647;bpo-40198 |
| CLI/uv 下载的文件不带 quarantine xattr,Gatekeeper 不评估;Apple Silicon 要求至少 ad-hoc 签名(PBS/wheel .so 均满足);截至 2026-01 无政策收紧预告 | Apple DevForums(DTS Trusted Execution 系列);eclecticlight 2026-01-17 |
| provenance xattr + ad-hoc 组合可致 python 被 SIGKILL/静默挂起,uv 上游未修(PR #17123 closed 未 merge),strip+重签自愈有效 | astral-sh/uv#16726(open);eclecticlight 2025-12-05 |
| 发布流程:inside-out 签名、禁 --deep、dmg 也签、只公证最外层、staple | Apple Packaging Mac software for distribution / Creating distribution-signed code |
| TCC 归因 responsible process,usage description 查父 .app 的 Info.plist,缺失可致子进程被杀 | microsoft/vscode#307364;Qt blog(responsible process);hbldh/bleak#761 |
| Core Audio process taps = 独立"仅系统录音"TCC 类别(NSAudioCaptureUsageDescription,tccutil 名 AudioCapture),无屏录月度 nag;未签名构建不弹窗 | Apple Capturing system audio with Core Audio taps;insidegui/AudioCap;本机 tccutil 实测 |
| 屏录重确认:15.1 起"大致月度、常用更少",Tahoe 仍在;MDM 不能预授权(PPPC 无 Allow),仅可抑制重复弹窗 | 9to5mac 2024-08;MacRumors 2024-10;Apple PPPC 文档 |
| PBS 非 framework build 跑 PyObjC/pywebview GUI 可行;"GUI 必须 framework build"已过时;坑集中在 bundle 身份类 API | Glyph 2024-09;PBS quirks;astral-sh/python-build-standalone#274 |
| Dock 悬停名运行时不可改;CFBundleName trick 只修菜单名;NSApp.applicationIconImage 可换 Dock 图标 | 社区多来源一致(无官方文档,§9 留实测点) |
| uv 隔离环境变量、python install 行为(~26MB、SHA256 校验、file:// mirror 可离线)、pip compile/sync 语义 | docs.astral.sh(environment/storage/pip);本机 uv 0.11.28 全流程实测 |
| 先例:ComfyUI Desktop(内嵌 uv,未做状态隔离=反面教材)、Datasette Desktop(内嵌 PBS + 首启 venv) | Comfy-Org/desktop 源码实读;simonwillison.net 2021-09 |
