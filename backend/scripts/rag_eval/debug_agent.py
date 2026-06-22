"""Debug(手动跑):对指定 golden 题,看 agent 到底搜没搜、用什么 query、池里有没有 gold。
用法: ./.venv/bin/python -m scripts.rag_eval.debug_agent <gid> [<gid> ...]
"""
from __future__ import annotations

import sys

from epictrace.agent.react import run_react_loop
from epictrace.agent.tools import ChunkAccumulator, build_tools
from scripts.rag_eval import wiring
from scripts.rag_eval.golden import load_golden
from scripts.rag_eval.metrics import chunk_hits


class _LogRetriever:
    def __init__(self, inner):
        self._inner = inner
        self.queries: list[str] = []

    def retrieve(self, **kw):
        self.queries.append(kw.get("query"))
        return self._inner.retrieve(**kw)


def main(argv=None) -> int:
    ids = (argv if argv is not None else sys.argv[1:])
    golden = {g.id: g for g in load_golden("eval-data/golden.jsonl")}
    inner = wiring.build_retriever(2)
    factory = wiring.build_chat_model_factory()
    for gid in ids:
        g = golden[gid]
        log = _LogRetriever(inner)
        acc = ChunkAccumulator()
        tools = build_tools(retriever=log, project_id=2, focus_ids=[],
                            attachment_retriever=None, conversation_id=0,
                            indexed_ext_ids=[], reference_texts={}, fulltext_ids=[])
        status = run_react_loop(factory(), tools, acc, g.question,
                                history=[], attachment_manifest="")
        pool = acc.chunks
        rank = next((i + 1 for i, c in enumerate(pool) if chunk_hits(c, g.gold_spans)), None)
        print(f"\n{gid} [{g.slices.get('lang')}/{g.slices.get('doc_type')}] {g.question[:40]}")
        print(f"  status={status}  searches={log.queries}  pool={len(pool)}  gold_rank={rank}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
