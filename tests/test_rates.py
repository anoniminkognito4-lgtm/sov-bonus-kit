"""Проверка истории ставок независимо от постоянной ставки образцов."""

import pytest
from conftest import ROOT, read_json

from sov.engine.calculate import calculate_case
from sov.engine.rules.rates import rate_integral


@pytest.mark.parametrize(
    "start,end,expected",
    [
        ("2026-01-01", "2026-01-21", 3),
        ("2026-01-11", "2026-01-21", 2),
        ("2026-01-21", "2026-01-01", -3),
        ("2026-01-01", "2026-01-01", 0),
        ("2025-12-31", "2026-01-21", None),
    ],
)
def test_piecewise_rate_integral(start, end, expected):
    history = [{"date_from": "2026-01-01", "rate": 0.1}, {"date_from": "2026-01-11", "rate": 0.2}]
    actual = rate_integral(start, end, history)
    assert actual == pytest.approx(expected) if expected is not None else actual is None


def test_piecewise_rates_reach_final_payout():
    case = read_json(ROOT / "tests/cases/T01.json")
    del case["key_rate"]
    case["key_rate_history"] = [
        {"date_from": "2024-01-01", "rate": 0.1},
        {"date_from": "2026-07-01", "rate": 0.2},
    ]
    employee = calculate_case(case)["employees"][0]
    row = employee["interest"]["rows"][0]
    # 08.08.2025 → 01.07.2026: 327 дней; затем до 10.11.2026: 132 дня.
    assert row["parts"][0]["interest"] == pytest.approx(
        row["deferred"] * (327 * 0.1 + 132 * 0.2) / 365
    )
    assert row["parts"][0]["rate"] == pytest.approx((327 * 0.1 + 132 * 0.2) / 459)
    assert employee["payouts"][0]["interest"] == employee["interest"]["total"]
