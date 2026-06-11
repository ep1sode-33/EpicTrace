ROUTE_SYS = (
    "判断回答用户问题是否需要检索该项目的资料库。只回一个词:retrieve 或 direct。"
    "打招呼/常识/与项目资料无关 → direct;涉及项目内容 → retrieve。"
)
GRADE_SYS = "你判断给定资料是否足以回答问题。只回一个词:sufficient 或 insufficient。"
REWRITE_SYS = "资料不足。基于问题与已有资料的缺口,改写出一个更可能检索到答案的中文查询,只回查询本身。"
GENERATE_SYS = (
    "你是基于资料作答的助手。只用提供的【资料】回答,凡用到某条资料就在句末标注其编号 [n]"
    "(n 为资料序号,可多个);不要编造资料没有的内容;用中文。"
)


def format_chunks(chunks) -> str:
    return "\n\n".join(f"[{i + 1}] {c.text}" for i, c in enumerate(chunks))
