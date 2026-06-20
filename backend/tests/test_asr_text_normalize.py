"""繁体→简体规整(ChineseSimplifier / OpenCC t2s)。"""
import pytest

from epictrace.asr.text_normalize import ChineseSimplifier


def test_simplifier_converts_traditional():
    pytest.importorskip("opencc")
    s = ChineseSimplifier()
    assert s.convert("這四個飄誤應該對麼") == "这四个飘误应该对么"
    assert s.convert("謝謝大家") == "谢谢大家"   # 转简后才能被幻觉过滤的简体表命中
    assert s.convert("") == ""


def test_simplifier_degrades_gracefully_without_opencc():
    """opencc 不可用 / 运行异常 → 恒等降级返回原文,绝不抛。"""
    s = ChineseSimplifier()
    s._cc = None  # 模拟 opencc 缺失
    assert s.convert("這個") == "這個"
    assert s.convert("") == ""
