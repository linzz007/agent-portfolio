import pytest

from policy_impact.company_wiki.updater import _replace_fact_value


def test_replace_fact_value_only_changes_target_fact_block():
    text = """# demo

## FACT: a
- value: 8
- importance: 5

## FACT: b
- value: 8
- importance: 3
"""
    updated = _replace_fact_value(text, target_fact_id="a", old_claim="8", new_claim="10")
    assert "## FACT: a\n- value: 10" in updated
    assert "## FACT: b\n- value: 8" in updated


def test_replace_fact_value_rejects_wrong_old_claim():
    text = """## FACT: a
- value: old
"""
    with pytest.raises(ValueError):
        _replace_fact_value(text, target_fact_id="a", old_claim="missing", new_claim="new")
