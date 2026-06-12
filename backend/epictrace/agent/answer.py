from __future__ import annotations

import json
from collections.abc import Iterator

from epictrace.agent.citations import build_citations
from epictrace.agent.prompts import GENERATE_SYS, format_chunks
from epictrace.retrieval.types import RetrievedChunk

# 与 ChatService.CHAT_SYS 同义:池空(寒暄)走普通聊天作答,不套【资料】框架。
CHAT_SYS = "你是有帮助的助手,用中文简洁作答。"


def stream_final_answer(llm, question: str, pool: list[RetrievedChunk], *,
                        history: list[dict], attached_names: list[str]) -> Iterator[dict]:
    """循环结束后的唯一一次作答(丢弃工具对话历史):有池→GENERATE_SYS+编号【资料】带 [n];
    池空→CHAT_SYS 直答。流式吐 token,收尾用 build_citations(answer, 池) 复用引用命门。"""
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
