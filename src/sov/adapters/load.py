"""Загрузка из реестра по правилам СОВ (сл. 14–20, 52; нормирование РП; D-046–D-048).

Чистые функции над строками реестра. Результат — периоды загрузки назначения
в формате таблицы `assignment_load` и проблемы для движка.
"""

import re
from datetime import date, timedelta

from sov.engine.params import DEFAULT_PARAMS
from sov.engine.rules.load import project_load, rp_load

OPEN_END = "9999-12-31"
GAPS = {"LOAD_POSITION_UNKNOWN", "RP_SET_MISSING_PROJECT"}
NORMALIZE_LITERAL = "буквально"
NORMALIZE_QUEUES = "только деление на очереди"


def _norm(text):
    return re.sub(r"\s+", " ", (text or "").lower().replace("ё", "е")).strip()


def _next_day(value):
    return (date.fromisoformat(value) + timedelta(days=1)).isoformat()


def _prev_day(value):
    return (date.fromisoformat(value) - timedelta(days=1)).isoformat()


def department_block(department, params=DEFAULT_PARAMS):
    """Блок таблицы загрузки по названию подразделения или None."""
    text = _norm(department)
    for block, words in params.department_blocks:
        for word in words:
            pattern = _norm(word)
            if re.search(rf"(?<![\w]){re.escape(pattern)}(?![\w])", text):
                return block
    return None


def position_load(block, position, stage, completion, on_date, params=DEFAULT_PARAMS):
    """% загрузки по таблице сл. 18, действующей на дату. None — должности или значения нет."""
    name = _norm(position)
    chosen = None
    for begin, row_block, names, stages, completion_value, _ in params.position_loads:
        if row_block != block or begin > on_date or name not in {_norm(n) for n in names}:
            continue
        if chosen is None or begin >= chosen[0]:
            chosen = (begin, stages, completion_value)
    if chosen is None:
        return None
    return chosen[2] if completion else chosen[1][stage - 1]


def _segments(assignments, extra_dates=()):
    points = {a["date_from"] for a in assignments}
    points |= {_next_day(a["date_to"]) for a in assignments if a["date_to"]}
    points |= set(extra_dates)
    points = sorted(points)
    for index, begin in enumerate(points):
        end = _prev_day(points[index + 1]) if index + 1 < len(points) else OPEN_END
        active = [
            a for a in assignments if a["date_from"] <= begin and (a["date_to"] or OPEN_END) >= end
        ]
        if active:
            yield begin, end, active


def _merge(rows):
    merged = []
    for row in rows:
        if (
            merged
            and merged[-1]["load"] == row["load"]
            and _next_day(merged[-1]["date_to"]) == row["date_from"]
        ):
            merged[-1]["date_to"] = row["date_to"]
        else:
            merged.append(dict(row))
    for row in merged:
        if row["date_to"] == OPEN_END:
            row["date_to"] = None
    return merged


def table_loads(assignments, block, completion_of, params=DEFAULT_PARAMS):
    """Загрузка по таблице должностей с делением на очереди и приведением к 100%.

    И-9 (открыто, параметр load_normalization):
    «буквально» — сумма меньше 100% по всем проектам и очередям приводится к 100%,
    как в действующей макрокниге (=ЕСЛИ(сумма>=100%; загрузка; загрузка/сумма));
    «только деление на очереди» — к 100% приводится только сумма, уменьшенная делением
    на очереди; одиночный проект с процентом таблицы ниже 100% не поднимается.
    """
    dates = {row[0] for row in params.position_loads if row[0] > "0001-01-01"}
    result = {a["id"]: [] for a in assignments}
    issues = {a["id"]: set() for a in assignments}
    for begin, end, active in _segments(assignments, dates):
        values = {}
        for a in active:
            value = position_load(block, a["position"], a["stage"], completion_of(a), begin, params)
            if value is None:
                issues[a["id"]].add("LOAD_POSITION_UNKNOWN")
            values[a["id"]] = value
        queues = {}
        for a in active:
            queues.setdefault(a["project_code"], set()).add(a["queue"])
        shares = {
            a["id"]: values[a["id"]] / len(queues[a["project_code"]])
            for a in active
            if values[a["id"]] is not None
        }
        total = sum(shares.values())
        split = any(len(q) > 1 for q in queues.values())
        literal = params.load_normalization == NORMALIZE_LITERAL
        if (literal or split) and 0 < total < 1 and len(shares) == len(active):
            shares = {key: value / total for key, value in shares.items()}
        for key, value in shares.items():
            result[key].append({"date_from": begin, "date_to": end, "load": value})
    return {k: _merge(v) for k, v in result.items()}, issues


def mp_loads(assignments, params=DEFAULT_PARAMS):
    """МП ОУП (сл. 20): 1 проект — 100%, больше — по 50%; очереди проекта делят его долю."""
    result = {a["id"]: [] for a in assignments}
    for begin, end, active in _segments(assignments):
        queues = {}
        for a in active:
            queues.setdefault(a["project_code"], set()).add(a["queue"])
        share = 1.0 if len(queues) == 1 else params.mp_multiple_project_load
        for a in active:
            result[a["id"]].append(
                {"date_from": begin, "date_to": end, "load": share / len(queues[a["project_code"]])}
            )
    return {k: _merge(v) for k, v in result.items()}


def rp_set_load(rows, role, params=DEFAULT_PARAMS):
    """Нормирование РП/ЗРП по утверждённому набору (D-046, D-047).

    Возвращает загрузку на строку набора (проект, очередь), x, % и признак диапазона.
    """
    coefficients = []
    for row in rows:
        coefficients.append(
            project_load(
                {
                    "class": row["class"],
                    "area_k_m2": row["area_k_m2"],
                    "stage": row["stage"],
                    "extra_objects": row["extra_objects"] or 0,
                    "is_queue": bool(row["is_queue"]),
                    "is_logistics": bool(row["is_logistics"]),
                },
                params,
            )["expected"]
        )
    zrp = role == "ЗРП"
    rank = rows[0]["rank"] if rows[0]["rank"] else (2 if zrp else None)
    if rank is None:
        raise ValueError("В наборе РП не указан ранг")
    load = rp_load(rank, coefficients, params)
    if zrp:
        x = load["x"]
        threshold = params.zrp_threshold
        load["load_pct"] = 1 if x <= threshold else (x / threshold - 1) * 2 + 1
        load["in_range"] = True
    calculators = {}
    for row in rows:
        calculators.setdefault(row["project_code"], []).append(row["queue"])
    per_project = load["load_pct"] / len(calculators)
    per_row = {
        (row["project_code"], row["queue"]): per_project / len(calculators[row["project_code"]])
        for row in rows
    }
    return {
        "x": load["x"],
        "load_pct": load["load_pct"],
        "in_range": load["in_range"],
        "rows": per_row,
    }


class LoadPlanner:
    """Выбор источника загрузки назначения: правило СОВ или файл 1С."""

    def __init__(self, adapter):
        self.adapter = adapter
        self.params = adapter.params
        self._cache = {}

    def _completion(self, assignment):
        stage = self.adapter.rows(
            "stage",
            project_code=assignment["project_code"],
            queue=assignment["queue"],
            stage=assignment["stage"],
        )
        return bool(stage and stage[0]["is_completion"])

    def for_assignment(self, assignment, employee):
        """(строки загрузки или None, источник, проблемы [(код, блокирует)])."""
        tab_no = employee["tab_no"]
        if tab_no not in self._cache:
            self._cache[tab_no] = self._plan(employee)
        planned = self._cache[tab_no].get(assignment["id"])
        file_rows = sorted(
            self.adapter.rows("assignment_load", assignment_id=assignment["id"]),
            key=lambda r: r["date_from"],
        )
        if planned is None:
            return None, "файл назначений", []
        rows, source, codes = planned
        if codes & GAPS:
            # Правило не даёт загрузку: берём файл 1С, если он есть, иначе блокируем.
            if file_rows:
                return None, "файл назначений", [(code, False) for code in sorted(codes)]
            return rows or None, source, [(code, code in GAPS) for code in sorted(codes)]
        issues = [(code, False) for code in sorted(codes)]
        if file_rows and {round(r["load"], 6) for r in file_rows} != {
            round(r["load"], 6) for r in rows
        }:
            issues.append(("LOAD_FILE_DIFFERS", False))
        return rows, source, issues

    def _plan(self, employee):
        assignments = [
            {**a, "position": employee["position"] or a["position"]}
            for a in self.adapter.rows("assignment", tab_no=employee["tab_no"])
        ]
        plan = {}
        block = department_block(employee["department"], self.params)
        table_driven = [
            a for a in assignments if block and a["category"] not in ("КУПК (РП/ЗРП)", "МП")
        ]
        if table_driven:
            loads, issues = table_loads(table_driven, block, self._completion, self.params)
            for a in table_driven:
                source = f"таблица загрузки СОВ ({block}, сл. 18)"
                plan[a["id"]] = (loads[a["id"]], source, issues[a["id"]])
        mp = [a for a in assignments if a["category"] == "МП"]
        if mp:
            for key, rows in mp_loads(mp, self.params).items():
                plan[key] = (rows, "правило МП (сл. 20)", set())
        rp_rows = self.adapter.rows("rp_project_set", tab_no=employee["tab_no"])
        rp = [a for a in assignments if a["category"] == "КУПК (РП/ЗРП)"]
        if rp and rp_rows:
            for a in rp:
                codes = set()
                try:
                    normalized = rp_set_load(rp_rows, a["role"], self.params)
                except (KeyError, ValueError):
                    plan[a["id"]] = ([], "нормирование РП", {"RP_SET_MISSING_PROJECT"})
                    continue
                if not normalized["in_range"]:
                    codes.add("RP_LOAD_OUT_OF_RANGE")
                if any(not r["approved_date"] for r in rp_rows):
                    codes.add("RP_SET_NOT_APPROVED")
                value = normalized["rows"].get((a["project_code"], a["queue"]))
                if value is None:
                    plan[a["id"]] = ([], "нормирование РП", codes | {"RP_SET_MISSING_PROJECT"})
                    continue
                rows = [{"date_from": a["date_from"], "date_to": a["date_to"], "load": value}]
                plan[a["id"]] = (rows, "нормирование РП/ЗРП", codes)
        return plan
