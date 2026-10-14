"""Чистый расчёт sov-result/1 по CALC_RULES §2–12, без внешнего состояния."""

from sov.engine.params import DEFAULT_PARAMS, SovParams
from sov.engine.rules.budget import project_bonus
from sov.engine.rules.dates import as_date, closure_date, milestone_weights, pay_date
from sov.engine.rules.financial import (
    CATEGORIES,
    bs_multiplier,
    is_kupk,
    k_fin,
    kpi_factor,
    milestone_factor,
    stage_factor,
)
from sov.engine.rules.payouts import apply_net_and_delay, final_parts, interest_rows

MESSAGES = {
    "NPV_MISSING": "Не задан NPV для расчёта.",
    "ASSIGNMENT_INCOMPLETE": "Назначение неполное или периоды пересекаются.",
    "BGB_GAP": "История БГБ не покрывает плановый период этапа.",
    "FINAL_PLAN_MISSING": "Не задан плановый срок ключевой вехи.",
    "FACT_WITHOUT_1C_CLOSE": "Фактическая дата и закрытие в 1С не согласованы.",
    "BS_SHARE_MISSING": "Не задана доля перевыставления БС.",
    "KPI_MISSING": "Нет KPI для вехи МП.",
    "LOAD_CHANGE_25PP": "Загрузка изменилась больше допустимого порога.",
    "STAGE_EARLY_OVER_100": "Опережение этапа требует решения Группы СОВ.",
    "DISMISSED_ASSIGNMENT_OPEN": "Сотрудник уволен, назначение не закрыто.",
    "MANUAL_CALC": "Случай требует ручного расчёта.",
    "MILESTONE_AFTER_FINAL_PLAN": "План промежуточной вехи позже ключевой.",
}


def _problem(problems, code, tab_no, blocking=True, milestone=None):
    item = {"code": code, "tab_no": tab_no, "blocking": blocking, "message": MESSAGES[code]}
    if milestone is not None:
        item["milestone"] = milestone
    if item not in problems:
        problems.append(item)


def _empty_employee(employee):
    return {
        **{
            key: employee.get(key)
            for key in ("tab_no", "fio", "category", "role", "start", "leave_date")
        },
        "duration_days": None,
        "pb": None,
        "advance_share": None,
        "advance_pool": None,
        "final_pool": None,
        "k_fin": None,
        "k_bs": None,
        "milestones": [],
        "final": {"amount": None, "raw_amount": None, "calc_status": "нет данных"},
        "leave": None,
        "interest": {"rows": [], "total": 0},
        "payouts": [],
        "total_payable": 0,
    }


def _has_previous_open(milestone, all_milestones):
    return any(m["plan"] < milestone["plan"] and not closure_date(m) for m in all_milestones)


def _calculate_employee(case, employee, params, problems):
    result = _empty_employee(employee)
    tab_no, category = employee.get("tab_no"), employee.get("category")
    final_input = case.get("final") or {}
    if not final_input.get("plan"):
        _problem(problems, "FINAL_PLAN_MISSING", tab_no)
        return result
    if category not in CATEGORIES or not employee.get("start"):
        _problem(problems, "ASSIGNMENT_INCOMPLETE", tab_no)
        return result
    start, leave_date = employee["start"], employee.get("leave_date")
    individual = employee.get("individual_dates") or {}
    milestones = case["milestones"]
    final_plan = individual.get("final", final_input["plan"])
    end = max([final_input["plan"], *[m["plan"] for m in milestones], *individual.values()])
    duration = (as_date(end) - as_date(start)).days + 1
    if duration <= 0:
        _problem(problems, "ASSIGNMENT_INCOMPLETE", tab_no)
        return result
    pb, budget_issues = project_bonus(employee.get("periods", []), start, end, params)
    for code in sorted(budget_issues):
        _problem(problems, code, tab_no)
    if employee.get("hr_dismissal_date") and not leave_date:
        _problem(problems, "DISMISSED_ASSIGNMENT_OPEN", tab_no)
    periods = sorted(employee.get("periods", []), key=lambda p: p["from"])
    for previous, current in zip(periods, periods[1:]):
        if (
            previous.get("load") is not None
            and current.get("load") is not None
            and abs(previous["load"] - current["load"]) > params.load_change_threshold
        ):
            _problem(problems, "LOAD_CHANGE_25PP", tab_no, blocking=False)
    if pb is None:
        return result
    kupk = is_kupk(category)
    financial = None
    if kupk:
        if employee.get("npv_start") is None or employee.get("npv_current") is None:
            _problem(problems, "NPV_MISSING", tab_no)
            return result
        financial = k_fin(category, employee["npv_start"], employee["npv_current"], params)
    bs = case.get("bs") or {}
    if bs.get("enabled") and category in ("КУПК", "РУПК") and bs.get("share_transferred") is None:
        _problem(problems, "BS_SHARE_MISSING", tab_no)
        return result
    bs_factor = bs_multiplier(
        category, bs.get("enabled", False), bs.get("share_transferred"), params
    )
    share = params.advance_shares[case["stage"] - 1]
    result.update(
        duration_days=duration,
        pb=pb,
        advance_share=share,
        advance_pool=pb * share,
        final_pool=pb * (1 - share),
        k_fin=financial,
        k_bs=bs_factor,
    )
    weights = milestone_weights(
        start,
        case.get("continues_from_prev_stage", False),
        case.get("new_stage_start"),
        [m["plan"] for m in milestones],
        {
            str(i): individual[m["name"]]
            for i, m in enumerate(milestones)
            if m["name"] in individual
        },
        params,
    )
    for index, milestone in enumerate(milestones):
        effective_plan = individual.get(milestone["name"], milestone["plan"])
        fact, closed = milestone.get("fact"), closure_date(milestone)
        item = {
            "name": milestone["name"],
            "plan": milestone["plan"],
            "individual_plan": individual.get(milestone["name"]),
            "fact": fact,
            "deviation_days": (as_date(effective_plan) - as_date(fact)).days if fact else None,
            "weight": weights["weight"] if weights["participates"][index] else None,
            "amount": None,
            "pay_date": None,
            "calc_status": "не закрыта",
        }
        result["milestones"].append(item)
        if effective_plan > final_plan:
            _problem(problems, "MILESTONE_AFTER_FINAL_PLAN", tab_no, False, milestone["name"])
        if not weights["participates"][index]:
            item["calc_status"] = "не участвует"
            continue
        if not fact or not closed:
            continue
        item["pay_date"] = pay_date(closed, case.get("holidays", ()), params)
        if leave_date and closed > leave_date:
            item.update(amount=0.0, calc_status="после ухода")
            continue
        kpi = (employee.get("kpi") or {}).get(milestone["name"])
        if category == "МП" and kpi is None:
            item["calc_status"] = "нет данных"
            _problem(problems, "KPI_MISSING", tab_no, milestone=milestone["name"])
            continue
        milestone_financial = financial
        if kupk and "npv_by_milestone" in employee:
            npv = employee["npv_by_milestone"].get(milestone["name"])
            if npv is None:
                item["calc_status"] = "нет данных"
                _problem(problems, "NPV_MISSING", tab_no, milestone=milestone["name"])
                continue
            milestone_financial = k_fin(category, employee["npv_start"], npv, params)
        milestone_bs = bs_factor
        if bs.get("enabled") and category in ("КУПК", "РУПК") and "bs_by_milestone" in case:
            value = case["bs_by_milestone"].get(milestone["name"])
            if value is None:
                item["calc_status"] = "нет данных"
                _problem(problems, "BS_SHARE_MISSING", tab_no, milestone=milestone["name"])
                continue
            milestone_bs = bs_multiplier(category, True, value, params)
        factor = milestone_factor(
            category, item["deviation_days"], milestone_financial, kpi, params
        )
        item["amount"] = pb * share * weights["weight"] * milestone_bs * factor
        item["calc_status"] = (
            "ждёт предыдущую веху" if _has_previous_open(milestone, milestones) else "рассчитано"
        )
    _calculate_final(case, employee, result, final_plan, params, problems)
    return result


def _calculate_final(case, employee, result, final_plan, params, problems):
    final_input = case["final"]
    fact, closed = final_input.get("fact"), closure_date(final_input)
    category, tab_no = employee["category"], employee["tab_no"]
    leave_date = employee.get("leave_date")
    deviation = (as_date(final_plan) - as_date(fact)).days if fact else None
    final = {
        "plan": final_input["plan"],
        "individual_plan": employee.get("individual_dates", {}).get("final"),
        "fact": fact,
        "closed_1c": closed,
        "deviation_days": deviation,
        "kind": "остаток при уходе" if leave_date else "финальный бонус",
        "k_stage": None,
        "amount": None,
        "raw_amount": None,
        "pay_date": None,
        "calc_status": "не закрыта",
    }
    result["final"] = final
    if fact and closed:
        final.update(
            k_stage=stage_factor(min(0, deviation) if leave_date else deviation, params),
            pay_date=pay_date(closed, case.get("holidays", ()), params),
            calc_status="рассчитано",
        )
        factor = result["k_fin"] if is_kupk(category) else 1
        if is_kupk(category):
            checkpoints = employee.get("npv_checkpoints") or {}
            end_npv = checkpoints.get("final", employee["npv_current"])
            if leave_date:
                leave_npv = employee.get("npv_at_leave", employee["npv_current"])
                end_npv = employee.get("npv_stage_end", end_npv)
                if leave_npv is not None and end_npv is not None:
                    end_npv = min(leave_npv, end_npv)
                else:
                    end_npv = None
            if end_npv is None:
                _problem(problems, "NPV_MISSING", tab_no)
                final["calc_status"] = "нет данных"
            else:
                factor = k_fin(category, employee["npv_start"], end_npv, params)
        if category == "МП":
            kpis = [(employee.get("kpi") or {}).get(m["name"]) for m in case["milestones"]]
            if not kpis or any(k is None for k in kpis):
                final["calc_status"] = "нет данных"
            else:
                factor = kpi_factor(sum(kpis) / len(kpis), params)
        if any(m["calc_status"] == "нет данных" for m in result["milestones"]):
            final["calc_status"] = "нет данных"
        if final["calc_status"] != "нет данных":
            base = result["pb"] * result["k_bs"]
            worked = None
            if leave_date:
                worked = (as_date(leave_date) - as_date(employee["start"])).days + 1
                base = base / result["duration_days"] * worked
            advances = sum(m["amount"] or 0 for m in result["milestones"])
            raw = base * factor * final["k_stage"] - advances
            final.update(raw_amount=raw, amount=max(0, raw))
            if leave_date:
                result["leave"] = {
                    "date": leave_date,
                    "worked_days": worked,
                    "k_stage": final["k_stage"],
                    "k_fin": factor if is_kupk(category) else None,
                    "remainder": final["amount"],
                }
            if _has_previous_open(final_input, case["milestones"]):
                final["calc_status"] = "ждёт предыдущую веху"
        if deviation > params.early_decision_days:
            final["calc_status"] = "требует решения"
            _problem(problems, "STAGE_EARLY_OVER_100", tab_no)
    parts = final_parts(case, employee, final, params)
    result["interest"] = interest_rows(
        case, result["milestones"], parts, result["advance_share"], params
    )
    if apply_net_and_delay(final, parts, params):
        _problem(problems, "MANUAL_CALC", tab_no)
    for part in parts:
        if part["calc_status"] == "нет данных":
            code = (
                "NPV_MISSING"
                if (is_kupk(category) and case["stage"] != 5 and part["npv_checkpoint"] is None)
                else "MANUAL_CALC"
            )
            _problem(problems, code, tab_no)
    result["payouts"] = parts
    result["total_payable"] = sum(
        item["amount"] or 0
        for item in [*result["milestones"], *parts]
        if item["calc_status"] == "рассчитано"
    )


def calculate_case(case: dict, params: SovParams | None = None) -> dict:
    """Вернуть полный расчёт и проблемы, не изменяя входной кейс."""
    params = params or DEFAULT_PARAMS
    problems = []
    for milestone in [*case.get("milestones", []), case.get("final") or {}]:
        if bool(milestone.get("fact")) != bool(closure_date(milestone)):
            _problem(problems, "FACT_WITHOUT_1C_CLOSE", None, False, milestone.get("name"))
    employees = [_calculate_employee(case, e, params, problems) for e in case["employees"]]
    blocking = sorted(
        {(p["code"], p["tab_no"]) for p in problems if p["blocking"]},
        key=lambda item: (item[0], item[1] or ""),
    )
    return {
        "schema": "sov-result/1",
        "id": case["id"],
        "variant": "spec",
        "edition": params.edition,
        "title": case.get("title"),
        "employees": employees,
        "problems": problems,
        "blocking_problems": [{"code": code, "tab_no": tab} for code, tab in blocking],
    }
