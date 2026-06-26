from __future__ import annotations

import json
from collections.abc import Iterator

from epictrace.agent.citations import build_citations
from epictrace.agent.prompts import GENERATE_SYS, format_chunks
from epictrace.retrieval.types import RetrievedChunk

# 与 ChatService.CHAT_SYS 同义:池空(寒暄)走普通聊天作答,不套【资料】框架。
CHAT_SYS = "你是有帮助的助手,用中文简洁作答。"
# 接地闸门(#144):检索后判【资料】是否真含答案。判失败/拿不准 → 放行(保守偏可答)。
ANSWERABLE_SYS = "你是接地判断器,只输出一个英文词 yes 或 no,不要解释。"
_REFUSAL = "根据已检索到的资料,没有找到能够回答这个问题的信息,因此无法作答。"


def _is_answerable(llm, question: str, pool: list[RetrievedChunk]) -> bool:
    """检索后接地闸门:判断【资料】是否真含有能**直接回答**问题的信息,修『资料没有却照编』的
    refusal 弱点(eval 实测:相似但不含答案的 chunk 会诱发模型编造带引用的假答案)。
    **保守偏可答**:只有资料明显不含答案(明确回 no)才判不可答 → 避免误杀可答题;
    拿不准/judge 失败 → True(放行让生成,模型仍可自行 hedge)。"""
    try:
        verdict = llm.complete([
            {"role": "system", "content": ANSWERABLE_SYS},
            {"role": "user", "content": (
                "下面【资料】是否包含可以**直接回答**该问题的信息?\n"
                "只回一个词:yes(资料里能找到答案)或 no(资料完全没有该问题的答案)。拿不准回 yes。\n\n"
                f"问题:{question}\n\n【资料】\n{format_chunks(pool)}")},
        ])
    except Exception:  # noqa: BLE001 — 判失败 → 放行(保守偏可答)
        return True
    return not (verdict or "").strip().lower().startswith("no")


def stream_final_answer(llm, question: str, pool: list[RetrievedChunk], *,
                        history: list[dict], attached_names: list[str]) -> Iterator[dict]:
    """循环结束后的唯一一次作答(丢弃工具对话历史):有池→GENERATE_SYS+编号【资料】带 [n];
    池空→CHAT_SYS 直答。流式吐 token,收尾用 build_citations(answer, 池) 复用引用命门。
    有池但接地闸门判【资料】不含答案 → 直接拒答(不调生成、不编造、不带引用)。"""
    if pool and not _is_answerable(llm, question, pool):
        yield {"event": "token", "data": _REFUSAL}
        yield {"event": "citations", "data": "[]"}
        yield {"event": "_answer", "data": _REFUSAL}  # 内部:供落库
        return
    if pool:
        note = ""
        if attached_names:
            note = (f"(用户在本次对话附加了文件:{'、'.join(attached_names)};"
                    f"下方【资料】已包含这些附件的相关内容,请据此作答,不要说未收到文件。)\n\n")
        messages = [{"role": "system", "content": GENERATE_SYS}]
        messages.extend(history)
        messages.append({"role": "user",
                         "content": f"{note}问题:{question}\n\n【资料】\n{format_chunks(pool)}"})
    else:
        messages = [{"role": "system", "content": CHAT_SYS}]
        messages.extend(history)
        messages.append({"role": "user", "content": question})

    parts: list[str] = []
    for tok in llm.stream(messages):
        parts.append(tok)
        yield {"event": "token", "data": tok}

    answer = "".join(parts)
    citations = build_citations(answer, pool) if pool else []
    yield {"event": "citations", "data": json.dumps(citations, ensure_ascii=False)}
    yield {"event": "_answer", "data": answer}  # 内部:供 ChatService 落库(不发给前端)
