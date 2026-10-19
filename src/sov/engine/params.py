"""Неизменяемая таблица параметров редакции СОВ v2, 3 квартал 2026."""

import json
import math
import re
from dataclasses import dataclass, fields, replace


@dataclass(frozen=True)
class SovParams:
    edition: str = "SOV-v2-2026Q3"
    year_days: int = 365
    advance_shares: tuple[float, ...] = (0.67, 0.67, 0.67, 0.67, 0.33)
    newcomer_days: int = 60
    timing_scale_days: int = 100
    milestone_cap: float = 1.5
    early_decision_days: int = 100
    financial_bounds: tuple[float, float] = (0.75, 1.25)
    rp_financial_bounds: tuple[float, float] = (0.5, 1.5)
    low_npv_threshold: float = 500_000_000
    low_npv_delta: float = 50_000_000
    low_npv_step: float = 100_000_000
    low_npv_slope: float = 0.05
    bs_base: float = 0.9
    bs_variable: float = 0.1
    bs_threshold: float = 0.4
    bs_slope: float = 2
    kpi_threshold: float = 0.5
    kpi_slope: float = 2
    kpi_floor: float = 0  # И-3
    final_shares: tuple[float, ...] = (0.5, 0.25, 0.25)
    stage5_shares: tuple[float, ...] = (0.5, 0.5)
    checkpoint_days: int = 90
    pay_day: int = 10
    pay_months_after_quarter: int = 2
    load_change_threshold: float = 0.25
    amount_change_threshold: float = 0.1
    mp_multiple_project_load: float = 0.5
    class_factors: tuple[tuple[str, float], ...] = (
        ("Элит", 1.1),
        ("Премиум", 1.0),
        ("Бизнес", 0.9),
        ("Бизнес+", 0.9),
        ("Комфорт", 0.8),
    )
    area_bounds: tuple[tuple[str, tuple[float, ...]], ...] = (
        ("Элит", (20, 50, 80, 100)),
        ("Премиум", (50, 100, 200, 300)),
        ("Бизнес", (50, 100, 200, 300)),
        ("Бизнес+", (50, 100, 200, 300)),
        ("Комфорт", (100, 200, 300, 400)),
    )
    size_factors: tuple[float, ...] = (1, 1.2, 1.3, 1.4, 1.5)
    stage_factors: tuple[float, ...] = (0.4, 0.4, 0.6, 0.6, 0.3)
    extra_object_factor: float = 0.05
    queue_factor: float = 0.85
    logistics_factor: float = 0.7
    rank_thresholds: tuple[float, ...] = (1.2, 1, 0.8)
    rank_ranges: tuple[tuple[float, float], ...] = ((1, 1.4), (0.8, 1.2), (0.6, 1))
    load_over_threshold_slope: float = 2
    redistribution_limit: float = 0.3
    sale_extension_days: int = 14


DEFAULT_PARAMS = SovParams()


def _tuples(value):
    return tuple(_tuples(v) for v in value) if isinstance(value, list) else value


def _compatible(value, example):
    if isinstance(example, tuple):
        return (
            isinstance(value, tuple)
            and len(value) == len(example)
            and all(_compatible(v, e) for v, e in zip(value, example, strict=True))
        )
    if isinstance(example, str):
        return isinstance(value, str) and bool(value)
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
        and value >= 0
    )


def params_for_quarter(rows, period):
    """Выбор редакции по effective_from; нет записей — комплектная редакция."""
    if not re.fullmatch(r"\d{4}-Q[1-4]", period):
        raise ValueError("Неверный квартал редакции")
    editions = {}
    for row in rows:
        values = editions.setdefault(row["edition"], {})
        if row["name"] in values:
            raise ValueError("Параметр редакции задан дважды")
        values[row["name"]] = row["value"]
    applicable = []
    for edition, raw in editions.items():
        begin = raw.get("effective_from")
        if not begin or not re.fullmatch(r"\d{4}-Q[1-4]", begin):
            raise ValueError("Редакции требуется effective_from в формате ГГГГ-QN")
        if begin <= period:
            applicable.append((begin, edition, raw))
    if not applicable:
        return DEFAULT_PARAMS
    begin, edition, raw = max(applicable)
    if sum(item[0] == begin for item in applicable) != 1:
        raise ValueError("Несколько редакций начинают действовать в одном квартале")
    field_types = {f.name: f.type for f in fields(SovParams)}
    allowed = set(field_types) - {"edition"}
    if set(raw) - allowed - {"effective_from"}:
        raise ValueError("Неизвестный параметр СОВ")
    changes = {}
    for name, value in raw.items():
        if name == "effective_from":
            continue
        parsed = _tuples(json.loads(value))
        default = getattr(DEFAULT_PARAMS, name)
        if not _compatible(parsed, default):
            raise ValueError(f"Неверное значение или структура параметра {name}")
        if field_types[name] is int and (not isinstance(parsed, int) or isinstance(parsed, bool)):
            raise ValueError(f"Параметр {name} должен быть целым")
        changes[name] = parsed
    result = replace(DEFAULT_PARAMS, edition=edition, **changes)
    if any(
        getattr(result, name) <= 0
        for name in (
            "year_days",
            "timing_scale_days",
            "low_npv_threshold",
            "low_npv_step",
            "checkpoint_days",
            "pay_day",
        )
    ):
        raise ValueError("Делители и интервалы должны быть положительными")
    if any(not isinstance(v, (int, float)) or not 0 < v < 1 for v in result.advance_shares):
        raise ValueError("Доли авансов должны быть между 0 и 1")
    for shares in (result.final_shares, result.stage5_shares):
        if any(
            not isinstance(v, (int, float)) or not 0 < v <= 1 for v in shares
        ) or not math.isclose(sum(shares), 1):
            raise ValueError("Доли финала должны давать 100%")
    if result.redistribution_limit > 0.3 or result.pay_day > 28 or result.milestone_cap < 1:
        raise ValueError("Параметры нарушают допустимые ограничения СОВ")
    if any(
        low > high
        for low, high in (result.financial_bounds, result.rp_financial_bounds, *result.rank_ranges)
    ):
        raise ValueError("Нижняя граница параметра выше верхней")
    if any(v <= 0 for v in result.rank_thresholds):
        raise ValueError("Пороги рангов должны быть положительными")
    return result


def restore_params(payload):
    """Восстановить ровно ту редакцию параметров, которая сохранена в снимке."""
    rows = [{"edition": payload["edition"], "name": "effective_from", "value": "0000-Q1"}]
    rows.extend(
        {"edition": payload["edition"], "name": key, "value": json.dumps(value, ensure_ascii=False)}
        for key, value in payload.items()
        if key != "edition"
    )
    return params_for_quarter(rows, "9999-Q4")
