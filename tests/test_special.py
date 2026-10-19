"""M6: особые случаи на копиях условных кейсов, без изменения эталонов."""

from copy import deepcopy
from dataclasses import replace

import pytest
from test_scenario import source

from sov.engine.calculate import calculate_case
from sov.engine.params import DEFAULT_PARAMS, params_for_quarter
from sov.engine.rules.redistribution import validate_redistribution


def one(case):
    return calculate_case(case)["employees"][0]


def test_probation_keeps_budget_holds_then_forfeits_bonus():
    case = source("T01")
    baseline = one(case)
    employee = case["employees"][0]
    employee.update(probation_end="2027-01-31", probation_result="проходит")
    case["as_of"] = "2026-09-30"
    held = one(case)
    assert held["pb"] == baseline["pb"]
    assert held["total_payable"] == 0
    assert all(m["calc_status"] == "ожидает окончания ИС" for m in held["milestones"])
    employee["probation_result"] = "не пройден"
    lost = one(case)
    assert lost["pb"] == baseline["pb"] and lost["total_payable"] == 0
    assert lost["final"]["amount"] == lost["interest"]["total"] == 0
    assert all(p["calc_status"] == "сгорело" and p["amount"] == 0 for p in lost["payouts"])


def test_successful_probation_uses_explicit_release_date_and_recalculates_interest():
    case = source("T01")
    original = one(case)
    case["employees"][0].update(
        probation_end="2025-09-30", probation_result="пройден", probation_release_date="2025-11-10"
    )
    case["as_of"] = "2025-09-30"
    assert one(case)["milestones"][0]["calc_status"] == "ожидает окончания ИС"
    case["as_of"] = "2026-09-30"
    result = one(case)
    assert result["milestones"][0]["pay_date"] == "2025-11-10"
    assert result["milestones"][0]["recognition_date"] == "2025-11-10"
    assert result["interest"]["total"] < original["interest"]["total"]
    assert result["pb"] == original["pb"]
    case["employees"][0]["probation_release_date"] = "2025-09-01"
    with pytest.raises(ValueError):
        one(case)


def test_sale_prorates_work_uses_previous_quarter_npv_and_single_payout():
    case = source("T02")
    # Будущие незакрытые вехи не удерживают остаток при продаже.
    for milestone in case["milestones"][3:]:
        milestone.update(fact=None, closed_1c=None)
    case["sale"] = {"date": "2026-01-15", "work_end": "2025-12-31", "npv_previous": 4_500_000_000}
    result = one(case)
    worked = 275
    advances = sum(m["amount"] or 0 for m in result["milestones"])
    expected = result["pb"] / result["duration_days"] * worked * 0.9 - advances
    assert result["final"]["raw_amount"] == pytest.approx(expected)
    assert result["final"]["kind"] == "остаток при продаже"
    assert result["payouts"][0]["part"] == "100%"
    assert result["payouts"][0]["event_date"] == "2026-01-15"
    assert result["payouts"][0]["pay_date"] == "2026-05-08"
    assert len(result["payouts"]) == 1
    assert not any(p["code"] == "STAGE_EARLY_OVER_100" for p in calculate_case(case)["problems"])


def test_sale_requires_work_end_and_explicit_max_14_day_extension():
    case = source("T01")
    case["sale"] = {"date": "2026-01-15", "work_end": None}
    assert calculate_case(case)["blocking_problems"][0]["code"] == "MANUAL_CALC"
    for end, approved, blocked in (
        ("2026-01-29", False, True),
        ("2026-01-29", True, False),
        ("2026-01-30", True, True),
    ):
        case["sale"].update(work_end=end, extension_approved=approved)
        assert bool(calculate_case(case)["blocking_problems"]) == blocked


def test_handover_burns_remaining_bonus_and_exception_projects_do_not_use_new_rules():
    case = source("T02")
    case["handover_date"] = "2026-01-15"
    result = one(case)
    assert result["final"]["calc_status"] == "сгорело" and result["final"]["amount"] == 0
    assert result["interest"]["total"] == 0
    assert all(m["amount"] == 0 for m in result["milestones"][3:])
    assert result["payouts"][0]["event_date"] == "2026-01-15"
    case["is_exception"] = True
    assert one(case)["pb"] is None
    assert calculate_case(case)["blocking_problems"][0]["code"] == "MANUAL_CALC"


def test_frozen_advances_survive_extension_and_keep_original_deferred_amount():
    case = source("T01")
    old = one(case)
    case["employees"][0]["frozen_advances"] = {
        m["name"]: {
            "amount": m["amount"],
            "pay_date": m["pay_date"],
            "event_date": m["fact"],
            "deferred": old["interest"]["rows"][i]["deferred"],
        }
        for i, m in enumerate(old["milestones"])
    }
    case["final"]["plan"] = "2026-12-31"
    case["employees"][0]["periods"][0]["to"] = "2026-12-31"
    result = one(case)
    assert result["pb"] > old["pb"]
    assert [m["amount"] for m in result["milestones"]] == [m["amount"] for m in old["milestones"]]
    assert [r["deferred"] for r in result["interest"]["rows"]] == [
        r["deferred"] for r in old["interest"]["rows"]
    ]
    assert result["final"]["amount"] > old["final"]["amount"]


def redistribution_context():
    case = source("T01")
    result = one(case)
    members = [
        {
            "id": identifier,
            "project_code": "TP-TEST",
            "queue": "1",
            "stage": 4,
            "tab_no": tab,
            "fp": "Условное ФП",
            "category": "РУПК",
            "employee": {**case["employees"][0], "tab_no": tab},
            "result": deepcopy(result),
        }
        for identifier, tab in (("T01", "T0001"), ("T01-other", "T9001"))
    ]
    rows = [
        {
            "assignment_id": identifier,
            "milestone": case["milestones"][0]["name"],
            "pct": pct,
            "decided_by": "T9009",
            "approved_date": "2025-07-01",
            "basis": "Условное письмо",
        }
        for identifier, pct in (("T01", 0.3), ("T01-other", -0.3))
    ]
    return {"members": members, "rows": rows}


def test_redistribution_balances_money_and_uses_average_for_final():
    context = redistribution_context()
    maps = validate_redistribution(context)
    assert maps["T01"]
    case = source("T01")
    baseline = one(case)
    case["redistribution_context"] = context
    before = deepcopy(case)
    result = one(case)
    assert case == before
    assert result["milestones"][0]["amount"] == pytest.approx(
        baseline["milestones"][0]["amount"] * 1.3
    )
    assert result["final"]["amount"] == pytest.approx(baseline["final"]["amount"] * 1.05)
    case["id"] = "T01-other"
    case["employees"][0]["tab_no"] = "T9001"
    other = one(case)
    assert result["final"]["amount"] + other["final"]["amount"] == pytest.approx(
        baseline["final"]["amount"] * 2
    )


@pytest.mark.parametrize(
    "mutation",
    ["limit", "self", "single", "fp", "category", "fund", "final", "zero", "frozen", "leaver"],
)
def test_invalid_redistribution_never_becomes_payroll(mutation):
    context = redistribution_context()
    if mutation == "limit":
        context["rows"][0]["pct"] = 0.31
    if mutation == "self":
        context["rows"][0]["decided_by"] = "T0001"
    if mutation == "single":
        context["members"] = context["members"][:1]
    if mutation == "fp":
        context["members"][1]["fp"] = "Другое ФП"
    if mutation == "category":
        context["members"][0]["category"] = "КУПК"
    if mutation == "fund":
        context["rows"][1]["pct"] = -0.2
    if mutation == "final":
        context["members"][0]["result"]["final"]["amount"] *= 2
    if mutation == "zero":
        context["members"][0]["result"]["milestones"][0]["amount"] = 0
    if mutation == "frozen":
        context["members"][0]["result"]["milestones"][0]["frozen"] = True
    if mutation == "leaver":
        # СОВ v3, сл. 32: решение не позднее чем за 5 рабочих дней до увольнения.
        context["members"][0]["employee"]["leave_date"] = "2025-07-04"
    with pytest.raises(ValueError):
        validate_redistribution(context)
    case = source("T01")
    case["redistribution_context"] = context
    result = calculate_case(case)
    assert any(p["code"] == "MANUAL_CALC" for p in result["problems"])
    assert result["employees"][0]["total_payable"] == 0


def test_rules_edition_selection_and_invalid_editions():
    rows = [
        {"edition": "Условная редакция", "name": "effective_from", "value": "2027-Q1"},
        {
            "edition": "Условная редакция",
            "name": "advance_shares",
            "value": "[0.6,0.6,0.6,0.6,0.3]",
        },
    ]
    assert params_for_quarter(rows, "2026-Q4").advance_shares[0] == 0.67
    assert params_for_quarter(rows, "2027-Q1").advance_shares[0] == 0.6
    for value in ("[1,1,1,1,1]", "[0.6]", '"строка"'):
        rows[1]["value"] = value
        with pytest.raises(ValueError):
            params_for_quarter(rows, "2027-Q1")


def test_new_edition_labels_and_frozen_payout_survive_share_change():
    case = source("T02")
    old = one(case)
    first = old["payouts"][0]
    employee = case["employees"][0]
    employee["frozen_payouts"] = {first["part"]: {**first, "slot": 0}}
    employee["frozen_interest"] = {
        first["part"]: {row["milestone"]: row["parts"][0] for row in old["interest"]["rows"]}
    }
    employee.update(
        probation_end="2026-12-31", probation_result="пройден", probation_release_date="2027-06-10"
    )
    case["as_of"] = "2027-09-30"
    result = calculate_case(case, replace(DEFAULT_PARAMS, final_shares=(0.6, 0.2, 0.2)))[
        "employees"
    ][0]
    assert [p["part"] for p in result["payouts"]] == ["50%", "20% (1)", "20% (2)"]
    for key in ("amount", "bonus", "interest", "pay_date", "event_date"):
        assert result["payouts"][0][key] == first[key]
    assert result["interest"]["rows"][0]["parts"][0] == old["interest"]["rows"][0]["parts"][0]
    assert result["payouts"][1]["pay_date"] == "2027-06-10"


@pytest.mark.parametrize("event", ["failed_probation", "handover"])
def test_forfeiture_preserves_already_accrued_payout_and_interest(event):
    case = source("T02")
    first = one(case)["payouts"][0]
    case["employees"][0]["frozen_payouts"] = {first["part"]: {**first, "slot": 0}}
    if event == "failed_probation":
        case["employees"][0].update(probation_end="2026-12-31", probation_result="не пройден")
    else:
        case["handover_date"] = "2026-01-15"
    result = one(case)
    assert result["payouts"][0]["amount"] == first["amount"]
    assert result["interest"]["total"] == first["interest"]
    assert all(p["amount"] == 0 for p in result["payouts"][1:])


def test_edition_accepts_fractional_float_parameters_but_requires_integer_days():
    rows = [
        {"edition": "Условная редакция", "name": "effective_from", "value": "2027-Q1"},
        {"edition": "Условная редакция", "name": "bs_slope", "value": "1.5"},
    ]
    assert params_for_quarter(rows, "2027-Q1").bs_slope == 1.5
    rows[1].update(name="checkpoint_days", value="90.5")
    with pytest.raises(ValueError):
        params_for_quarter(rows, "2027-Q1")


def test_redistribution_allowed_five_workdays_before_leave():
    context = redistribution_context()
    context["members"][0]["employee"]["hr_dismissal_date"] = "2025-07-08"  # 01.07 — за 5 раб. дней
    assert validate_redistribution(context)["T01"]
