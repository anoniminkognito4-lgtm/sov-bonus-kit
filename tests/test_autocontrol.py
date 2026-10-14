"""Проблемы, статусы и сохранность сумм при неполных данных."""

from copy import deepcopy

import pytest
from conftest import ROOT, read_json

from sov.engine.calculate import calculate_case

CASES = read_json(ROOT / "tests/autocontrol/cases.json")["cases"]


def modified_case(control):
    case = deepcopy(read_json(ROOT / "tests/cases" / f"{control['base']}.json"))
    for path, value in control["set"].items():
        parts = [int(p) if p.isdigit() else p for p in path.split(".")]
        target = case
        for key in parts[:-1]:
            target = target[key]
        target[parts[-1]] = value
    return case


@pytest.mark.m1
@pytest.mark.parametrize("control", CASES, ids=[c["id"] for c in CASES])
def test_autocontrol(control):
    result = calculate_case(modified_case(control))
    for expected in control["expect"]:
        assert any(
            all(problem.get(key) == value for key, value in expected.items())
            for problem in result["problems"]
        ), f"Не найдена проблема: {expected}"
    if control.get("expect_no_blocking"):
        assert not result["blocking_problems"]
        assert not any(p["blocking"] for p in result["problems"])
    employees = {e["tab_no"]: e for e in result["employees"]}
    for tab_no, statuses in control.get("statuses", {}).items():
        employee = employees[tab_no]
        milestones = {m["name"]: m for m in employee["milestones"]}
        for name, status in statuses.items():
            item = employee["final"] if name == "final" else milestones[name]
            assert item["calc_status"] == status
    if comparison := control.get("same_amounts_as"):
        baseline = calculate_case(read_json(ROOT / "tests/cases" / f"{comparison['case']}.json"))
        # T11 и T11b используют разные табельные номера; сопоставляем позиции.
        for employee, original in zip(result["employees"], baseline["employees"], strict=True):
            current = {m["name"]: m for m in employee["milestones"]}
            before = {m["name"]: m for m in original["milestones"]}
            for name in comparison["milestones"]:
                assert current[name]["amount"] == pytest.approx(
                    before[name]["amount"], rel=0, abs=0.01
                )
