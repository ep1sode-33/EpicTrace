from epictrace.services.budget import estimate_tokens, fulltext_budget, fits_fulltext


def test_estimate_tokens_is_conservative_char_based():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 2          # 4 字符 / 2 ≈ 2 token(向上取整)
    assert estimate_tokens("abcde") == 3


def test_fulltext_budget_is_half_window():
    assert fulltext_budget(32768) == 16384
    assert fulltext_budget(0) == 0


def test_fits_fulltext_respects_budget_and_used():
    win = 1000                                   # 预算 = 500 token ≈ 1000 字符
    assert fits_fulltext("a" * 800, win) is True            # ~400 token ≤ 500
    assert fits_fulltext("a" * 1200, win) is False          # ~600 token > 500
    # 已用预算累加:再来 ~400 token 会超
    assert fits_fulltext("a" * 400, win, used_tokens=400) is False
