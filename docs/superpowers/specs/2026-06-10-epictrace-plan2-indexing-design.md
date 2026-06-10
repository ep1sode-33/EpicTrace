# EpicTrace Plan 2:索引管线 设计

- **日期**:2026-06-10
- **范围**:把当前占位的「建立索引」+ 待索引队列接通。待索引文件 →(索引时)经 MediaProcessor 提取文本 → 切分(保字符偏移)→ BGE-M3 本地嵌入 → 写入 Milvus Lite(落地现有 `VectorStore` 接口)→ 文件翻成「已索引」。
- **前置**:Foundation + 文件入库(已在 main):FastAPI + SQLAlchemy/SQLite、五个抽象接口、ScanService(文件夹扫描登记)、IngestRecord(含 `indexed`)、前端三 tab + PendingList + 禁用的「建立索引」。
- **约定**:不出现任何前身原型代号;Python 3.11 venv(无额外全局工具);git 身份 `ep1sode-33`。

---

## 1. 关键决策(已敲定)

1. **BGE-M3 进程内**(选项 A):`FlagEmbedding`/`sentence-transformers` 装进后端 venv(带 torch),首次索引下载模型(~2.3GB)。自包含、真本地;嵌入在工作线程跑,不堵 FastAPI。
2. **自写切分器**(不上 LlamaIndex):精确记录 `char_start/char_end`(引用跳回命门)。LlamaIndex 留到 Plan 3 的 RAG/引用引擎。
3. **按项目触发索引**:每个项目"建立索引"处理该项目的待索引文件;后台执行 + 进度可查。
4. **提取时机 = 索引时**:扫描只登记(保持瞬时);点「建立索引」时才 提取→切分→嵌入。
5. **本期媒体类型**:text(.md/.txt)+ **pdf / docx / pptx 本地解析**。**图片、音频本期不索引**(见 §6 延后项)。

---

## 2. 数据流

```
待索引 IngestRecord(indexed=false)
  → MediaProcessor.process(file) 提取文本(text/pdf/docx/pptx)
  → Chunker 切分(每块带 char_start/char_end)
  → EmbeddingProvider.embed(chunk_texts) → 1024 维向量(BGE-M3)
  → VectorStore.upsert(chunks)  写入 Milvus Lite
  → IngestRecord.indexed = true
```
图片/音频等无文本产出的类型:索引时**跳过**,保持 `indexed=false`,UI 标为「需多媒体处理(暂未支持)」。

---

## 3. 组件

### 3.1 MediaProcessor 扩充(索引时调用)
现有注册表新增,统一吐 `MediaResult{text, metadata}`:
- `TextMediaProcessor`(已存在).md/.txt
- `PdfMediaProcessor` — `pypdf`(或 pdfplumber)提取文本
- `DocxMediaProcessor` — `python-docx`
- `PptxMediaProcessor` — `python-pptx`(每张幻灯片文本)
- 图片/音频:**无 processor**(`get_processor` 返回 None)→ 索引跳过

> 提取从 ScanService 移除:**ScanService 改为只登记**(filename/path/hash/size/mtime,不再在扫描时读文本)。文本提取统一在索引时做。

### 3.2 Chunker(自写,字符偏移精确)
`chunk(text: str, *, source_type) -> list[Chunk]`,`Chunk = {text, char_start, char_end}`:
- 递归切:先按空行/段落,再按句子,合并到目标 ~512 token(用简单的字符/词近似,不引入 tokenizer 依赖),相邻块重叠 ~64 token。
- 代码类(.py/.ts 等)按行聚合到目标大小。
- **每块的 `char_start/char_end` 是相对该文件提取文本的字符区间**(供将来高亮/跳回)。

### 3.3 EmbeddingProvider —— 进程内 BGE-M3 实现
落地现有 `EmbeddingProvider` 接口的进程内实现 `BgeM3Embedder`:
- 懒加载模型(首次 `embed` 触发下载/加载);`model_id = "bge-m3"`;维度 1024。
- `embed(texts: list[str]) -> list[list[float]]`(稠密向量)。
- 模型加载与编码放在**工作线程**(`asyncio.to_thread` 或线程池),避免阻塞事件循环。

### 3.4 VectorStore —— MilvusLiteStore 实现
落地现有 `VectorStore` 接口(Milvus Lite,单 `.db` 文件,放 AppConfig 数据目录):
- collection `chunks`,字段:`id`(主键,自增/uuid)· `vector`(FLOAT_VECTOR,1024)· `text`(VARCHAR)· `ingest_record_id`(INT64)· `project_id`(INT64)· `char_start`/`char_end`(INT64)· `source_type`(VARCHAR)· 预留可空 `session_id`/`start_time`/`end_time`/`audio_pos`(本期 folder_scan 文件留空,给将来 session 内容)· `embed_model_id`(VARCHAR)。
- 索引:HNSW,metric `COSINE`。
- 方法:`upsert(chunks)`;`query(vector, filter, k)`(本期实现但主要供 Plan 3 用);`delete_by_record(ingest_record_id)`(重索引/清理用)。

### 3.5 IndexService + 后台任务
`IndexService.index_project(project_id) -> job`:
- 取该项目 `indexed=false` 且**有可用 processor**的文件;逐个:提取→切分→嵌入→`upsert`→`flush` 该文件的 `indexed=true`。
- **后台执行**(FastAPI BackgroundTasks 或线程);维护 job 状态 `{project_id, total, done, status: running|done|error, errors}`,内存即可(本地单用户)。
- 单文件失败(解析/嵌入异常):记进 `errors`,跳过续做,不整体回滚。
- 重索引:先 `delete_by_record` 再重建(防重复 chunk)。

---

## 4. API

- `POST /api/projects/{id}/index` → 启动该项目后台索引任务,返回当前 job 状态(202/200)。
- `GET /api/projects/{id}/index/status` → `{total, done, status, errors}`。
- (沿用)`GET /api/files?project_id=` 反映 `indexed` 翻转。

---

## 5. 前端接线

- 把 PendingList 的「建立索引」由全局禁用占位改为**按项目分组的「建立索引」按钮**(对应已有的按项目折叠分组)。
- 点击 → `POST .../index` → 轮询 `.../index/status`,显示进度(`done/total` + 进度态),完成后刷新待索引/文件列表(文件从「待索引」→「已索引」)。
- **图片/音频文件**:在列表里显示为 **「需多媒体处理(暂未支持)」** 徽章,索引时跳过,不报错。

---

## 6. 明确延后(本期不做,留接口/预留)

- **图片处理**:本地 OCR(便宜,可选)或云端多模态 caption(更强,有成本/联网)——将来在**软件设置的「多媒体处理」栏**让用户配置走哪条;caption/OCR 文本入库再嵌入。本期图片仅登记、不索引。
- **音频处理**:将来交给 **faster-whisper** 转写成 transcript(带时间戳)→ 再嵌入(与采集/Plan 4 衔接,届时填充 schema 里预留的 `start_time/audio_pos`)。本期不做。
- **「多媒体处理」设置面板**:配置 OCR/云模型/开关。本期不做。
- **混合检索 / Rerank / Agentic RAG / 带引用对话**:Plan 3。本期只把向量灌进 Milvus(query 方法预留)。
- 切分用 tokenizer 精确计数、LlamaIndex node parser:本期用近似;Plan 3 视需要再升级。

---

## 7. 依赖新增(venv)

`FlagEmbedding`(或 `sentence-transformers`,带 torch)· `pymilvus`(含 milvus-lite)· `pypdf` · `python-docx` · `python-pptx`。首次索引下载 BGE-M3。

---

## 8. 测试要点(TDD)

- Chunker:字符偏移正确、重叠、空文本、代码按行;`char_end-char_start` 与子串一致。
- MediaProcessor:pdf/docx/pptx 提取出预期文本;未知类型返回 None。
- EmbeddingProvider:可用一个**假实现**(返回固定维度向量)单测 IndexService,避免测试下真跑 torch/下模型;另有一个(可选、标记 slow)真 BGE-M3 冒烟。
- MilvusLiteStore:upsert + query 往返;按 project_id 过滤;delete_by_record。
- IndexService:用假 Embedder + 临时 Milvus,验证 提取→切分→嵌入→入库→`indexed` 翻转;单文件失败被跳过并记 errors;图片/音频被跳过。
- API:index 启动 + status 进度;未知项目 404。
