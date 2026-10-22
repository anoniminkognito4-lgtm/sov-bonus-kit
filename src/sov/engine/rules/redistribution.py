"""Проверка денежного баланса ФП/проекта до применения индивидуальных процентов."""

from datetime import date, timedelta
from math import isclose, isfinite

from sov.engine.rules.special import redistribution_factor


def _workdays_before(day, count):
    current = date.fromisoformat(day)
    while count:
        current -= timedelta(days=1)
        if current.weekday() < 5:
            count -= 1
    return current.isoformat()


def validate_redistribution(context, limit=0.3):
    members = {m["id"]: m for m in context["members"]}
    seen, deltas, final_deltas = set(), {}, {}
    maps = {identifier: {} for identifier in members}
    for row in context["rows"]:
        identifier, name, pct = row["assignment_id"], row["milestone"], row["pct"]
        if identifier not in members:
            raise ValueError("Участник перераспределения не принадлежит проекту")
        member = members[identifier]
        if not member.get("fp"):
            raise ValueError("Не задано функциональное подразделение участника")
        key = (identifier, name)
        if key in seen:
            raise ValueError("Веха сотрудника указана дважды")
        seen.add(key)
        if (
            isinstance(pct, bool)
            or not isinstance(pct, (int, float))
            or not isfinite(pct)
            or abs(pct) > limit
        ):
            raise ValueError("Изменение бонуса не может превышать ±30%")
        if member["category"] != "РУПК":
            raise ValueError("Перераспределение действует только для РУПК")
        if not row.get("decided_by") or row["decided_by"] == member["tab_no"]:
            raise ValueError("РФП не может менять бонус себе; укажите его табельный номер")
        if not row.get("approved_date") or not row.get("basis"):
            raise ValueError("Нужны дата согласования и основание")
        leave = member["employee"].get("leave_date") or member["employee"].get("hr_dismissal_date")
        if leave and row["approved_date"] > _workdays_before(leave, 5):
            raise ValueError(
                "Перераспределение применяется только к действующему сотруднику: решение "
                "не позднее чем за 5 рабочих дней до увольнения (СОВ v3, сл. 32)"
            )
        group = (member["project_code"], member["queue"], member["stage"], member["fp"])
        peers = [
            m
            for m in members.values()
            if m["category"] == "РУПК"
            and (m["project_code"], m["queue"], m["stage"], m["fp"]) == group
        ]
        if len({m["tab_no"] for m in peers}) < 2:
            raise ValueError("В ФП проекта нужен как минимум второй РУПК")
        milestone = next((m for m in member["result"]["milestones"] if m["name"] == name), None)
        if not milestone or milestone.get("amount") is None or milestone["amount"] <= 0:
            raise ValueError("Нельзя перераспределить незаработанную или нулевую веху")
        if milestone.get("frozen") and not isclose(
            pct, milestone.get("redistribution_pct", 0), abs_tol=1e-12
        ):
            raise ValueError(
                "Начисленный аванс заморожен; требуется отдельная корректировка следующего квартала"
            )
        if milestone["amount"] * (1 + pct) <= 0:
            raise ValueError("Нельзя уменьшить бонус до нуля")
        balance_key = (*group, name)
        deltas[balance_key] = (
            deltas.get(balance_key, 0) + milestone.get("base_amount", milestone["amount"]) * pct
        )
        maps[identifier][name] = pct
    for identifier, values in maps.items():
        if not values:
            continue
        member = members[identifier]
        original = member["result"]["final"].get("amount")
        if original is None:
            # Для незавершённого этапа баланс финала проверяется заново при закрытии.
            continue
        factor = redistribution_factor(
            {**member["employee"], "redistribution": values}, member["result"]["milestones"]
        )
        group = (member["project_code"], member["queue"], member["stage"], member["fp"])
        final_deltas[group] = final_deltas.get(group, 0) + original * (factor - 1)
    if any(not isclose(value, 0, abs_tol=1e-7) for value in deltas.values()):
        raise ValueError("Денежная сумма изменений за веху по ФП и проекту должна быть нулевой")
    if any(not isclose(value, 0, abs_tol=1e-7) for value in final_deltas.values()):
        raise ValueError("Перераспределение меняет общий фонд финала ФП и проекта")
    return maps
