"""Чистые календарные правила, без обращения к дате компьютера."""

import re
from datetime import date, timedelta

from sov.engine.params import DEFAULT_PARAMS, SovParams


def as_date(value: str) -> date:
    return date.fromisoformat(value)


def pay_date(fact: str, holidays=(), params: SovParams = DEFAULT_PARAMS, workdays=()) -> str:
    """Дата выплаты; holidays — нерабочие будни, workdays — рабочие субботы и воскресенья."""
    event = as_date(fact)
    month_index = event.year * 12 + ((event.month - 1) // 3 + 1) * 3 - 1
    month_index += params.pay_months_after_quarter
    year, month = divmod(month_index, 12)
    result = date(year, month + 1, params.pay_day)
    while (result.weekday() >= 5 and result.isoformat() not in workdays) or (
        result.isoformat() in holidays
    ):
        result -= timedelta(days=1)
    return result.isoformat()


def milestone_part(name: str) -> int | None:
    """Номер части вехи из двух частей по хвосту «(часть 1)» / «(часть 2)» (сл. 55, п. 19)."""
    match = re.search(r"\(\s*часть\s*([12])\s*\)\s*$", name or "", re.IGNORECASE)
    return int(match.group(1)) if match else None


def milestone_weights(
    start: str,
    continues_from_prev_stage: bool,
    new_stage_start: str | None,
    plans: list[str],
    individual: dict,
    params: SovParams = DEFAULT_PARAMS,
    parts: list | None = None,
) -> dict:
    """Вес вехи = 1 ÷ число участвующих вех; часть вехи — половина веса одной вехи."""
    continued = continues_from_prev_stage and start == new_stage_start
    cutoff = as_date(start) + timedelta(days=params.newcomer_days)
    participates = [
        continued or as_date(individual.get(str(i), plan)) >= cutoff for i, plan in enumerate(plans)
    ]
    sizes = [0.5 if part else 1 for part in (parts or [None] * len(plans))]
    units = sum(size for size, taking in zip(sizes, participates, strict=True) if taking)
    weights = [
        size / units if taking and units else None
        for size, taking in zip(sizes, participates, strict=True)
    ]
    return {
        "participates": participates,
        "weight": 1 / units if units else None,
        "weights": weights,
    }


def closure_date(milestone: dict) -> str | None:
    """И-8: явная дата закрытия 1С, иначе фактическая дата тестового кейса."""
    return milestone.get("closed_1c", milestone.get("fact"))
