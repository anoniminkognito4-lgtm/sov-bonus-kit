"""Чистые календарные правила, без обращения к дате компьютера."""

from datetime import date, timedelta

from sov.engine.params import DEFAULT_PARAMS, SovParams


def as_date(value: str) -> date:
    return date.fromisoformat(value)


def pay_date(fact: str, holidays=(), params: SovParams = DEFAULT_PARAMS) -> str:
    event = as_date(fact)
    month_index = event.year * 12 + ((event.month - 1) // 3 + 1) * 3 - 1
    month_index += params.pay_months_after_quarter
    year, month = divmod(month_index, 12)
    result = date(year, month + 1, params.pay_day)
    while result.weekday() >= 5 or result.isoformat() in holidays:
        result -= timedelta(days=1)
    return result.isoformat()


def milestone_weights(
    start: str,
    continues_from_prev_stage: bool,
    new_stage_start: str | None,
    plans: list[str],
    individual: dict,
    params: SovParams = DEFAULT_PARAMS,
) -> dict:
    continued = continues_from_prev_stage and start == new_stage_start
    cutoff = as_date(start) + timedelta(days=params.newcomer_days)
    participates = [
        continued or as_date(individual.get(str(i), plan)) >= cutoff for i, plan in enumerate(plans)
    ]
    count = sum(participates)
    return {"participates": participates, "weight": 1 / count if count else None}


def closure_date(milestone: dict) -> str | None:
    """И-8: явная дата закрытия 1С, иначе фактическая дата тестового кейса."""
    return milestone.get("closed_1c", milestone.get("fact"))
