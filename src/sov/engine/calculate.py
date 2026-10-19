"""Чистый расчёт sov-result/1 по CALC_RULES §2–13, без внешнего состояния."""

from copy import deepcopy

from sov.engine.params import DEFAULT_PARAMS, SovParams
from sov.engine.rules.budget import project_bonus
from sov.engine.rules.dates import (
    as_date,
    closure_date,
    milestone_part,
    milestone_weights,
    pay_date,
)
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
from sov.engine.rules.redistribution import validate_redistribution
from sov.engine.rules.special import (
    freeze_advance,
    frozen_payout,
    handover_active,
    redistribution_factor,
    release_dates,
    restrict_probation,
    sale_details,
)

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
    "MILESTONE_STATUS_UNKNOWN": "Неизвестный статус вехи в 1С: веха не учитывается до решения.",
    "MILESTONE_PART_PLAN": "План второй части вехи приравнен к ключевой вехе этапа (РнВ).",
    "LOAD_POSITION_UNKNOWN": "Нет загрузки по должности в таблице СОВ.",
    "LOAD_FILE_DIFFERS": "Загрузка по правилам СОВ отличается от файла назначений.",
    "RP_LOAD_OUT_OF_RANGE": "Загрузка РП вне допустимого диапазона ранга.",
    "RP_SET_NOT_APPROVED": "Набор проектов РП не утверждён.",
    "RP_SET_MISSING_PROJECT": "Проекта нет в наборе РП или набор заполнен не полностью.",
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
        "final": {
            "amount": None,
            "raw_amount": None,
            "calc_status": "нет данных",
            "kind": "остаток при уходе" if employee.get("leave_date") else "финальный бонус",
        },
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
    for issue in employee.get("load_issues") or ():
        _problem(problems, issue["code"], tab_no, issue["blocking"])
    for excluded in case.get("excluded_milestones") or ():
        _problem(problems, "MILESTONE_STATUS_UNKNOWN", tab_no, milestone=excluded["name"])
    if case.get("is_exception") or employee.get("legacy_sov"):
        _problem(problems, "MANUAL_CALC", tab_no)
        return result
    if employee.get("redistribution") and not case.get("redistribution_context"):
        _problem(problems, "MANUAL_CALC", tab_no)
        return result
    try:
        sale = sale_details(case, employee, params)
    except ValueError:
        _problem(problems, "MANUAL_CALC", tab_no)
        return result
    if sale:
        case = {**case, "sale": sale}
    final_input = case.get("final") or {}
    if not final_input.get("plan"):
        _problem(problems, "FINAL_PLAN_MISSING", tab_no)
        return result
    if category not in CATEGORIES or not employee.get("start"):
        _problem(problems, "ASSIGNMENT_INCOMPLETE", tab_no)
        return result
    start, leave_date = employee["start"], employee.get("leave_date")
    if sale:
        leave_date = sale["work_end"]
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
    parts = [milestone_part(m["name"]) for m in milestones]
    second_part_plan = {
        i: final_plan for i, part in enumerate(parts) if part == 2 and case["stage"] == 4
    }
    weights = milestone_weights(
        start,
        case.get("continues_from_prev_stage", False),
        case.get("new_stage_start"),
        [m["plan"] for m in milestones],
        {
            **{
                str(i): individual[m["name"]]
                for i, m in enumerate(milestones)
                if m["name"] in individual
            },
            **{str(i): plan for i, plan in second_part_plan.items()},
        },
        params,
        parts,
    )
    for index, milestone in enumerate(milestones):
        effective_plan = second_part_plan.get(
            index, individual.get(milestone["name"], milestone["plan"])
        )
        if index in second_part_plan and milestone["plan"] != final_plan:
            _problem(problems, "MILESTONE_PART_PLAN", tab_no, False, milestone["name"])
        fact, closed = milestone.get("fact"), closure_date(milestone)
        item = {
            "name": milestone["name"],
            "plan": milestone["plan"],
            "individual_plan": individual.get(milestone["name"]),
            "fact": fact,
            "deviation_days": (as_date(effective_plan) - as_date(fact)).days if fact else None,
            "weight": weights["weights"][index],
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
        item["pay_date"] = case.get("manual_pay_dates", {}).get(
            milestone["name"], pay_date(closed, case.get("holidays", ()), params)
        )
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
        deviation = item["deviation_days"]
        if parts[index] == 2:
            deviation = min(0, deviation)  # сл. 55 п. 19: опережение второй части не влияет
        factor = milestone_factor(category, deviation, milestone_financial, kpi, params)
        item["amount"] = pb * share * weights["weights"][index] * milestone_bs * factor
        item["calc_status"] = (
            "ждёт предыдущую веху" if _has_previous_open(milestone, milestones) else "рассчитано"
        )
        if employee.get("redistribution"):
            pct = employee["redistribution"].get(milestone["name"], 0)
            if category != "РУПК" or abs(pct) > params.redistribution_limit:
                _problem(problems, "MANUAL_CALC", tab_no)
                item["calc_status"] = "ручной расчёт"
            else:
                item["base_amount"] = item["amount"]
                item["redistribution_pct"] = pct
                item["amount"] *= 1 + pct
        freeze_advance(employee, item)
    release_dates(case, employee, result["milestones"])
    _calculate_final(case, employee, result, final_plan, params, problems)
    restrict_probation(case, employee, result)
    if handover_active(case):
        for item in result["milestones"]:
            source = next(m for m in milestones if m["name"] == item["name"])
            if (
                closure_date(source)
                and closure_date(source) > case["handover_date"]
                and not item.get("frozen")
            ):
                item.update(amount=0.0, calc_status="сгорело")
        result["final"].update(amount=0.0, calc_status="сгорело")
        for part in result["payouts"]:
            if not part.get("frozen"):
                part.update(amount=0.0, bonus=0.0, interest=0.0, calc_status="сгорело")
        result["interest"] = {
            "rows": [],
            "total": sum(p.get("interest") or 0 for p in result["payouts"] if p.get("frozen")),
        }
    result["total_payable"] = sum(
        item["amount"] or 0
        for item in [*result["milestones"], *result["payouts"]]
        if item["calc_status"] == "рассчитано"
    )
    return result


def _calculate_final(case, employee, result, final_plan, params, problems):
    final_input = case["final"]
    fact, closed = final_input.get("fact"), closure_date(final_input)
    sale = case.get("sale")
    if sale:
        fact = closed = sale["work_end"]
    elif handover_active(case) and not fact:
        fact = closed = case["handover_date"]
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
    if sale:
        final["kind"] = "остаток при продаже"
    if handover_active(case):
        day = case["handover_date"]
        final.update(
            amount=0.0,
            raw_amount=0.0,
            calc_status="сгорело",
            k_stage=0.0,
            pay_date=pay_date(day, case.get("holidays", ()), params),
        )
        result["payouts"] = [
            {
                "part": "100%",
                "event_date": day,
                "pay_date": final["pay_date"],
                "bonus": 0.0,
                "interest": 0.0,
                "amount": 0.0,
                "calc_status": "сгорело",
                "npv_checkpoint": None,
            }
        ]
        if employee.get("frozen_payouts"):
            result["payouts"] = [
                {**deepcopy(part), "frozen": True} for part in employee["frozen_payouts"].values()
            ]
        return
    if fact and closed:
        final.update(
            k_stage=stage_factor(min(0, deviation) if leave_date else deviation, params),
            pay_date=pay_date(closed, case.get("holidays", ()), params),
            calc_status="рассчитано",
        )
        if sale:
            final.update(
                k_stage=1.0, pay_date=pay_date(sale["date"], case.get("holidays", ()), params)
            )
        factor = result["k_fin"] if is_kupk(category) else 1
        if is_kupk(category):
            checkpoints = employee.get("npv_checkpoints") or {}
            end_npv = checkpoints.get("final", employee["npv_current"])
            if sale:
                end_npv = sale.get("npv_previous")
            if leave_date and not sale:
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
            if sale:
                base = result["pb"]  # СОВ, сл. 38: формула продажи без множителя БС.
            worked = None
            if leave_date or sale:
                worked = (
                    as_date(sale["work_end"] if sale else leave_date) - as_date(employee["start"])
                ).days + 1
                base = base / result["duration_days"] * worked
            advances = sum(m.get("base_amount", m["amount"]) or 0 for m in result["milestones"])
            raw = base * factor * final["k_stage"] - advances
            if employee.get("redistribution"):
                raw *= redistribution_factor(employee, result["milestones"])
            final.update(raw_amount=raw, amount=max(0, raw))
            if leave_date:
                result["leave"] = {
                    "date": leave_date,
                    "worked_days": worked,
                    "k_stage": final["k_stage"],
                    "k_fin": factor if is_kupk(category) else None,
                    "remainder": final["amount"],
                }
            if not sale and _has_previous_open(final_input, case["milestones"]):
                final["calc_status"] = "ждёт предыдущую веху"
        if (
            not sale
            and deviation > params.early_decision_days
            and not case.get("early_stage_approved", False)
        ):
            final["calc_status"] = "требует решения"
            _problem(problems, "STAGE_EARLY_OVER_100", tab_no)
    parts = final_parts(case, employee, final, params)
    release_dates(case, employee, parts, parts=True)
    result["interest"] = interest_rows(
        case, result["milestones"], parts, result["advance_share"], params
    )
    if apply_net_and_delay(final, parts, params):
        _problem(problems, "MANUAL_CALC", tab_no)
    if employee.get("frozen_payouts"):
        for index, part in enumerate(parts):
            fixed = frozen_payout(employee, part, index)
            if fixed:
                parts[index] = {**fixed, "frozen": True}
        for row in result["interest"]["rows"]:
            for index, part in enumerate(row["parts"]):
                fixed = (
                    employee.get("frozen_interest", {})
                    .get(parts[index]["part"], {})
                    .get(row["milestone"])
                )
                if fixed:
                    row["parts"][index] = dict(fixed)
        result["interest"]["total"] = sum(p["interest"] or 0 for p in parts)
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
    if case.get("redistribution_context"):
        case = deepcopy(case)
        try:
            maps = validate_redistribution(
                case["redistribution_context"], params.redistribution_limit
            )
            for person in case["employees"]:
                person["redistribution"] = maps.get(case["id"], {})
        except ValueError as error:
            for person in case["employees"]:
                person.pop("redistribution", None)
                _problem(problems, "MANUAL_CALC", person["tab_no"])
                problems[-1]["message"] = f"Перераспределение: {error}"
    for milestone in [*case.get("milestones", []), case.get("final") or {}]:
        if bool(milestone.get("fact")) != bool(closure_date(milestone)):
            _problem(problems, "FACT_WITHOUT_1C_CLOSE", None, False, milestone.get("name"))
    employees = [_calculate_employee(case, e, params, problems) for e in case["employees"]]
    if case.get("redistribution_context") and any(p["code"] == "MANUAL_CALC" for p in problems):
        for employee in employees:
            for item in [*employee["milestones"], employee["final"], *employee["payouts"]]:
                if item.get("amount") is not None:
                    item["calc_status"] = "ручной расчёт"
            employee["total_payable"] = 0
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
