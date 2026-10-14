"""Ожидания отдельных формул из tests/unit, без копирования формул в тесты."""

import pytest
from conftest import unit_cases

from sov.engine.rules import evaluate_rule

CASES = list(unit_cases())


@pytest.mark.m1
@pytest.mark.parametrize("rule,row", [(r, c) for r, c, _ in CASES], ids=[i for _, _, i in CASES])
def test_rule(rule, row):
    expected_keys = {
        "weights": {"participates", "weight"},
        "normalization.project": {"expected", "expected_size"},
        "normalization.rp": {"x", "load_pct", "per_project", "in_range"},
        "mp_load": {"expected_per_project"},
    }.get(rule, {"expected"})
    inputs = {k: v for k, v in row.items() if k not in expected_keys | {"why"}}
    result = evaluate_rule(rule, inputs)
    expected = {k: row[k] for k in expected_keys}
    assert result.keys() >= expected.keys()
    for key, value in expected.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            assert result[key] == pytest.approx(value, rel=0, abs=1e-9)
        else:
            assert result[key] == value
