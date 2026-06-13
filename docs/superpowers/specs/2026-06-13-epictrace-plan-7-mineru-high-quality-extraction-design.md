# EpicTrace Plan 7 设计:MinerU 高质量提取(hybrid/high · 应用内 uv provisioning · 无回退)

> 把 PDF 提取从 pypdf 升级为 **MinerU**(版面/表格/公式/OCR 级质量),接在既有 `MediaProcessor` 缝后。提取质量上去后,Plan 5/6 的检索输入(切块/向量/引用)**自动变好**,无需改下游。
> **MinerU 即 PDF 引擎,无回退**:PDF 直接交给 MinerU,未 provision / 运行失败 → **明确报错**(不静默退回 pypdf)。`media/pdf.py`(pypdf)代码**保留在仓库但从活动注册表摘除**(备用、不接路径)。
> 动手前必读:`backend/epictrace/interfaces/media.py`(`MediaProcessor`/`MediaResult` 契约)、`backend/epictrace/media/__init__.py`(注册表)、`backend/epictrace/media/pdf.py`(现 pypdf 实现)。取舍见 `docs/decisions/`(D18+:hybrid/high、uv provisioning、无回退、provenance 存档)。

## 1. 背景与目标
现 `PdfMediaProcessor` 用 pypdf 抽文本——丢版面、表格塌成乱行、公式丢失、扫描件为空。研究确认(2026-06):MinerU 现有 **hybrid-engine**(VLM 版面理解 + 数字 PDF 原生文本抽取 + 109 语种 OCR),精度 ~95(pypdf 档 ≈ pipeline 86 之下),且 **Apple Silicon 上经 MLX 原生加速**;许可 v3.1.0 起转为基于 Apache 2.0 的自定义许可(VLM 权重仍标 AGPL,本地自用不触发义务)。

**目标**:在 `MediaProcessor` 缝后加 MinerU 后端(**hybrid-engine, effort=high**,模型源默认 modelscope),作为 **PDF 的唯一引擎**;**应用内 uv provisioning**(面向 DMG 分发,无需用户手动跑脚本);成功产出的 markdown 进既有切块/索引/引用链(char 偏移不变,文本更好);**存档 content_list**(块级 type/bbox/page_idx)备将来"跳回 PDF 原页"。

## 2. MVP 边界
**做**
- MinerU 子进程后端(`hybrid-engine --effort high --source modelscope`),取代注册表 PDF 槽。
- `MinerUProvisioner`:用**打包的 `uv`** 在 `<data_dir>/.MinerU-venv` 建隔离环境 + 装 mineru(dev 期 uv 取 PATH,DMG 期取内置二进制——同一套代码)。
- 应用内 provisioning 流程(后端接口 + 前端"高质量提取"区:状态/安装/进度);首次解析拉模型(~GB)。
- 成功后把 `content_list.json` 落 **sidecar**(`<data_dir>/provenance/<kind>-<id>.json`),由 ingest/attachment 调用点在持久化 `extracted_text` 时顺手写。
- **无回退**:未 provision / 子进程失败 / 超时 / 缺输出 → 抛明确错误,调用方按既有失败路径呈现(入库标失败 / 挂载报错)。

**不做(本期)**
- 跳回 PDF 原页 / 画 bbox 的前端(content_list 仅存档)。
- 自动重抽已入库的旧 PDF(开 provision 不回溯;新入库/重新扫描才用 MinerU)。
- DMG 打包器本身(只把 provisioning 做成 dev/DMG 同一套,打包期塞 uv 二进制)。
- 退回 pypdf 的任何分支(pypdf 代码保留但不接)。
- docx/pptx/text/image 走 MinerU(本期仅 PDF;office 维持现处理器)。

## 3. 核心原则锚定
- **接口缝不返工**:仅替换 PDF 槽的实现,`MediaProcessor.process(path)->MediaResult` 契约对下游不变;Plan 5/6 检索链零改动。
- **引用是一等公民**:markdown 文本走既有 char 偏移切块 → SourceViewer 高亮缓存文本跳回(与今相同,文本更优)。content_list 存档为将来 char区间→bbox→page 跳回留种,不丢数据。
- **事实来源 vs 派生**:provenance(content_list)= 派生缓存,可由重跑 MinerU 重建 → 放 sidecar、可删,不进核心 SQL 事实表。
- **本地优先 + opt-in 安装**:核心安装不带 MinerU(几 GB);用户在应用内点装、一次性下载,装完**全本地**运行。
- **macOS gRPC-fork 段错误**:MinerU 跑在**独立子进程**,torch/MLX 不与主进程 milvus-lite gRPC 客户端共存——结构性规避([[macos-embedding-milvus-fork-order]])。

## 4. 架构
```
get_processor(path, config) ──PDF──> MinerUMediaProcessor(provisioner, settings)
                                          │ provisioned & ready?
                              ┌───────────┴────────────┐
                             是                          否
                              ▼                           ▼
        subprocess: <.MinerU-venv>/bin/mineru        raise ExtractionEngineNotReady
          -p <pdf> -o <tmp> -b hybrid-engine             → 调用方报错"请先安装高质量提取引擎"
          --effort high --source modelscope
                              │ 子进程失败/超时/缺输出 → raise ExtractionFailed(无回退)
                              ▼ 读 <tmp>/<name>/<name>.md + <name>_content_list.json
        MediaResult{text=md, metadata={pages, content_list, backend}}  + 由调用方存档 content_list
```
MinerU 处理器**占据注册表 PDF 槽**;`get_processor` 改为**带 `config`**(取 data_dir + provisioning 状态)。`PdfMediaProcessor` 留在 `media/pdf.py` 但不在 `_PROCESSORS` 里。docx/pptx/text 处理器不动。

## 5. 组件(各一职、可注入测试)
### 5.1 `MinerUProvisioner`(新)
管 `<data_dir>/.MinerU-venv`。`is_ready()`(venv + mineru 可执行存在)/ `provision(progress_cb)`(`uv venv --python 3.11` → `uv pip install "mineru[all]"`——hybrid 需 pipeline(torch)+ VLM 两套依赖,精确 extra 按 pin 的 MinerU 版本定)/ `mineru_bin()` / `uv_bin()`(dev: PATH;DMG: 打包内置)。状态机:`not_installed → installing → ready / failed`。
### 5.2 `MinerUMediaProcessor`(新,实现 `MediaProcessor`)
`supports(path)` = 是 `.pdf`(恒接 PDF,不因未就绪而让槽空)。`process(path)`:未 ready → `raise ExtractionEngineNotReady`;ready → 调子进程 runner → 解析输出 → `MediaResult`;runner 失败 → `raise ExtractionFailed`(**不回退**)。
### 5.3 子进程 runner(新)
拼命令(backend/effort/source/输出目录)、`subprocess.run` 带 **timeout**(可配,默认如 600s);读 `<out>/<stem>/<stem>.md` + `<stem>_content_list.json`;非零退出 / 超时 / 缺文件 → 抛 `ExtractionFailed`。模型源 `--source modelscope`(国内快,可配 `MINERU_MODEL_SOURCE`)。
### 5.4 provenance 存档
`MinerUMediaProcessor` 把解析后的 `content_list` 放进 `MediaResult.metadata["content_list"]`;**ingest/attachment 两个调用点**在写 `extracted_text` 时,若 metadata 带 content_list → 落 `<data_dir>/provenance/<kind>-<id>.json`(`kind`=`ingest`/`reference`)。不穿过 chunker、不改引用链。
### 5.5 设置 / 注册表
`get_processor(path, config)`;`media/__init__.py` 用 config 构造 MinerU 处理器(注入 provisioner)。新增 provisioning 状态 + `model_source`(默认 modelscope)+ `timeout` 配置项。后端新增 `provision`(触发 + 进度)与 `status` 接口;前端设置页加"高质量提取"区(状态徽标 / 安装按钮 / 进度 / 失败原因)。

## 6. 数据流
1. 用户在设置点"安装高质量提取" → provisioner 用 uv 建 `.MinerU-venv` + 装 mineru(粗粒度进度)→ 跑一次内置样例 PDF **预热**拉模型(显示"下载模型(约 X GB),仅首次")→ `ready`。
2. PDF 入库(项目)/ 挂附件 → 经缝 → MinerU 子进程 → markdown 进既有切块/向量/引用链(char 偏移照旧)+ content_list 存档。
3. 未 provision → 处理 PDF 抛 `ExtractionEngineNotReady` → 入库标失败 / 挂载报错并提示去安装。
4. 子进程失败/超时 → `ExtractionFailed` → 同上呈现(不静默降级)。

## 7. provisioning & 模型下载 UX
- 环境安装:后端跑 uv,前端粗粒度状态("安装环境中")。
- 模型下载:预热阶段触发,`--source modelscope`;前端"下载模型(约 X GB),仅首次"(进度尽力从 mineru stdout 抓,抓不到则 spinner + 提示)。
- 一次性,装完全本地;契合本地优先(核心不带这坨)。

## 8. 数据模型 / 接口契约变更
- `get_processor(path)` → `get_processor(path, config)`;调用点(`IngestService`、`ReferenceService`)传 config。
- `_PROCESSORS`:移除 `PdfMediaProcessor()`,PDF 槽改 `MinerUMediaProcessor`;`media/pdf.py` 保留(不引用)。
- `MediaResult` 文本契约**不变**;`metadata` 可带 `content_list`/`backend`。
- 新异常 `ExtractionEngineNotReady` / `ExtractionFailed`(`media` 层);调用方映射到既有失败处理。
- 新 sidecar 目录 `<data_dir>/provenance/`;新配置 `model_source`/`extraction_timeout`/provisioning 状态。
- 新接口:`POST .../extraction/provision`(进度)、`GET .../extraction/status`。前端设置页新增区块。
- **无 SQL 表新增**(provenance 走 sidecar 文件)。

## 9. 错误处理与边界
- 报错而非回退:未 ready / 子进程非零 / 超时 / 缺输出 / 空文本 → 抛错,调用方呈现(入库失败态可重试;挂载报错可重挂)。
- provision 失败(uv 缺失 / 网络 / 磁盘)→ 状态 `failed` + 原因,可重试;PDF 处理仍报"未就绪"。
- 旧文件:开 provision **不自动重抽**;重新扫描/重新入库才走 MinerU。
- 许可:VLM 权重 AGPL——本地自用不触发;将来公开分发再评估(记决策日志)。
- Mac 上 hybrid 是否自动走 MLX:实现期按 pin 的 MinerU 版本确认后端旗标(`hybrid-engine` vs `hybrid-auto-engine` 跨版本有漂移)。

## 10. 测试策略(后端 TDD,Fakes 为主)
- `MinerUMediaProcessor`:**假子进程 runner** 喂预置 md + content_list → 断言 `MediaResult.text`/metadata.content_list;未 ready → `ExtractionEngineNotReady`;runner 失败 → `ExtractionFailed`(**无回退,不返回 pypdf 文本**)。
- `MinerUProvisioner`:**假 uv** 验证命令拼装(`uv venv` / `uv pip install`)+ 状态机 + `is_ready` 探测。
- `get_processor(config)`:PDF → MinerU 处理器;pypdf 不再被选中。
- provenance:成功解析 → sidecar 写入正确路径/内容(Fakes)。
- 调用点:ingest/attachment 失败时按既有路径标错;成功时存 provenance。
- 真模型 slow 测试走 `EPICTRACE_RUN_SLOW=1`(需已 provision,实跑 mineru);前端 `npm run build`。

## 11. 明确不做(重申)
跳回 PDF 原页/bbox 前端、旧文件自动重抽、DMG 打包器、退回 pypdf 的分支、office/图片走 MinerU。
