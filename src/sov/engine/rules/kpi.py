"""Общий перевод квартальных KPI в KPI вех для реестра и моделирования."""

from calendar import monthrange
from datetime import date


def quarter(value):
    day = date.fromisoformat(value)
    return f"{day.year}-Q{(day.month - 1) // 3 + 1}"


def quarter_add(value, offset):
    index = int(value[:4]) * 4 + int(value[-1]) - 1 + offset
    return f"{index // 4}-Q{index % 4 + 1}"


def quarter_end(value):
    year, month = int(value[:4]), int(value[-1]) * 3
    return date(year, month, monthrange(year, month)[1]).isoformat()


def milestone_kpis(milestones, quarterly):
    result, last_quarter = {}, None
    for milestone in sorted(milestones, key=lambda r: r.get("closed_1c") or r["plan"]):
        period = quarter(milestone.get("closed_1c") or milestone.get("fact") or milestone["plan"])
        first = quarter_add(last_quarter, 1) if last_quarter and period > last_quarter else period
        values, cursor = [], first
        while cursor <= period:
            values.append(quarterly.get(cursor))
            cursor = quarter_add(cursor, 1)
        result[milestone["name"]] = (
            sum(values) / len(values) if values and all(v is not None for v in values) else None
        )
        if milestone.get("closed_1c") and result[milestone["name"]] is not None:
            last_quarter = period
    return result
