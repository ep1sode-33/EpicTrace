# EpicTrace「提取设置」设计(MinerU 可配置 + 模型预下载)

> 批次 B。给 Plan 7 的 MinerU 提取加上**用户可配置设置**(effort / model_source)+ **模型预下载**(独立成步)。当前只有 MinerU 一个引擎,UI 用一个"引擎选择器"壳承载(MinerU 默认选中),其旋钮**条件渲染在"选中 MinerU"之下**,为将来加引擎留结构。
> 动手前必读:`backend/epictrace/media/mineru_provisioner.py`、`backend/epictrace/services/settings.py`(settings.json 读写 + `extraction_status`)、`backend/epictrace/config.py`(`extraction_effort`/`model_source`/`extraction_timeout` 默认)、`backend/epictrace/api/{deps.py,routers/settings.py}`、`backend/epictrace/media/{mineru.py,mineru_runner.py,__init__.py}`、`backend/epictrace/api/routers/references.py`(SSE 挂载)、`backend/epictrace/services/index.py`、前端 `frontend/src/views/SettingsView.tsx` + `frontend/src/lib/api.ts`。

## 1. 背景与目标
Plan 7 把 `effort`(默认 medium)、`model_source`(默认 modelscope)做成了 `AppConfig` 默认值,但**用户无法在 UI 里改**;且 `provision()` 只装包不下模型 → 首次解析静默下几 GB(已知坑)。本期:① 把 `effort` / `model_source` 做成**持久化用户设置 + UI**;② **模型预下载**独立成步(按钮 + 进度);③ "装了包没下模型"时用户发文件 → **自动下载 + 可见进度**(走现有进度通道),不再静默。

## 2. MVP 边界
**做**:`extraction` 持久化设置(engine/effort/model_source)+ GET/PUT;provisioner 拆成"装包 / 下模型"两步 + 扩展状态机;`is_ready()` 含模型检查;无模型发文件 → 自动下载带进度;前端引擎选择器壳 + MinerU 旋钮条件渲染。
**不做**:backend(hybrid/pipeline/vlm)切换、多厂商引擎、换 `model_source` 自动重下(只给手动"重新下载")、effort 之外的 MinerU 参数。

## 3. 状态机(provisioner 扩展)
`not_installed → installing → installed_no_models → downloading_models → ready / failed`。
`is_ready()` = mineru 可执行 `is_file()` **且** 模型已下。模型检查 marker:`mineru-models-download` 会生成 `mineru.json` 并填充模型目录 → 以"`mineru.json` 存在且其引用的模型目录非空"为准(实现期对着 pin 的 MinerU 版本确定确切 marker)。

## 4. 持久化设置
`settings.json` 增 `extraction` 对象:`{ "engine": "mineru", "effort": "medium", "model_source": "modelscope" }`。`SettingsService`:`get_extraction_settings()` / `set_extraction_settings(...)`(校验 `effort∈{high,medium}`、`model_source∈{modelscope,huggingface,local}`、`engine` 暂只 `mineru`;非法 → 抛/400)。**runner/registry 构造 `MinerUMediaProcessor` 时读持久化设置**(无 → 回退 `AppConfig` 默认),取代当前直接用 `AppConfig.extraction_effort`/`model_source` 的写法。

## 5. provisioner 改造
- `provision(progress_cb)`:`uv venv` + `uv pip install "mineru[all]"`(**只装包**)→ `installed_no_models`(移除任何隐式模型下载)。
- `download_models(progress_cb)`(新):跑 `<venv>/bin/mineru-models-download`(按 `model_source`;确切旗标实现期定)流式进度 → `ready`。subprocess 调用**可注入**便于测试。
- `is_ready()`:bin + 模型 marker。`state` 反映五态(+ downloading)。沿用 Plan 7 的并发锁 + `last_error`;`provision` 与 `download_models` 各自不可重入。

## 6. 无模型发文件 → 自动下载带进度(本期关键)
处理文件前(挂附件走 SSE / 项目索引走索引状态)检查 provisioner 状态:
- `ready` → 正常提取。
- `installed_no_models` → **先触发 `download_models`,把"正在下载模型 …"进度走现有进度通道**(挂载 = SSE `status` 事件;索引 = 索引状态),下完接着提取(block-until-ready,可见)。
- `not_installed` → 报"请先在设置中安装高质量提取引擎"(Plan 7 行为)。
- 下载已在进行 → 等待/复用同一下载,不重复触发。

## 7. API
- `GET /api/extraction/settings` → `{engine, effort, model_source}`。
- `PUT /api/extraction/settings`(校验)→ 持久化 + 返回更新后的设置。
- `POST /api/extraction/download-models` → 触发模型下载(带进度;与 provision 一致的后台 + 状态轮询机制)。
- 现有 `GET /api/extraction/status` 扩展:`state` 含 `installed_no_models`/`downloading_models`;带 `last_error`。

## 8. 前端(扩 Plan 7「高质量提取」区)
**引擎选择器**(下拉/段控;当前唯一项 MinerU,默认选中)→ 选中 MinerU 时渲染:
- **状态徽标**:未安装 / 已安装·未下模型 / 下载中 / 就绪 / 失败(+ 失败原因)。
- **「安装」**(装包)、**「下载模型」**(进度;轮询 status)。
- **effort 下拉**(high/medium)、**model_source 下拉**(modelscope/huggingface/local)→ 改动即 `PUT` 持久化(乐观 + 失败回滚)。换 `model_source` 时提示"需重新下载模型"。
- `api.ts`:`getExtractionSettings` / `putExtractionSettings` / `downloadModels`(复用现有 status 轮询)。

## 9. 错误处理与边界
- 下载失败 → `state=failed` + `last_error`,可重试。
- `PUT` 非法值 → 400。
- 无模型发文件、自动下载失败 → 该轮按既有失败路径呈现(挂载 error 事件 / 索引失败),不静默。
- 换 `model_source`:**不自动重下**,提示用户手动"重新下载模型"。

## 10. 测试策略(后端 TDD,Fakes)
- provisioner:`provision`→`installed_no_models`(不下模型)、`download_models`→`ready`、`is_ready` 的模型检查、并发不可重入(假 uv/下载 runner)。
- `SettingsService`:`get/set_extraction_settings` + 校验(非法 effort/model_source 拒绝)。
- runner/registry:`MinerUMediaProcessor` 用**持久化的** effort/model_source(而非硬编码/纯 AppConfig)。
- 无模型发文件:状态 `installed_no_models` → 触发下载并经进度通道吐进度、再提取(假 download)。
- API:get/put/download-models/status(扩展状态)。
- 前端:`npm run build`。

## 11. 明确不做(重申)
backend(hybrid/pipeline/vlm)切换、多厂商引擎、换源自动重下、effort 之外的 MinerU 参数。
