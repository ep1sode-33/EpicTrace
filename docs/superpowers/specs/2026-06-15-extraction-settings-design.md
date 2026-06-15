# EpicTrace「提取设置」设计(引擎可选 + MinerU 可配置 + 模型预下载)

> 批次 B(+ v2 叠加)。给 Plan 7 的提取加上**文档处理引擎选择**(pypdf / MinerU)+ MinerU 的**用户可配置设置**(effort / model_source)+ **模型预下载**(独立成步)。
> **文档处理引擎**:**pypdf**(简单文字处理,默认、开箱即用、免安装/免模型)/ **MinerU**(OCR/VLM,质量高;需安装环境 + 下模型)。UI 用一个"引擎选择器"承载;MinerU 的旋钮(effort / model_source / 安装 / 下模型)**条件渲染在"选中 MinerU"之下**。
> 动手前必读:`backend/epictrace/media/mineru_provisioner.py`、`backend/epictrace/services/settings.py`(settings.json 读写 + `extraction_status`)、`backend/epictrace/config.py`(`extraction_effort`/`model_source`/`extraction_timeout` 默认)、`backend/epictrace/api/{deps.py,routers/settings.py}`、`backend/epictrace/media/{mineru.py,mineru_runner.py,__init__.py}`、`backend/epictrace/api/routers/references.py`(SSE 挂载)、`backend/epictrace/services/index.py`、前端 `frontend/src/views/SettingsView.tsx` + `frontend/src/lib/api.ts`。

## 1. 背景与目标
Plan 7 把 `effort`(默认 medium)、`model_source`(默认 modelscope)做成了 `AppConfig` 默认值,但**用户无法在 UI 里改**;且 `provision()` 只装包不下模型 → 首次解析静默下几 GB(已知坑)。本期:① 把 `effort` / `model_source` 做成**持久化用户设置 + UI**;② **模型预下载**独立成步(按钮 + 进度);③ "装了包没下模型"时用户发文件 → **自动下载 + 可见进度**(走现有进度通道),不再静默。

## 2. MVP 边界
**做**:`extraction` 持久化设置(engine/effort/model_source)+ GET/PUT;**引擎选择 pypdf(默认)/ mineru**,据 engine 选富文档处理器(pypdf → 内置 `PdfMediaProcessor`/`DocxMediaProcessor`/`PptxMediaProcessor`;mineru → `MinerUMediaProcessor`);provisioner 拆成"装包 / 下模型"两步 + 扩展状态机;`is_ready()` 含**真实模型缓存检测**;无模型发文件(仅 MinerU 引擎)→ 自动下载带进度;前端引擎选择器 + MinerU 旋钮条件渲染。
**不做**:backend(hybrid/pipeline/vlm)切换、pypdf/MinerU 之外的引擎、换 `model_source` 自动重下(只给手动"重新下载")、effort 之外的 MinerU 参数。

## 3. 状态机(provisioner 扩展)
`not_installed → installing → installed_no_models → downloading_models → ready / failed`。
`is_ready()` = mineru 可执行 `is_file()` **且** 模型已下。**模型就绪 = 检测 HuggingFace / modelscope 真实缓存**(替换旧的 `<venv>/.models-ready` 哨兵):HF hub 缓存(默认 `~/.cache/huggingface/hub/`,可注入便于测试)下存在 MinerU 的模型仓库目录(`models--opendatalab--*MinerU*` 或 `models--opendatalab--*PDF-Extract-Kit*`,且**非空**)即视模型可用。这样能识别**已存在**的模型(用户机器上模型在 HF 缓存里但没哨兵 → 不再误判"没下");`download_models` 成功后不再写哨兵,纯靠真实缓存检测。`mineru-models-download` 幂等(缓存已有时快速 no-op)。

## 4. 持久化设置
`settings.json` 增 `extraction` 对象:`{ "engine": "pypdf", "effort": "medium", "model_source": "modelscope" }`(**engine 默认 pypdf**)。`SettingsService`:`get_extraction_settings()` / `set_extraction_settings(...)`(校验 `engine∈{pypdf,mineru}`、`effort∈{high,medium}`、`model_source∈{modelscope,huggingface,local}`;非法 → 抛/400)。**`get_processor` 据 `engine` 选富文档处理器**(无持久化 → 回退默认 pypdf);engine=mineru 时构造 `MinerUMediaProcessor` 读持久化的 effort/model_source(取代旧的直接用 `AppConfig` 的写法)。

## 5. provisioner 改造
- `provision(progress_cb)`:`uv venv` + `uv pip install "mineru[all]"`(**只装包**)→ `installed_no_models`(移除任何隐式模型下载)。
- `download_models(progress_cb)`(新):跑 `<venv>/bin/mineru-models-download -s <model_source> -m all`(hybrid 需 pipeline + vlm 两套权重)流式进度 → `ready`。subprocess 调用**可注入**便于测试。成功后**不写哨兵**——靠真实缓存检测。
- `is_ready()`:bin + **真实模型缓存检测**(`detect_mineru_models(hf_cache_dir)`,见 §3)。`state` 反映五态(+ downloading)。沿用 Plan 7 的并发锁 + `last_error`;`provision` 与 `download_models` 各自不可重入。缓存根 `hf_cache_dir` 可注入(测试用 tmp 目录假造仓库目录,不碰真 `~/.cache`)。

## 6. 无模型发文件 → 自动下载带进度(本期关键;仅 MinerU 引擎)
**仅当实际处理器是 `MinerUMediaProcessor` 时才涉及模型下载**(engine=pypdf 时富文档走内置处理器,完全不碰 MinerU/provisioner,`isinstance(proc, MinerUMediaProcessor)` 门自然跳过)。处理文件前(挂附件走 SSE / 项目索引走索引状态)检查 provisioner 状态:
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
**文档处理引擎选择器**(下拉/段控,两项):**pypdf**(简单文字处理,默认) / **MinerU**(OCR/VLM,质量高)。改动即 `PUT` 持久化(乐观 + 失败回滚)。
- 选中 **pypdf** → 不渲染 MinerU 旋钮(开箱即用,无需安装/下模型)。
- 选中 **MinerU** → 渲染:
  - **状态徽标**:未安装 / 已安装·未下模型 / 下载中 / 就绪 / 失败(+ 失败原因)。
  - **「安装」**(装包)、**「下载模型」**(进度;轮询 status)。
  - **effort 下拉**(high/medium)、**model_source 下拉**(modelscope/huggingface/local)。换 `model_source` 时提示"需重新下载模型"。
- `api.ts`:`getExtractionSettings` / `putExtractionSettings` / `downloadModels`(复用现有 status 轮询)。

## 9. 错误处理与边界
- 下载失败 → `state=failed` + `last_error`,可重试。
- `PUT` 非法值 → 400。
- 无模型发文件、自动下载失败 → 该轮按既有失败路径呈现(挂载 error 事件 / 索引失败),不静默。
- 换 `model_source`:**不自动重下**,提示用户手动"重新下载模型"。

## 10. 测试策略(后端 TDD,Fakes)
- provisioner:`provision`→`installed_no_models`(不下模型)、`download_models`→`ready`、**真实模型缓存检测**(注入 tmp 缓存根:有 MinerU/PDF-Extract-Kit 仓库非空 → 就绪;空目录/无关仓库/缺根 → 未就绪)、并发不可重入(假 uv/下载 runner)。
- `SettingsService`:`get/set_extraction_settings` + 校验(默认 engine=pypdf;非法 engine/effort/model_source 拒绝)。
- `get_processor` 引擎选择:engine=pypdf(默认)→ 富文档走内置 `Pdf/Docx/Pptx` 处理器;engine=mineru → `MinerUMediaProcessor`(读持久化 effort/model_source);text/code/data 始终 `TextMediaProcessor`,与 engine 无关。
- 无模型发文件(仅 MinerU 引擎):状态 `installed_no_models` → 触发下载并经进度通道吐进度、再提取(假 download)。
- API:get/put/download-models/status(扩展状态)。
- 前端:`npm run build`。

## 11. 明确不做(重申)
backend(hybrid/pipeline/vlm)切换、pypdf/MinerU 之外的引擎、换源自动重下、effort 之外的 MinerU 参数。
