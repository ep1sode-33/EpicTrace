export const meta = {
  name: 'rag-eval-judge',
  description: 'Judge a RAG-eval generation dump with off-family Opus subagents that Read per-item files (no krill proxy)',
  phases: [{ title: 'Judge', detail: 'one subagent per item reads its file and scores 5 judge metrics' }],
}

// args = { dir: "/abs/.../gen_items", items: [{ id, q_type, n_cited }] }
// 每个子代理用 Read 取 dir/<id>.json(含 context 等大字段),只回原始判断;分数公式在此 JS 算,
// 与 harness 一致(null = nan)。args 极小,context 不进主上下文也不进 args。
const parsed = typeof args === 'string' ? JSON.parse(args) : args
const { dir, items } = parsed || {}
if (!dir || !Array.isArray(items) || items.length === 0) {
  throw new Error('args 需为 { dir, items: [{id, q_type, n_cited}] }')
}

const SCHEMA = {
  type: 'object',
  additionalProperties: false,
  required: ['faithfulness_claims', 'relevancy', 'answer_claims_supported',
             'reference_claims_covered', 'citation_supported', 'is_refusal'],
  properties: {
    faithfulness_claims: {
      type: 'array',
      items: { type: 'object', additionalProperties: false, required: ['supported'],
               properties: { text: { type: 'string' }, supported: { type: 'boolean' } } },
    },
    relevancy: { type: 'number' },
    answer_claims_supported: { type: 'array', items: { type: 'boolean' } },
    reference_claims_covered: { type: 'array', items: { type: 'boolean' } },
    citation_supported: { type: 'array', items: { type: 'boolean' } },
    is_refusal: { type: 'boolean' },
  },
}

function judgePrompt(id) {
  return [
    `先用 Read 工具读取文件:${dir}/${id}.json`,
    '它含字段:question、reference_answer、answer、context、cited_texts(数组)。',
    '你是严格的 off-family RAG 评测裁判(被测系统是 DeepSeek,你不是它)。只做判断,逐条给布尔/分数,经 StructuredOutput 返回 6 字段:',
    '1) faithfulness_claims:把 answer 拆成原子声明,逐条判断是否能由 context 蕴含支撑 → [{text, supported}]。',
    '2) relevancy:answer 在多大程度上直接回答了 question,0~1 小数(1=完全切题)。',
    '3) answer_claims_supported:answer 每条原子声明是否被 reference_answer 支撑 → [bool]。',
    '4) reference_claims_covered:reference_answer 每条原子声明是否被 answer 覆盖 → [bool]。',
    '5) citation_supported:对 cited_texts 每条,是否真支撑 answer 中引用它的那句论述 → [bool](顺序对应;cited_texts 为空给 [])。',
    '6) is_refusal:answer 是否在恰当地表示"资料中没有/无法回答"(拒答) → bool。',
  ].join('\n')
}

const meanBool = (xs) => (xs && xs.length ? xs.filter((x) => x === true).length / xs.length : null)
const clamp01 = (x) => { const v = Number(x); return Number.isNaN(v) ? null : Math.max(0, Math.min(1, v)) }

function scores(meta, j) {
  if (!j) return null
  const fc = j.faithfulness_claims || []
  const faith = fc.length ? fc.filter((c) => c.supported === true).length / fc.length : null
  const p = meanBool(j.answer_claims_supported), r = meanBool(j.reference_claims_covered)
  let corr
  if (p === null || r === null) corr = null
  else if (p + r === 0) corr = 0.0
  else corr = (2 * p * r) / (p + r)
  const cs = j.citation_supported || []
  const citeF = meta.n_cited > 0 ? (cs.length ? cs.filter((x) => x === true).length / cs.length : null) : null
  const m = { faithfulness: faith, answer_relevancy: clamp01(j.relevancy),
              answer_correctness: corr, citation_faithfulness: citeF }
  if (meta.q_type === 'negation') m.refusal_correctness = j.is_refusal === true ? 1.0 : 0.0
  return m
}

const out = await pipeline(
  items,
  (it) => agent(judgePrompt(it.id), { label: `judge:${it.id}`, phase: 'Judge', schema: SCHEMA })
    .then((j) => ({ id: it.id, judge: scores(it, j) })),
)

const ok = out.filter(Boolean)
log(`judged ${ok.length}/${items.length} items`)
return ok
