"""Части финала, проценты и отрицательный нетто: CALC_RULES §9–10."""

from datetime import timedelta

from sov.engine.params import SovParams
from sov.engine.rules.dates import as_date, pay_date
from sov.engine.rules.financial import is_kupk
from sov.engine.rules.rates import rate_integral, rate_on


def final_parts(case: dict, employee: dict, final: dict, params: SovParams) -> list[dict]:
    if final["amount"] is None:
        return []
    kupk = is_kupk(employee["category"])
    expense = case.get("stage5_expense") or {}
    split_expense = (
        kupk
        and case["stage"] == 5
        and employee.get("role") in ("РП", "РПС")
        and expense.get("remaining")
    )
    checkpoints = employee.get("npv_checkpoints") or {}
    npv_final = checkpoints.get("final", employee.get("npv_current"))
    shares = (
        params.stage5_shares
        if split_expense
        else params.final_shares
        if kupk and case["stage"] != 5
        else (1.0,)
    )
    labels = (
        ("50%", "50% (2)")
        if split_expense
        else (("50%", "25% (1)", "25% (2)") if len(shares) == 3 else ("100%",))
    )
    result = []
    for index, (label, share) in enumerate(zip(labels, shares, strict=True)):
        offset = (2 if split_expense and index else index) * params.checkpoint_days
        event = (as_date(final["fact"]) + timedelta(days=offset)).isoformat()
        status = final["calc_status"]
        checkpoint = None
        if len(shares) == 3:
            checkpoint = npv_final if index == 0 else checkpoints.get(f"q{index}")
            if checkpoint is None or npv_final is None:
                status = "нет данных"
            elif index and checkpoint < npv_final:
                status = "приостановлено"
        part = {
            "part": label,
            "event_date": event,
            "pay_date": (
                final["pay_date"]
                if index == 0
                else pay_date(event, case.get("holidays", ()), params)
            ),
            "bonus": final["amount"] * share,
            "interest": 0.0,
            "calc_status": status,
            "npv_checkpoint": checkpoint,
            "_share": share,
        }
        if split_expense and index:
            if expense.get("plan_budget") is None or not expense.get("fact_budget"):
                part["calc_status"] = "нет данных"
                part["bonus"] = None
            else:
                part["k_expense"] = min(1, expense["plan_budget"] / expense["fact_budget"])
                part["bonus"] *= part["k_expense"]
        # Решение/порядок закрытия важнее условия NPV для допуска в выплату.
        if final["calc_status"] != "рассчитано":
            part["calc_status"] = final["calc_status"]
        part["amount"] = part["bonus"]
        result.append(part)
    return result


def interest_rows(
    case: dict, milestones: list[dict], parts: list[dict], advance_share: float, params: SovParams
) -> dict:
    rows = []
    for milestone in milestones:
        if milestone["amount"] is None or not milestone["pay_date"]:
            continue
        rate = case.get("key_rates", {}).get(milestone["name"], case.get("key_rate"))
        if "key_rate_history" in case:
            rate = rate_on(milestone["pay_date"], case["key_rate_history"])
        deferred = milestone["amount"] / advance_share * (1 - advance_share)
        row = {
            "milestone": milestone["name"],
            "advance": milestone["amount"],
            "pay_date": milestone["pay_date"],
            "deferred": deferred,
            "rate": rate,
            "parts": [],
        }
        for part in parts:
            days = (as_date(part["pay_date"]) - as_date(milestone["pay_date"])).days
            integral = (
                rate_integral(milestone["pay_date"], part["pay_date"], case["key_rate_history"])
                if "key_rate_history" in case
                else rate * days
                if rate is not None
                else None
            )
            weighted_rate = integral / days if integral is not None and days else rate
            if not row["parts"] and "key_rate_history" in case:
                row["rate"] = weighted_rate
            if integral is None:
                value = None
                part["calc_status"] = "нет данных"
                part["interest"] = None
            else:
                value = deferred * part["_share"] * integral / params.year_days
                if part["interest"] is not None:
                    part["interest"] += value
            row["parts"].append(
                {"part": part["part"], "days": days, "interest": value, "rate": weighted_rate}
            )
        rows.append(row)
    for part in parts:
        part["amount"] = (
            part["bonus"] + part["interest"]
            if part["bonus"] is not None and part["interest"] is not None
            else None
        )
    return {"rows": rows, "total": sum(p["interest"] or 0 for p in parts)}


def apply_net_and_delay(final: dict, parts: list[dict], params: SovParams) -> bool:
    """И-4 и И-6. Возвращает необходимость ручного расчёта."""
    if not parts:
        return False
    if any(part["interest"] is None for part in parts):
        for part in parts:
            part.pop("_share")
        return False
    raw = final["raw_amount"]
    net = raw + sum(p["interest"] or 0 for p in parts)
    manual = raw < 0 and net > 0 and len(parts) > 1
    if raw < 0:
        for part in parts:
            if manual:
                part["calc_status"] = "ручной расчёт"
            elif net <= 0:
                part.update(bonus=0.0, amount=0.0, calc_status="не выплачивается")
            else:
                part["bonus"] = raw
                part["amount"] = net
        if manual:
            final["calc_status"] = "ручной расчёт"
    if (
        len(parts) == 3
        and parts[1]["calc_status"] == "приостановлено"
        and parts[2]["calc_status"] == "рассчитано"
    ):
        # И-4: проценты уже рассчитаны до исходной даты первой части 25%.
        parts[1].update(pay_date=parts[2]["pay_date"], delayed=True, calc_status="рассчитано")
    for part in parts:
        part.pop("_share")
    return manual
