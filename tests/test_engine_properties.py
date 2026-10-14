"""Свойства движка и запуск CLI, существенные для переноса на Windows."""

import json
import subprocess
import sys
from copy import deepcopy
from dataclasses import replace

import pytest
from conftest import ROOT, read_json

from sov.engine.calculate import calculate_case
from sov.engine.params import DEFAULT_PARAMS

CASES = sorted((ROOT / "tests/cases").glob("*.json"))


@pytest.mark.parametrize("path", CASES, ids=[p.stem for p in CASES])
def test_calculation_is_repeatable_and_does_not_change_input(path):
    case = read_json(path)
    before = deepcopy(case)
    result = calculate_case(case)
    assert case == before
    assert result == calculate_case(case)
    assert result["schema"] == "sov-result/1"
    assert result["edition"] == DEFAULT_PARAMS.edition
    # Результат должен сериализоваться без NaN/Infinity, включая неполные кейсы.
    json.dumps(result, allow_nan=False)


def test_parameters_change_rules_without_changing_default_edition():
    case = read_json(ROOT / "tests/cases/T05.json")
    capped = calculate_case(case, replace(DEFAULT_PARAMS, milestone_cap=1))
    on_time = deepcopy(case)
    on_time["milestones"][2]["fact"] = on_time["milestones"][2]["plan"]
    baseline = calculate_case(on_time)
    assert capped["employees"][0]["milestones"][2]["amount"] == pytest.approx(
        baseline["employees"][0]["milestones"][2]["amount"]
    )
    assert calculate_case(case) != capped


def test_closure_date_controls_pay_quarter_and_leave_eligibility():
    case = read_json(ROOT / "tests/cases/T15.json")
    case["milestones"][3]["closed_1c"] = "2026-04-01"
    result = calculate_case(case)
    for employee in result["employees"]:
        milestone = employee["milestones"][3]
        assert milestone["fact"] == "2026-03-31"
        assert milestone["pay_date"] == "2026-08-10"
        assert milestone["amount"] == 0
        assert milestone["calc_status"] == "после ухода"


def test_delayed_part_keeps_interest_to_original_date():
    case = read_json(ROOT / "tests/cases/T14.json")
    first, delayed = calculate_case(case)["employees"]
    assert delayed["payouts"][1]["delayed"]
    assert delayed["payouts"][1]["pay_date"] == delayed["payouts"][2]["pay_date"]
    assert delayed["payouts"][1]["interest"] == first["payouts"][1]["interest"]


def test_cli_supports_unicode_spaces_and_utf8_bom(tmp_path):
    inputs = tmp_path / "Условные кейсы"
    outputs = tmp_path / "Результаты расчёта"
    inputs.mkdir()
    case = read_json(ROOT / "tests/cases/T01.json")
    (inputs / "T01.json").write_text(json.dumps(case, ensure_ascii=False), encoding="utf-8-sig")
    command = [sys.executable, "-m", "sov.engine.cli", str(inputs), str(outputs)]
    process = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, check=False)
    assert process.returncode == 0, process.stderr
    assert read_json(outputs / "T01.json") == calculate_case(case)


def test_cli_does_not_replace_case_files(tmp_path):
    original = (ROOT / "tests/cases/T01.json").read_bytes()
    case_path = tmp_path / "T01.json"
    case_path.write_bytes(original)
    process = subprocess.run(
        [sys.executable, "-m", "sov.engine.cli", str(tmp_path), str(tmp_path)],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    assert process.returncode == 2
    assert case_path.read_bytes() == original


def test_cli_reports_invalid_json_without_partial_results(tmp_path):
    inputs, outputs = tmp_path / "cases", tmp_path / "results"
    inputs.mkdir()
    (inputs / "T01.json").write_bytes((ROOT / "tests/cases/T01.json").read_bytes())
    (inputs / "T02.json").write_text("{invalid", encoding="utf-8")
    process = subprocess.run(
        [sys.executable, "-m", "sov.engine.cli", str(inputs), str(outputs)],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    assert process.returncode == 2
    assert not outputs.exists()


def test_negative_final_with_positive_net_requires_manual_kupk_calculation():
    case = read_json(ROOT / "tests/cases/T02.json")
    case["final"]["fact"] = "2026-12-29"
    case["key_rate"] = 9  # Условный стресс-сценарий: проценты перекрывают отрицательный финал.
    result = calculate_case(case)
    for employee in result["employees"]:
        assert employee["final"]["raw_amount"] < 0
        assert employee["final"]["raw_amount"] + employee["interest"]["total"] > 0
        assert employee["final"]["calc_status"] == "ручной расчёт"
        assert all(p["calc_status"] == "ручной расчёт" for p in employee["payouts"])
        assert {"code": "MANUAL_CALC", "tab_no": employee["tab_no"]} in result["blocking_problems"]


def test_missing_control_npv_blocks_only_the_affected_part():
    case = read_json(ROOT / "tests/cases/T14.json")
    case["employees"][0]["npv_checkpoints"]["q1"] = None
    result = calculate_case(case)
    parts = result["employees"][0]["payouts"]
    assert parts[1]["calc_status"] == "нет данных"
    assert parts[0]["calc_status"] == parts[2]["calc_status"] == "рассчитано"
    assert {"code": "NPV_MISSING", "tab_no": case["employees"][0]["tab_no"]} in result[
        "blocking_problems"
    ]


def test_missing_interest_rate_is_not_reported_as_missing_npv():
    case = read_json(ROOT / "tests/cases/T02.json")
    del case["key_rate"]
    result = calculate_case(case)
    assert all(p["code"] == "MANUAL_CALC" for p in result["problems"])
    assert result["blocking_problems"]
    assert all(p["calc_status"] == "нет данных" for e in result["employees"] for p in e["payouts"])
