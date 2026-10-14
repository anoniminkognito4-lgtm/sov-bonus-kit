"""ПБ и проверка полного покрытия периода: CALC_RULES §2."""

from datetime import timedelta

from sov.engine.params import SovParams
from sov.engine.rules.dates import as_date


def project_bonus(periods: list[dict], start: str, end: str, params: SovParams) -> tuple:
    first, last = as_date(start), as_date(end)
    cursor = first
    total = 0.0
    issues = set()
    for period in sorted(periods, key=lambda p: p["from"]):
        begin, finish = max(first, as_date(period["from"])), min(last, as_date(period["to"]))
        if finish < begin:
            continue
        if begin != cursor:
            issues.add("BGB_GAP" if begin > cursor else "ASSIGNMENT_INCOMPLETE")
        if period.get("bgb") is None:
            issues.add("BGB_GAP")
        elif period.get("load") is None:
            issues.add("ASSIGNMENT_INCOMPLETE")
        else:
            total += period["bgb"] / params.year_days * ((finish - begin).days + 1) * period["load"]
        cursor = max(cursor, finish + timedelta(days=1))
    if cursor <= last:
        issues.add("BGB_GAP")
    return (None if issues else total), issues
