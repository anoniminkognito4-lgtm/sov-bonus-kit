"""Редакции и варианты правила К фин (RULE_CHANGES §1, §4; E03, E04)."""

from dataclasses import replace

import pytest
from conftest import ROOT, read_json

from sov.engine.calculate import calculate_case
from sov.engine.params import DEFAULT_PARAMS, E0_PARAMS, params_for_quarter
from sov.engine.rules.financial import k_fin
from tools.compare_golden import compare

M = 1_000_000
TABLE = [
    # старт, текущий, категория, start_lt, abs_current_lt, any_le
    (600, 300, "КУПК", 0.75, 0.85, 0.85),
    (800, 480, "КУПК (РП/ЗРП)", 0.60, 0.84, 0.84),
    (500, 700, "КУПК", 1.25, 1.25, 1.10),
    (450, 900, "КУПК (РП/ЗРП)", 1.225, 1.50, 1.225),
    (-11_965.987635, -6_356.254468, "КУПК (РП/ЗРП)", 1.50, 0.5312, 1.50),  # сл. 24, пример 1
    (499.568365, 560.658545, "КУПК", 1.0305, 1.1223, 1.0305),  # сл. 24, пример 2
]


@pytest.mark.parametrize("start, current, category, expected", [(*r[:3], r[3:]) for r in TABLE])
def test_k_fin_variants_table(start, current, category, expected):
    for trigger, value in zip(("start_lt", "abs_current_lt", "any_le"), expected, strict=True):
        params = replace(DEFAULT_PARAMS, low_npv_trigger=trigger)
        assert k_fin(category, start * M, current * M, params) == pytest.approx(value, abs=1e-4)


def test_edition_by_quarter():
    assert params_for_quarter([], "2026-Q2").low_npv_trigger == "start_lt"
    assert params_for_quarter([], "2026-Q2").edition == "E0"
    assert params_for_quarter([], "2026-Q3") is DEFAULT_PARAMS
    assert DEFAULT_PARAMS.low_npv_trigger == "any_le"


def test_uploaded_edition_inherits_builtin_of_its_start():
    rows = [
        {"edition": "SOV-test", "name": "effective_from", "value": "2026-Q1"},
        {"edition": "SOV-test", "name": "checkpoint_days", "value": "91"},
    ]
    params = params_for_quarter(rows, "2026-Q2")
    assert params.checkpoint_days == 91 and params.low_npv_trigger == "start_lt"
    rows.append({"edition": "SOV-test", "name": "low_npv_trigger", "value": '"other"'})
    with pytest.raises(ValueError):
        params_for_quarter(rows, "2026-Q2")


GOLDEN = sorted((ROOT / "tests/golden/spec").glob("*.json"))


@pytest.mark.parametrize("path", GOLDEN, ids=[p.stem for p in GOLDEN])
def test_golden_in_base_edition_e0(path):
    # E04: эталоны считаются в E0; any_le их не меняет (основной тест — test_golden.py).
    case = read_json(ROOT / "tests/cases" / path.name)
    assert not compare(read_json(path), calculate_case(case, E0_PARAMS))
