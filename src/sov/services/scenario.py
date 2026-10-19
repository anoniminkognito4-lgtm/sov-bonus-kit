"""Чистое моделирование одного назначения. Не имеет доступа к реестру."""

from copy import deepcopy
from datetime import date, timedelta
from math import isfinite

from sov.engine.calculate import calculate_case
from sov.engine.rules.dates import closure_date
from sov.engine.rules.financial import is_kupk
from sov.engine.rules.kpi import milestone_kpis, quarter, quarter_add

FIELDS = {
    "dates",
    "npv_current",
    "npv_checkpoints",
    "kpi_quarterly",
    "bs_share",
    "leave_date",
    "periods",
}


def _number(value, low=None, high=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        raise ValueError("Введите конечное число")
    if (low is not None and value < low) or (high is not None and value > high):
        raise ValueError("Значение вне допустимого диапазона")
    return value


def _day(value):
    return date.fromisoformat(value).isoformat()


def quarterly_periods(case):
    person = case["employees"][0]
    first = quarter(person["start"])
    last = quarter(
        max(m.get("closed_1c") or m.get("fact") or m["plan"] for m in case["milestones"])
    )
    periods = set(person.get("kpi_quarterly", {}))
    while first <= last:
        periods.add(first)
        first = quarter_add(first, 1)
    return sorted(periods)


def scenario_case(case, changes, *, historical=False):
    """Подменить только разрешённые поля копии; пустые изменения сохраняют вход точно."""
    if len(case["employees"]) != 1:
        raise ValueError("Моделирование выполняется по одному назначению сотрудника")
    if set(changes) - FIELDS:
        raise ValueError("Неизвестные поля сценария")
    result = deepcopy(case)
    person, original = result["employees"][0], case["employees"][0]
    targets = {m["name"]: m for m in result["milestones"]}
    targets["final"] = result["final"]
    originals = {m["name"]: m for m in case["milestones"]}
    originals["final"] = case["final"]
    for name, value in changes.get("dates", {}).items():
        if name not in targets:
            raise ValueError("Неизвестная веха")
        if closure_date(originals[name]) and not historical:
            raise ValueError("Для закрытой вехи включите исторический сценарий")
        day = _day(value)
        if targets[name].get("fact") == day and closure_date(targets[name]) == day:
            continue
        targets[name].update(fact=day, closed_1c=day)
        if name == "final":
            result.pop("early_stage_approved", None)
        else:
            result.get("manual_pay_dates", {}).pop(name, None)
    if "npv_current" in changes or "npv_checkpoints" in changes:
        if not is_kupk(person["category"]):
            raise ValueError("NPV моделируется только для КУПК")
    if "npv_current" in changes:
        value = _number(changes["npv_current"])
        person["npv_current"] = value
        person["npv_by_milestone"] = {
            m["name"]: value
            if historical or not closure_date(m)
            else original.get("npv_by_milestone", {}).get(m["name"], original.get("npv_current"))
            for m in case["milestones"]
        }
        checkpoints = person.setdefault("npv_checkpoints", {})
        if historical or not closure_date(case["final"]):
            checkpoints["final"] = value
            if historical:
                person["npv_stage_end"] = value
                person["npv_at_leave"] = value
        else:
            checkpoints.setdefault("final", original.get("npv_current"))
    if "npv_checkpoints" in changes:
        if set(changes["npv_checkpoints"]) - {"q1", "q2"}:
            raise ValueError("Изменять можно только контрольные NPV q1 и q2")
        for key, value in changes["npv_checkpoints"].items():
            person.setdefault("npv_checkpoints", {})[key] = _number(value)
    if "kpi_quarterly" in changes:
        if person["category"] != "МП":
            raise ValueError("KPI моделируется только для МП")
        values = changes["kpi_quarterly"]
        if set(values) - set(quarterly_periods(result)):
            raise ValueError("Квартал KPI вне периода назначения")
        person.setdefault("kpi_quarterly", {}).update(
            {key: _number(value, 0, 1.5) for key, value in values.items()}
        )
    if (
        person["category"] == "МП"
        and person.get("kpi_quarterly")
        and (changes.get("dates") or "kpi_quarterly" in changes)
    ):
        person["kpi"] = milestone_kpis(result["milestones"], person["kpi_quarterly"])
    if "bs_share" in changes:
        if not case.get("bs", {}).get("enabled") or person["category"] not in ("КУПК", "РУПК"):
            raise ValueError("Перевыставление не применяется к этому назначению")
        value = _number(changes["bs_share"], 0, 1)
        result["bs_by_milestone"] = {
            m["name"]: value
            if historical or not closure_date(m)
            else case.get("bs_by_milestone", {}).get(m["name"], case["bs"].get("share_transferred"))
            for m in case["milestones"]
        }
        if historical or not closure_date(case["final"]):
            result["bs"]["share_transferred"] = value
    if "leave_date" in changes:
        value = changes["leave_date"]
        end = case["final"].get("fact") or case["final"]["plan"]
        if value is not None and not person["start"] <= _day(value) <= end:
            raise ValueError("Дата ухода должна быть в пределах назначения и завершения этапа")
        person["leave_date"] = value
    for index, values in changes.get("periods", {}).items():
        if not isinstance(index, int) or not 0 <= index < len(person["periods"]):
            raise ValueError("Неизвестный период БГБ / загрузки")
        if set(values) - {"bgb", "load"}:
            raise ValueError("Изменять можно только БГБ и загрузку периода")
        for key, value in values.items():
            person["periods"][index][key] = _number(value, 0, 1 if key == "load" else None)
    return result


def nearest_payment(employee, as_of):
    _day(as_of)
    rows = [
        r
        for r in [*employee["milestones"], *employee["payouts"]]
        if r.get("pay_date")
        and r["pay_date"] >= as_of
        and r["calc_status"] == "рассчитано"
        and r.get("amount")
    ]
    first = min((r["pay_date"] for r in rows), default=None)
    return {"date": first, "amount": sum(r["amount"] for r in rows if r["pay_date"] == first)}


def day_prices(case, *, historical=False):
    """Цена дня опережения через тот же движок; в зоне нуля по сроку — ноль (UI §4)."""
    person = case["employees"][0]
    prices = []
    for name, milestone in [
        *((m["name"], m) for m in case["milestones"]),
        ("final", case["final"]),
    ]:
        if closure_date(milestone) and not historical:
            continue
        plan = person.get("individual_dates", {}).get(name, milestone.get("plan"))
        if not plan:
            continue
        day = milestone.get("fact") or plan

        def amount(value):
            projected = scenario_case(case, {"dates": {name: value}}, historical=True)
            employee = calculate_case(projected)["employees"][0]
            if name == "final":
                return employee["final"]["amount"]
            return next((m["amount"] for m in employee["milestones"] if m["name"] == name), None)

        current = amount(day)
        earlier = amount((date.fromisoformat(day) - timedelta(days=1)).isoformat())
        value = earlier - current if current is not None and earlier is not None else None
        if name != "final" and (date.fromisoformat(day) - date.fromisoformat(plan)).days >= 100:
            value = 0.0 if current is not None else None
        prices.append({"name": milestone["name"], "date": day, "amount": value})
    return prices


def comparison_rows(baseline, scenario):
    """Сопоставление по идентификаторам частей; неизвестная сумма не равна нулю."""
    rows = []

    def add(label, left, right):
        a, b = left.get("amount"), right.get("amount")
        rows.append(
            {
                "Показатель": label,
                "Сейчас, ₽": a,
                "По сценарию, ₽": b,
                "Разница, ₽": b - a if a is not None and b is not None else None,
                "Статус сейчас": left.get("calc_status"),
                "Статус сценария": right.get("calc_status"),
                "Выплата сейчас": left.get("pay_date"),
                "Выплата сценария": right.get("pay_date"),
            }
        )

    left_ms = {m["name"]: m for m in baseline["milestones"]}
    for item in scenario["milestones"]:
        add(item["name"], left_ms.get(item["name"], {}), item)
    add("Финал (без процентов)", baseline["final"], scenario["final"])
    left = {p["part"]: p for p in baseline["payouts"]}
    right = {p["part"]: p for p in scenario["payouts"]}
    for name in dict.fromkeys([*left, *right]):
        add(f"Финал: {name} (с процентами)", left.get(name, {}), right.get(name, {}))
    add(
        "Проценты",
        {"amount": baseline["interest"]["total"]},
        {"amount": scenario["interest"]["total"]},
    )
    add(
        "Итого за этап",
        {"amount": baseline["total_payable"]},
        {"amount": scenario["total_payable"]},
    )
    return rows


def simulate(case, changes, *, as_of, historical=False):
    """Общий движок на исходном и подменённом входе, без текущей даты и побочных эффектов."""
    projected = scenario_case(case, changes, historical=historical)
    baseline, scenario = calculate_case(case), calculate_case(projected)
    left, right = baseline["employees"][0], scenario["employees"][0]
    eligible = {
        m["name"] for m in [*case["milestones"], case["final"]] if historical or not closure_date(m)
    }
    return {
        "case": projected,
        "baseline": baseline,
        "scenario": scenario,
        "rows": comparison_rows(left, right),
        "prices": [r for r in day_prices(projected, historical=True) if r["name"] in eligible],
        "nearest_now": nearest_payment(left, as_of),
        "nearest_scenario": nearest_payment(right, as_of),
        "as_of": as_of,
    }
