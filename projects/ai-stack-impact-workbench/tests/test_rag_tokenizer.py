from policy_impact.rag.lexical import tokenize


def test_tokenize_keeps_chinese_terms():
    tokens = tokenize("\u653f\u7b56ABC\u4f01\u4e1a")
    assert tokens == ["\u653f\u7b56", "abc", "\u4f01\u4e1a"]
