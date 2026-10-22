"""M4: эталонные свойства сценария, сохранность реестра и предел задержки."""

import json
from copy import deepcopy
from datetime import date, timedelta
from hashlib import sha256
from time import perf_counter

import pytest
from conftest import ROOT
from test_panel import fix_sources, panel  # noqa: F401, F811

from sov.engine.calculate import calculate_case
from sov.engine.rules.kpi import milestone_kpis
from sov.exports.files import payroll
from sov.services.panel import Actor, PanelService
from sov.services.scenario import day_prices, nearest_payment, scenario_case, simulate


def source(name, employee=0):
    case = json.loads((ROOT / f"tests/cases/{name}.json").read_text(encoding="utf-8"))
    case["employees"] = [case["employees"][employee]]
    return case


def run(case, changes=None, historical=True):
    return simulate(case, changes or {}, as_of="2026-09-30", historical=historical)


@pytest.mark.parametrize(
    "path", sorted((ROOT / "tests/cases").glob("*.json")), ids=lambda p: p.stem
)
def test_no_changes_is_exact_and_input_untouched(path):
    case = source(path.stem)
    before = deepcopy(case)
    result = run(case)
    assert result["case"] == case == before
    assert result["baseline"] == result["scenario"] == calculate_case(case)


def test_cap_zero_zone_and_price_day():
    case = source("T05")
    milestone = case["milestones"][2]
    result = run(case, {"dates": {milestone["name"]: "2025-09-15"}})
    a, b = (r["employees"][0]["milestones"][2] for r in (result["baseline"], result["scenario"]))
    assert b["amount"] == a["amount"]
    assert next(r for r in result["prices"] if r["name"] == milestone["name"])["amount"] == 0
    case = source("T06")
    milestone = case["milestones"][2]
    for delay in (100, 120):
        fact = (date.fromisoformat(milestone["plan"]) + timedelta(days=delay)).isoformat()
        result = run(case, {"dates": {milestone["name"]: fact}})
        assert result["scenario"]["employees"][0]["milestones"][2]["amount"] == 0
        assert next(r for r in result["prices"] if r["name"] == milestone["name"])["amount"] == 0
    case = source("T01")
    prices = day_prices(case, historical=True)
    expected = calculate_case(case)["employees"][0]["milestones"][0]["amount"] * 0.01
    assert prices[0]["amount"] == pytest.approx(expected)


def test_checkpoint_bs_and_leaving_acceptance_cases():
    result = run(source("T14"), {"npv_checkpoints": {"q2": 4_900_000_000}})
    assert result["baseline"]["employees"][0]["payouts"][-1]["calc_status"] == "рассчитано"
    assert result["scenario"]["employees"][0]["payouts"][-1]["calc_status"] == "приостановлено"
    result = run(source("T10b"), {"bs_share": 0.95})
    assert result["baseline"]["employees"][0]["k_bs"] == pytest.approx(0.94)
    assert result["scenario"]["employees"][0]["k_bs"] == 1
    result = run(source("T15"), {"leave_date": "2026-04-30"})
    a, b = (r["employees"][0]["leave"] for r in (result["baseline"], result["scenario"]))
    assert b["worked_days"] > a["worked_days"]
    assert b["remainder"] > a["remainder"]


def test_open_date_uses_individual_plan_and_nearest_payment_moves():
    case = source("T07")
    milestone = case["milestones"][3]
    milestone.update(fact=None, closed_1c=None)
    original = deepcopy(case)
    result = simulate(case, {"dates": {milestone["name"]: "2026-03-31"}}, as_of="2026-03-31")
    row = result["scenario"]["employees"][0]["milestones"][3]
    assert row["deviation_days"] == 30  # индивидуальный срок 30 апреля
    assert any(r["name"] == milestone["name"] for r in result["prices"])
    assert result["nearest_scenario"]["date"] == "2026-05-08"
    # Открытая предыдущая веха удерживает все последующие выплаты.
    assert result["nearest_now"]["date"] is None
    assert case == original


def test_current_conditions_preserve_closed_financial_inputs():
    case = source("T10b")
    case["milestones"][-1].update(fact=None, closed_1c=None)
    original = deepcopy(case)
    projected = scenario_case(case, {"npv_current": 7_000_000_000, "bs_share": 0.95})
    assert (
        projected["employees"][0]["npv_checkpoints"]["final"]
        == case["employees"][0]["npv_checkpoints"]["final"]
    )
    assert projected["bs"] == case["bs"]
    result = calculate_case(projected)["employees"][0]
    baseline = calculate_case(case)["employees"][0]
    assert result["milestones"][0] == baseline["milestones"][0]
    assert (
        projected["employees"][0]["npv_by_milestone"][case["milestones"][-1]["name"]]
        == 7_000_000_000
    )
    assert case == original


def test_quarterly_kpi_uses_shared_rule_and_reports_gaps():
    case = source("T11")
    person = case["employees"][0]
    person["kpi_quarterly"] = {f"{y}-Q{q}": 0.8 for y in (2025, 2026) for q in (1, 2, 3, 4)}
    person["kpi"] = milestone_kpis(case["milestones"], person["kpi_quarterly"])
    result = run(case, {"kpi_quarterly": {"2025-Q2": 1.5}})
    assert (
        result["scenario"]["employees"][0]["milestones"][0]["amount"]
        > result["baseline"]["employees"][0]["milestones"][0]["amount"]
    )
    case["milestones"][0].update(fact="2025-12-31", closed_1c="2025-12-31")
    person["kpi_quarterly"].pop("2025-Q3")
    projected = scenario_case(case, {"kpi_quarterly": {"2025-Q4": 1.0}}, historical=True)
    assert projected["employees"][0]["kpi"] == milestone_kpis(
        projected["milestones"], projected["employees"][0]["kpi_quarterly"]
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"unknown": 1},
        {"early_stage_approved": True},
        {"periods": {0: {"bgb": -1}}},
        {"periods": {0: {"load": 1.1}}},
        {"periods": {0: {"bgb": float("nan")}}},
        {"periods": {0: {"from": "2026-01-01"}}},
        {"periods": {50: {"bgb": 1}}},
        {"leave_date": "2025-01-01"},
        {"leave_date": "2028-01-01"},
        {"dates": {"final": "not a date"}},
        {"dates": {"none": "2026-01-01"}},
        {"npv_checkpoints": {"final": 1}},
        {"npv_current": float("inf")},
        {"bs_share": 1.1},
        {"kpi_quarterly": {"2025-Q2": 1}},
    ],
)
def test_invalid_changes_are_rejected(changes):
    with pytest.raises(ValueError):
        scenario_case(source("T05"), changes, historical=True)


def test_closed_dates_require_explicit_historical_mode_and_decisions_are_not_reused():
    case = source("T01")
    with pytest.raises(ValueError, match="исторический"):
        scenario_case(case, {"dates": {"final": "2026-09-10"}})
    case["early_stage_approved"] = True
    case["manual_pay_dates"] = {case["milestones"][0]["name"]: "2025-07-01"}
    projected = scenario_case(
        case,
        {"dates": {"final": "2026-05-01", case["milestones"][0]["name"]: "2025-07-02"}},
        historical=True,
    )
    assert "early_stage_approved" not in projected
    assert not projected["manual_pay_dates"]
    assert case["early_stage_approved"]


def test_nearest_uses_explicit_date_and_calculated_nonzero_only():
    employee = calculate_case(source("T14"))["employees"][0]
    payment = nearest_payment(employee, "2027-01-01")
    assert payment["date"] >= "2027-01-01" and payment["amount"] > 0
    assert nearest_payment(employee, "2030-01-01") == {"date": None, "amount": 0}


def test_service_does_not_change_database_snapshot_or_exports_and_is_fast(panel, tmp_path):  # noqa: F811
    fix_sources(panel, tmp_path)
    panel.close(panel.calculate("2026-Q3"))
    assignment = next(a for a in panel.registry.rows("assignment") if a["person_id"] == "T0008")
    before = sha256(panel.registry.path.read_bytes()).hexdigest()
    journal = panel.journal()
    revision = panel.registry.revision()
    closed = panel.closed("2026-Q3")
    export = payroll(closed)
    for changes in (
        {},
        {"dates": {"Мотивационная веха 3": "2025-09-01"}},
        {"periods": {0: {"bgb": 3_000_000}}},
        {},
    ):
        started = perf_counter()
        result = panel.simulate(assignment["id"], changes, as_of="2026-09-30", historical=True)
        assert perf_counter() - started <= 1
        assert result["scenario"] == calculate_case(result["case"])
    assert result["baseline"] == result["scenario"]
    assert (
        result["baseline"]
        == panel.calculate("2026-Q3", [assignment["id"]]).results[assignment["id"]]["result"]
    )
    assert sha256(panel.registry.path.read_bytes()).hexdigest() == before
    assert panel.journal() == journal and panel.registry.revision() == revision
    assert panel.closed("2026-Q3") == closed and payroll(panel.closed("2026-Q3")) == export
    for role in ("Сотрудник", "Руководитель"):
        with pytest.raises(PermissionError):
            PanelService(panel.registry, Actor("Тестов", role)).simulate(
                assignment["id"], {}, as_of="2026-09-30"
            )


def test_pending_manual_decision_is_visible_in_both_comparisons(panel):  # noqa: F811
    assignment = next(a for a in panel.registry.rows("assignment") if a["person_id"] == "T0008")
    panel.save_decision(
        "redistribution",
        assignment["id"],
        {"pct": 0.1},
        "Условное перераспределение",
        "условное_письмо.pdf",
    )
    result = panel.simulate(assignment["id"], {}, as_of="2026-09-30")
    assert result["baseline"] == result["scenario"]
    assert (
        result["baseline"]
        == panel.calculate("2026-Q3", [assignment["id"]]).results[assignment["id"]]["result"]
    )
    assert any(p["code"] == "MANUAL_CALC" for p in result["scenario"]["problems"])
