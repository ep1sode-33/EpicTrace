"""判官限流/过载处理:429/5xx → 退避重试;持续失败 → None(指标记 nan,不记 0)。
backoff_base=0 让测试不真睡。"""
from scripts.rag_eval.judge_client import AnthropicJudge, JudgeConfig

_CFG = JudgeConfig(base_url="http://x", api_key="k", model="claude-opus-4-8")


def test_retries_past_rate_limit():
    calls = []

    def transport(url, headers, body):
        calls.append(1)
        if len(calls) == 1:
            return 429, {}  # 第一次限流
        return 200, {"content": [{"type": "text", "text": '{"v": 1.0}'}]}

    j = AnthropicJudge(_CFG, transport=transport, retries=4, backoff_base=0)
    assert j.judge_json("sys", "user") == {"v": 1.0}
    assert len(calls) == 2  # 退避后重试拿到 200


def test_gives_up_on_persistent_rate_limit():
    def transport(url, headers, body):
        return 429, {}

    j = AnthropicJudge(_CFG, transport=transport, retries=2, backoff_base=0)
    assert j.judge_json("sys", "user") is None  # 始终 429 → 放弃 → None


def test_recovers_from_transport_exception():
    calls = []

    def transport(url, headers, body):
        calls.append(1)
        if len(calls) == 1:
            raise ConnectionError("boom")
        return 200, {"content": [{"type": "text", "text": '{"v": 0.5}'}]}

    j = AnthropicJudge(_CFG, transport=transport, retries=4, backoff_base=0)
    assert j.judge_json("sys", "user") == {"v": 0.5}
