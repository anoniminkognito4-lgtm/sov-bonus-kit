"""Параметры редакции СОВ v2 (3 кв. 2026); загрузка по должностям — с историей по датам."""

import json
import math
import re
from dataclasses import dataclass, fields, replace

_RPPP = ("Руководитель проекта по продукту", "Руководитель группы разработки продукта")
_MRP = ("Менеджер по разработке продукта",)
_RPP = ("Руководитель проекта проектирования",)
_GIP = ("Главный инженер проекта",)
_GIP_IS = ("Главный инженер проекта ИС",)
_GIP_HELPER = ("Помощник ГИПа", "Помощник главного инженера проекта")
_MANAGERS = tuple(
    f"{level}{role}"
    for role in (
        "менеджер по направлениям",
        "менеджер по благоустройству",
        "менеджер по строительству",
    )
    for level in ("Старший ", "", "Младший ")
)
_DUP = (
    (_RPP, (0.15, 0.5, 0.5, 0.5, 0.05), 0.05, 0.25),
    (_GIP, (0.25, 1, 1, 0.75, 0.5), 0.25, 0.5),
    (_GIP_IS, (0.05, 0.75, 0.75, 0.75, 0.5), 0.25, 0.25),
    (_GIP_HELPER, (0, 1, 1, 0.75, 0.5), 0.25, 0.5),
)
_DUS = (
    (("Руководитель проекта строительства",), (0, 0.25, 0.5, 0.5, 0.5), 0.25, None),
    (("Руководитель строительства",), (0, 0, 1, 1, 1), 0.5, None),
    (("Главный инженер по направлениям",), (0, 0, 0.5, 0.5, 0.5), 0.25, None),
    (_MANAGERS, (0, 0, 1, 1, 1), 0.5, None),
)
POSITION_LOADS = (
    # До редакции v2: ДРП по сообщению пользователя (ждёт подтверждения, D-051);
    # ДУП и ДУС в v2 не менялись.
    ("0001-01-01", "ДРП", _RPPP, (0.5, 0.5, 0.5, 0.1665, 0.1665), None, None),
    ("0001-01-01", "ДРП", _MRP, (1, 1, 1, 0.333, 0.333), None, None),
    *(("0001-01-01", "ДУП", *row) for row in _DUP),
    *(("0001-01-01", "ДУС", *row) for row in _DUS),
    # СОВ v2, сл. 18: снижена загрузка ДРП.
    ("2026-07-01", "ДРП", _RPPP, (0.43, 0.43, 0.43, 0.14, 0.14), 0.07, None),
    ("2026-07-01", "ДРП", _MRP, (1, 1, 0.8, 0.25, 0.25), 0.125, None),
    # СОВ v3 (4 кв. 2026), сл. 18: ДУП на 5 этапе — 20% у ГИП, ГИП ИС, помощника ГИПа.
    ("2026-10-01", "ДУП", _GIP, (0.25, 1, 1, 0.75, 0.2), 0.25, 0.5),
    ("2026-10-01", "ДУП", _GIP_IS, (0.05, 0.75, 0.75, 0.75, 0.2), 0.25, 0.25),
    ("2026-10-01", "ДУП", _GIP_HELPER, (0, 1, 1, 0.75, 0.2), 0.25, 0.5),
)


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
    zrp_threshold: float = 1.0  # D-047, закрывает И-1
    # И-9 (открыто): «буквально» | «только деление на очереди», см. adapters/load.py.
    load_normalization: str = "буквально"
    # Статусы вех из 1С, которые движок понимает; прочие — проблема, веха не учитывается.
    milestone_statuses: tuple[str, ...] = ("Назначена", "Выполнена")
    # Блок таблицы загрузки по подразделению: (блок, слова в названии подразделения).
    department_blocks: tuple = (
        ("ДРП", ("ДРП", "департамент разработки продукта")),
        ("ДУП", ("ДУП", "департамент управления проектированием")),
        ("ДУС", ("ДУС", "ОУС", "управления строительством")),
    )
    # Таблицы загрузки по должностям (сл. 18) с датой начала действия:
    # (с даты, блок, названия должности, этапы 1–5, дострой, ЛЦ и объекты СКБ).
    position_loads: tuple = POSITION_LOADS


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


def _number(value, allow_none=False):
    if value is None:
        return allow_none
    return not isinstance(value, bool) and isinstance(value, (int, float)) and 0 <= value <= 2


def _texts(value):
    return (
        isinstance(value, tuple)
        and bool(value)
        and all(isinstance(v, str) and v.strip() for v in value)
    )


def _free_shape(name, value):
    """Параметры-таблицы произвольной длины: проверка формы вместо сравнения с образцом."""
    if name == "milestone_statuses":
        return _texts(value)
    if name == "department_blocks":
        return isinstance(value, tuple) and all(
            isinstance(v, tuple) and len(v) == 2 and isinstance(v[0], str) and _texts(v[1])
            for v in value
        )
    if name == "position_loads":
        return isinstance(value, tuple) and all(
            isinstance(v, tuple)
            and len(v) == 6
            and isinstance(v[0], str)
            and bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", v[0]))
            and isinstance(v[1], str)
            and _texts(v[2])
            and isinstance(v[3], tuple)
            and len(v[3]) == 5
            and all(_number(x) for x in v[3])
            and _number(v[4], True)
            and _number(v[5], True)
            for v in value
        )
    return None


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
        free = _free_shape(name, parsed)
        if free is False or (free is None and not _compatible(parsed, default)):
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
    if result.load_normalization not in ("буквально", "только деление на очереди"):
        raise ValueError("load_normalization: «буквально» или «только деление на очереди»")
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
