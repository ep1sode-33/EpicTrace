# RAG 评测台架(RAG Evaluation Harness)设计

> 状态:设计已评审,待落实现计划(writing-plans)。
> 语言约定:正文简体中文;代码 / 路径 / 命令 / 标识符 / 指标名保持英文。

## 1. 背景与目标

EpicTrace 的 RAG 子系统(Plan 2–6)已打通:索引管线(chunker → BGE-M3 embedding → Milvus Lite)、混合检索(dense + jieba/BM25 → RRF → BGE-reranker-v2)、LangGraph ReAct Agent(工具检索 + 引用 `[n]`,带 char-offset 回跳)。但**至今没有任何手段量化它好不好**——改个检索参数、换个 prompt,全凭感觉。

**首要目的:做一个可重复的「调参 / 迭代台架」(tuning harness)。** 改检索参数 / prompt / 模型后,跑同一套题,看指标 **delta**,用数据驱动优化。次级收益:这套地基同时也能产出一份可信的、用行业标准指标(RAG triad + 检索指标)写的评测报告。

**核心原则贯穿全程:没有基线,别动刀。** 台架是「迭代回路」的量尺;先冻结现状、量出基线,再把每个修复当成带 delta 的受控实验。已知的实现弱点(见 §11)不在台架之前修,而是作为台架的「第一批客户」。

### 设计取向(为什么自建,不套框架)

评测引擎自建精简版,**实现行业标准指标但不引入 RAGAS/DeepEval 依赖**。理由:

- 最值钱的两块——**精确检索指标**和 **char-offset 引用准确率**——框架根本测不了(RAGAS 假设你没有标注,用 LLM *估*上下文相关性;我们有 gold 跨度,能算**真值**,比它准),本来就得自建。
- 剩下的生成 judge,方法论是公开的;复用项目已有的 `OpenAICompatLLM`(BYOK)写中文可控的 judge,零新依赖、贴 BYOK、无版本耦合,契合 repo 干净依赖 + 现成 `scripts/asr_eval.py` 范式。
- 报告**沿用标准指标的定义与命名**(faithfulness / answer relevancy / context precision / answer correctness),credibility 不丢。

## 2. 非目标(明确不做)

- **CI 阈值守门**:确定性的 `retrieve` 子命令以后可升级成 CI smoke,本期不做。
- **Web dashboard**:markdown + 控制台报告够用。
- **Langfuse 接入**:以后可包一层,台架不依赖它。
- **自动调参搜索器**:先手动 sweep;optimizer 是后话。
- **公开 QA benchmark 当主 golden set**:benchmark 仅用于补「自产不了的薄片」,隔离、单列、不进主分(见 §4.3)。

## 3. 总体架构

```
golden.jsonl ──┐
               ├─►[① retriever-isolation]──► 检索指标(确定性, 内循环)
固定分层语料 ──┤
(已索引)       ├─►[② agentic retrieve]──┐
               └─►[③ final answer+cites]─┴─► 生成 / 引用 judge(外循环)
                                          ──► 分片 + 分阶段报告 + Δ
```

### 3.1 三个测量点 = 分阶段归因

pipeline 有「检索器」和「Agent(ReAct,LLM 自决调几次工具 / 改写 query)」两层。为把失败归到具体层,在三个点取数:

1. **检索器单测(point ①)**:raw 问题 → `HybridRetriever.retrieve`,绕开 Agent。确定性、免 LLM。**sweep 参数的内循环。**
2. **Agent 实际检索(point ②)**:跑真 `ChatService`/Agent,收集其工具调用**合并**捞到的 chunk。衡量 Agent 决策有没有把 gold 跨度捞上来。
3. **最终答案(point ③)**:Agent 的答案 + 引用。LLM judge 算生成质量 + 引用。

归因示例:「① 命中 gold,但 ③ 没引它」→ 锅在 **Agent/生成**,不在检索器。

### 3.2 两层循环

- **内循环**(快、免 LLM、零成本):只跑 ①,改 `k/dense_n/fuse_m/top_k/RRF/权重`,秒级看检索 delta。
- **外循环**(慢、烧 LLM、periodically):跑 ②③,全套生成 + 引用 judge。
- 边界:**改 chunker(`target/overlap`)动的是入库物 → 需重建索引,属外循环**;改检索参数复用索引,属内循环。runner 按此画死(§7)。

## 4. 评测语料

### 4.1 分层维度(广度靠刻意分层,不靠堆量)

报告按格子分片出分,而非一个糊涂总分:

- **领域 domain**:学习(讲义 / 论文 / 笔记)× 工作(技术文档 / 纪要 / PRD / 代码)
- **文档类型 doc_type**:md / pdf(走 MinerU)/ docx / pptx / 代码(py/java/c/h)/ txt / html / srt 字幕
- **语言 lang**:zh / en / 中英混
- **问题类型 q_type**:single_hop / multi_hop / negation(否定·不可答)/ table_numeric / timeline

### 4.2 真实数据源(本机只读,绝不改原件)

主语料取自用户本机真实课程 / 培训材料(只读,**原件一字节不动**):

- `CS 2505`(Computer Organization:pdf / pptx / c / txt 富矿)与 `CS 2506`(含 pptx/pdf/html/txt/**srt 字幕**)—— 主力,doc_type 广度拉满。
- `TX AI培训`(docx/pdf/pptx/md)—— 补**中文 + work 味**。
- `CS 2104 / CS 2114`(py / java / md / txt)—— 补代码;`CS 2104` 大半是 `.git` 内部文件,只取 py/md。
- **媒体(mp4/m4a)排除**——那是 ASR 评测的范围,RAG 语料只取文本类。

**语料处置(守「别改里面的」)**:harness **只读**原目录,**拷一份分层切片到冻结目录** `backend/eval-data/`(本地、gitignored),入库走这份冻结拷贝;原件不参与任何写路径。git 里**只 check in** `golden.jsonl` + 一份 `manifest.jsonl`(相对路径 + sha256 + slice 标签)。理由:几百 MB 二进制 + 可能含个人作业/成绩,不该进 git;派生 golden 足够复现。

### 4.3 薄片补语料(优先级)

已知 skew:语料偏学习 / CS / 英文,中文与 work-纯偏薄。补语料**只针对「报告显示又低又样本少」的片**(分不清真差还是噪声那种);「又低但样本够」= 真代码问题,去 §11 修,不是补语料。来源按真实优先:

1. **用户自己的更多真实素材**(中文笔记 / 培训 / work 文档)—— 最真。
2. **真实公开中文 / work 文档当 KB 入库**(非 QA benchmark):中文开源项目文档、技术博客、公开 PRD/白皮书/年报。
3. **公开 benchmark 补片(自产不了时的标准解法)**:选**真·检索/RAG 语料**(非单段 MRC)——中文 RAG 专用 `CRUD-RAG` / `DomainRAG`;中文检索带标签 `DuReader_retrieval` / `T2Ranking`;work-ish 长中文 金融 `FinanceIQ`/`AlphaFin`、法律 `LeCaRD`/`CAIL`。两种用法:**(a) 借标签**——直接拿「Q + gold passage」喂确定性检索指标;**(b) 借文档**——只拿文档当语料种子,跑我们自己的 golden 合成。
4. **合成文档**:最后兜底,小批量,标死 `provenance=synthetic-doc`,报告单列(文档+题皆 LLM 生成有自指风险,缺真实文档的脏乱,不能与真实片混着下结论)。

**纪律**:benchmark / 合成片一律 `source=benchmark:<name>` / `synthetic-doc` 标记,**单独成片、单独出分,绝不进主聚合分、绝不替没覆盖的片做调参决策**;benchmark 采样几百条即可,不整集吞;license 走研究/CC、本机不分发,风险低。

## 5. Golden set 构建

### 5.1 数据格式(`golden.jsonl`,冻结、checked-in)

```json
{
  "id": "g0042",
  "question": "k8s 里 Pod 一直 Pending 最常见的原因是什么?",
  "gold_spans": [{"ingest_record_id": 12, "doc_char_start": 1862, "doc_char_end": 2090}],
  "reference_answer": "...",
  "slices": {"domain": "work-tech", "doc_type": "pdf", "lang": "zh", "q_type": "single_hop"},
  "provenance": "synthetic",
  "source": "own",
  "corpus_version": "v1"
}
```

### 5.2 关键设计:gold 用「文档字符范围」,不绑 chunk id

`gold_spans` 记成**源文档(抽取文本)里的 char 区间**。检索到的 chunk「命中」= 同 `ingest_record_id` 且 `[char_start,char_end]` 与 gold 区间**重叠**。好处:**改 chunker 时 chunk 偏移会变,但 gold 文档区间不变** → 同一套 golden set 照样测重切块后的检索,chunker sweep 纳入同一台架,无需重标;**只有改语料内容才需重生成 golden**。合成时让 LLM **回引支撑句**,把 gold 收窄到真正承载答案的句子,使 context-precision 有区分度。

### 5.3 构建管线(5 步)

1. **冻结语料 + 真实索引**:`eval-data/` 过真实入库管线;gold 区间从真实抽取文本取偏移。
2. **分层采样**:按 slice 格子均匀抽源文本片段,保证长尾格子不空。
3. **LLM 合成**:每片 → 「一道只能由它回答的自然题 + 参考答案 + 回引支撑句」;支撑句映射回文档偏移 = gold 跨度。一次拿两样:检索真值 + 参考答案。
4. **自动过滤**(挡烂题):**可答性**(另一 LLM 只凭该片作答须与参考一致)· **防泄漏**(题不逐字抄原文)· **自包含**(不许「这段/上文」指代)· **语义去重**。
5. **人工精修(轻量)**:`review-golden` CLI 逐题 accept/edit/reject,目标留 ~60–70%。机器干重活,人只做减法。

### 5.4 手写难题补充(~10–15 题)

单 chunk 合成器造不出、且正好压广度盲区的,手写补,`provenance=hand`:多跳/综合(`gold_spans` 多个)· 否定题(参考=拒答)· 表格/数值· 中英混/跨语言。

## 6. 指标引擎

**命中定义**贯穿:检索 chunk「命中」gold = 同 `ingest_record_id` 且 char 区间与某条 gold 跨度重叠。

### 6.A 检索指标(确定性 · 免 LLM · 内循环)

对每题(gold_spans + top-k 排序结果):

- **recall@k** — 单跳:top-k 有任一命中=1 否则 0;多跳:覆盖率 = 命中 gold 跨度数 / 总 gold 跨度数。两者都报(`any@k` / `coverage@k`)。
- **MRR** — 第一个命中 chunk 名次的倒数 `1/rank`,跨题取均值。
- **nDCG@k** — `DCG = Σ gain_i / log2(i+1)`,`gain` 取命中重叠比例;`nDCG = DCG / IDCG`。
- **context-precision@k** — top-k 命中占比(信噪比);另给**有序版**(命中越靠前得分越高),衡量喂给 LLM 的上下文干不干净。

在 point ①(检索器单测)与 point ②(Agent 合并检索)各算一遍,同一套函数。

### 6.B 引用指标(char-offset 独门)

答案 `[n]` → `build_citations` → 每条引用带 char 跨度:

- **引用合法率 citation_validity** — `[n]` 映射到合法 chunk(`1≤n≤len`)的占比;抓模型吐没吐**编造引用号**(测丢弃前的原始吐出率)。确定性。
- **引用准确率 citation_accuracy** — 被引 chunk 跨度命中 gold 的占比 = 引没引**对源**。确定性(用 gold)。
- **引用忠实度 citation_faithfulness** — 逐条:被引 chunk 文本**是否真支撑**那句(LLM judge,不需 gold)。抓「引了但那块没这么说」。

> 准确率(引对 gold 源,要 gold)vs 忠实度(被引块支撑该句,要 judge),两者一起看才完整。

### 6.C 生成指标(LLM judge · 外循环)

judge 吃 (question, retrieved-context, generated-answer, reference-answer):

- **faithfulness(无幻觉)** — 答案拆成原子声明,逐条问「检索上下文是否蕴含」,`score = 被支撑数/总数`(声明分解法,中文 prompt)。不需 gold。
- **answer_relevancy(答非所问)** — 答案是否对题,judge 直评 0–1(可选嵌入法:反推 N 个该答案能回答的问题,与原题算余弦)。不需 gold。
- **answer_correctness(对不对)** — vs `reference_answer` 的语义正确性,声明级 F1(答案声明 ∩ 参考声明 → P/R/F1)。需 gold。
- **refusal_correctness(否定题专项)** — 对不可答/否定题(参考=拒答),系统有没有正确说「没有/无法回答」而非硬编。专抓幻觉失败模式。

### 6.D Judge 基础设施

- **judge 模型**:BYOK 里最强 profile,`temperature=0`,**结构化 JSON 输出**(声明表 + 裁决),失败重试,**判不出标 NaN 不标 0**(judge 超时 ≠ 不忠实)。复用 `OpenAICompatLLM`,零新依赖。
- **判官 ≠ 选手**:judge 模型尽量别与被测生成同一个(避免 self-preference bias),报告标明 judge 模型。
- **缓存**:judge 结果按 `(metric, question_id, answer_hash, context_hash, judge_model)` 落盘;相同 run 重跑不付费,只有答案/上下文变了才重判。

### 6.E 输出

每题一行 JSONL(全指标 + 命中明细 + judge 理由),便于钻失败题。

## 7. Runner / Config / 索引

### 7.1 Config(一份 run 的所有旋钮)

dataclass/YAML:**retrieval**(`k/dense_n/fuse_m/top_k/RRF-k0/dense-sparse 权重/sparse 开关`)· **chunker**(`target/overlap`)· **generation**(Agent 路径 tool-calling vs fallback / prompt 变体 / model profile)· **eval**(跑哪些 slice / `@k` 的 k / judge profile)。config 算稳定 hash,run 产物按它归档。

### 7.2 两层 runner

- `rag-eval retrieve`(内循环):载冻结索引 → 每题跑 point ① → 确定性检索指标。
- `rag-eval run`(外循环):内循环 + point ②③ → 生成 + 引用 judge。
- `rag-eval index`:从 `eval-data/` 建冻结索引。每个 chunker 配置 → 自己的索引快照(按 chunker-hash 归档),A/B 切块尺寸不互相覆盖;检索参数 sweep 复用同一索引。

## 8. 报告 + run-vs-run delta

- `rag-eval report <run>`:分片 × 分阶段表(domain×doc_type×lang×q_type),主聚合**只算 `source=own` 的片**,benchmark/synthetic 片单列。markdown + 控制台。
- `rag-eval diff <runA> <runB>`:逐指标 delta、分片、▲▼ 标回归/改善、高亮移动的片。**「改完跑同题看涨跌」的兑现点。**
- 产物:`runs/<config-hash>-<seq>/{config.json, per_question.jsonl, summary.json, report.md}`(`runs/` gitignore;留几个 baseline summary 进 git 当参照)。

## 9. 落地形态

- `backend/scripts/rag_eval/` 包——**手动跑、不进 CI、懒导入重依赖**(FlagEmbedding/Milvus/LLM),沿用 `scripts/asr_eval.py` 风格。子命令:`index / gen-golden / review-golden / retrieve / run / report / diff`。
- **复用真生产组件**(`HybridRetriever` / `ChatService` / `IngestService` / `OpenAICompatLLM`)——测真管线,不另写会漂移的副本。
- `backend/tests/fixtures/rag_eval/`:`golden.jsonl` + `manifest.jsonl`(进 git);`backend/eval-data/` 语料拷贝 + `backend/scripts/rag_eval/runs/`(gitignore)。

## 10. 台架自测(评测可信的前提)

台架也有 bug,TDD 其确定性核:

- **指标纯函数**(recall@k/MRR/nDCG/context-precision/重叠命中/citation_accuracy):手搓 fixture 单测(gold `[10,20]`、chunk `[15,25]`→命中;`[30,40]`→未命中)。纯函数,好测。
- **judge prompt 聚合**:**假 LLM** 回固定 JSON → 断言指标聚合对(不碰真模型,守「测试绝不起真模型/真 ASR worker」规矩)。
- **runner smoke**:假 embedder/store/LLM → 跑通 + 写产物。
- 真模型那部分维持 opt-in / 手动。

## 11. 实现弱点清单(台架要照出的,带 file:line)

这些是评测**要去量化**的对象,不是评测前要修的前置。每条接「评测怎么照出来」:

1. **稀疏检索每查询重建全项目 BM25**(`retrieval/sparse.py:16-24`):`list_by_project` 拉全部 → jieba 切全部 → 每次 query 新建 `BM25Okapi`,O(语料规模)/查询,无持久倒排。→ 评测把**延迟 vs 语料规模**当一类指标盯;架构/性能问题,不影响质量分,可独立重构,但等台架确认它在目标语料规模下真咬人再动。
2. **中文 chunk 尺寸标定错**(`indexing/chunker.py:6`):按「~4 字符/token」算(英文比例),中文约 1–1.5 字/token,`1800 字` 实际 ~1200–1800 token,是目标 2–3 倍 → 中文块过大、检索粒度粗、引用跨度粗。→ 评测「中文长文档」片的 precision + citation_accuracy 会显著低于英文片,直接定位。
3. **切块对结构视而不见**(`indexing/chunker.py` `_BOUNDARIES`):只认句号/换行,不用 MinerU 的 markdown 标题/表格结构,跨标题硬切、表格切碎。→ 评测「带表格 / 层级标题」题会塌,提示上结构感知切块。
4. **终排无多样性(无 MMR)**(`retrieval/rerank.py:36`):rerank 后纯按分取 top_k,叠加 200 字 chunk 重叠,同文档相邻块易霸榜,挤掉异处证据。→ 坑多跳/综合题;评测 `multi_hop` 片暴露,提示加 MMR/去冗。
5. **BGE-M3 学习型稀疏 + ColBERT 未用**(`embedding/bge_m3.py:28` 只取 `return_dense`),另起 jieba/BM25。→ 不是 bug,是该被量化的分叉:jieba-BM25 vs M3-sparse 跑同套题比 recall(config 开关)。

小项:dense 真实余弦分被丢、只用名次喂 RRF(`retrieval/dense.py:15`,对 RRF 自洽但失去阈值/标定能力);ANN 召回(HNSW `ef`)是该被评测验证的旋钮(`vectorstore/milvus_lite.py`,存疑未定论)。

## 12. 风险与取舍

- **生成/judge 随机性**:retrieval 确定;生成 + judge 随机 → judge `temperature=0`;噪声指标可 N-sample 报 mean±std;缓存令重跑稳定且省钱。
- **judge 偏差**:judge ≠ 选手 + 标明模型;中文 prompt 需实测校准。
- **语料 skew**:学习/CS/英文重,中文/work 薄 → 分片报告暴露,定向补(§4.3),不追满每格。
- **benchmark 域外性**:benchmark 片只衡量域外鲁棒性,隔离不进主分。
- **gold 与 corpus 版本绑定**:换语料须重生成 golden(`corpus_version` 守门)。

## 13. 验收标准(本设计实现到什么程度算完成)

- `eval-data/` 冻结语料 + `manifest.jsonl` + `golden.jsonl`(自产分层 + 手写难题 + 必要的 benchmark 补片),原件零改动。
- `index / gen-golden / review-golden / retrieve / run / report / diff` 七个子命令可用,复用真生产组件。
- 检索 / 引用 / 生成三族指标按 §6 定义实现;judge 复用 `OpenAICompatLLM` + 缓存。
- 报告分片 × 分阶段,`diff` 出 run-vs-run delta。
- 指标纯函数 + judge 聚合 + runner smoke 有单测(假 LLM,不起真模型)。
- 跑出一份**基线 run**,把 §11 的弱点各自量化到具体分片(基线即「第一批客户」的起点)。
