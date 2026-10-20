"""Финансовые коэффициенты: CALC_RULES §4–6; вариант правила низкого NPV — RULE_CHANGES §1."""

from sov.engine.params import DEFAULT_PARAMS, SovParams

CATEGORIES = ("КУПК (РП/ЗРП)", "КУПК", "РУПК", "МП")


def is_kupk(category: str) -> bool:
    return category in CATEGORIES[:2]


def k_fin(
    category: str, npv_start: float, npv_current: float, params: SovParams = DEFAULT_PARAMS
) -> float:
    threshold = params.low_npv_threshold
    low = {
        "start_lt": npv_start < threshold,
        "abs_current_lt": abs(npv_current) < threshold,
        "any_le": not (npv_start > threshold and npv_current > threshold),
    }[params.low_npv_trigger]
    if low:
        delta = npv_current - npv_start
        value = (
            1 + params.low_npv_slope * delta / params.low_npv_step
            if abs(delta) > params.low_npv_delta
            else 1
        )
    else:
        value = npv_current / npv_start
    low, high = params.rp_financial_bounds if category == CATEGORIES[0] else params.financial_bounds
    return min(high, max(low, value))


def bs_multiplier(
    category: str, bs_enabled: bool, share: float | None, params: SovParams = DEFAULT_PARAMS
) -> float:
    if not bs_enabled or category not in ("КУПК", "РУПК"):
        return 1.0
    if share is None:
        raise ValueError("Не задана доля перевыставления БС")
    return params.bs_base + params.bs_variable * min(
        1, max(0, (share - params.bs_threshold) * params.bs_slope)
    )


def kpi_factor(kpi: float, params: SovParams = DEFAULT_PARAMS) -> float:
    """И-3: множитель KPI не опускается ниже нуля."""
    return max(params.kpi_floor, (kpi - params.kpi_threshold) * params.kpi_slope)


def milestone_factor(
    category: str,
    deviation_days: int,
    k_fin: float | None,
    kpi: float | None,
    params: SovParams = DEFAULT_PARAMS,
) -> float:
    if deviation_days <= -params.timing_scale_days:
        return 0.0
    if is_kupk(category):
        factor = k_fin
    elif category == "МП":
        factor = kpi_factor(kpi, params)
    else:
        factor = 1
    return min(1 + deviation_days / params.timing_scale_days, params.milestone_cap) * factor


def stage_factor(deviation_days: int, params: SovParams = DEFAULT_PARAMS) -> float:
    return max(0, 1 + deviation_days / params.timing_scale_days)
