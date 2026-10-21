"""Новый калькулятор: отбор вех из хранилища, команда, создание (docs/NEW_CALC.md §3–§6).

Калькулятор в системе — этап очереди (`stage`), зафиксированный набор вех
(`calc_milestone`) и назначения. Функции отбора чистые: на входе строки реестра.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from sov.engine.rules.kpi import quarter
from sov.loaders.mapping import assignment_id
from sov.loaders.milestones import clean

MESSAGES = {
    "NEWCALC_EXISTS": "Калькулятор для этой тройки уже есть",
    "NEWCALC_NO_KEY": "У очереди и этапа нет ключевой вехи",
    "NEWCALC_MULTI_KEY": "Несколько ключевых вех, взята самая ранняя",
    "NEWCALC_AFTER_KEY": "Веха очереди позже ключевой",
    "NEWCALC_QUEUE_TEXT": "Текст очереди в 1С отличается пробелами или регистром",
    "NEWCALC_SET_CHANGED": "Отбор дал бы другой набор вех: нужно решение calc_set_change",
    "NEWCALC_STAGE_DUE": "Пора создать калькулятор следующего этапа",
    "MS_NO_PLAN_DATE": "У вехи нет плановой даты",
}
BLOCKING = {"NEWCALC_EXISTS", "NEWCALC_NO_KEY"}


def _decisions(rows, kind):
    chosen = [r for r in rows if r["kind"] == kind]
    return [json.loads(r["payload_json"]) for r in sorted(chosen, key=lambda r: r["created_at"])]


def _next_day(value):
    return (date.fromisoformat(value) + timedelta(days=1)).isoformat()


@dataclass
class Selection:
    key: dict | None = None
    milestones: list = field(default_factory=list)  # промежуточные, по порядку строк
    excluded: list = field(default_factory=list)  # (веха, причина)
    problems: list = field(default_factory=list)

    @property
    def blocking(self):
        return [p for p in self.problems if p["blocking"]]

    @property
    def codes(self):
        return [m["milestone_code"] for m in self.milestones] + (
            [self.key["milestone_code"]] if self.key else []
        )


class Store:
    """Срез хранилища вех и реестра для отбора (строки registry.rows)."""

    def __init__(self, rows):
        self.rows = rows
        self.milestones = rows("milestone")
        self.plans = {}
        for version in rows("milestone_plan_version"):
            current = self.plans.get(version["milestone_code"])
            if current is None or version["version"] > current["version"]:
                self.plans[version["milestone_code"]] = version
        decisions = rows("manual_decision")
        self.inactive = {p["milestone_code"] for p in _decisions(decisions, "milestone_inactive")}
        self.status_alias = {p["raw"]: p["status"] for p in _decisions(decisions, "status_alias")}
        self.project_aliases = _decisions(decisions, "project_alias")
        self.stages = {(s["project_code"], s["queue"], s["stage"]): s for s in rows("stage")}
        self.calc = {}
        for row in rows("calc_milestone"):
            key = (row["project_code"], row["queue"], row["stage"])
            self.calc.setdefault(key, []).append(row)

    def plan(self, code):
        return (self.plans.get(code) or {}).get("plan_date")

    def status_state(self, status):
        """None — рабочий; иначе причина, по которой веха не входит."""
        if not status or status in ("Назначена", "Выполнена"):
            return None
        meaning = self.status_alias.get(status)
        if meaning == "inactive":
            return "снята"
        if meaning in ("assigned", "done"):
            return None
        return f"неизвестный статус 1С «{status}»"


def select_milestones(store, project, queue, stage):
    """Отбор вех нового калькулятора по правилам макроса (NEW_CALC §4.1, §4.2)."""
    result = Selection()

    def problem(code, **extra):
        result.problems.append(
            {"code": code, "blocking": code in BLOCKING, "message": MESSAGES[code], **extra}
        )

    if (project, queue, stage) in store.stages:
        problem("NEWCALC_EXISTS")
    candidates = []
    for m in store.milestones:
        if m["project_code"] != project or m["stage"] != stage or m["queue"] not in (queue, ""):
            continue
        reason = store.status_state(m["status"])
        if m["milestone_code"] in store.inactive:
            reason = "снята"
        if reason:
            result.excluded.append((m, reason))
            continue
        if not store.plan(m["milestone_code"]):
            result.excluded.append((m, "нет плановой даты"))
            problem("MS_NO_PLAN_DATE", milestone=m["name"], milestone_code=m["milestone_code"])
            continue
        candidates.append(m)
        if m["queue"] and m["queue_raw"] and m["queue_raw"] != queue:
            if clean(m["queue_raw"]).lower() == clean(queue).lower():
                problem("NEWCALC_QUEUE_TEXT", milestone=m["name"], queue_raw=m["queue_raw"])
    keys = sorted(
        (m for m in candidates if m["is_key"] and m["queue"] == queue),
        key=lambda m: (store.plan(m["milestone_code"]), m["milestone_code"]),
    )
    if not keys:
        problem("NEWCALC_NO_KEY")
    if len(keys) > 1:
        problem("NEWCALC_MULTI_KEY", milestones=[m["name"] for m in keys])
    result.key = keys[0] if keys else None
    key_date = store.plan(result.key["milestone_code"]) if result.key else None
    chosen = []
    for m in candidates:
        if m is result.key:
            continue
        if m["is_key"] and m["queue"] == queue:
            result.excluded.append((m, "вторая ключевая веха"))
            continue
        if m["queue"] == queue:
            chosen.append(m)
            if key_date and store.plan(m["milestone_code"]) > key_date:
                problem(
                    "NEWCALC_AFTER_KEY", milestone=m["name"], milestone_code=m["milestone_code"]
                )
        elif key_date and store.plan(m["milestone_code"]) <= key_date:
            chosen.append(m)  # веха проекта без очереди не позже ключевой
        else:
            result.excluded.append(
                (m, "веха проекта позже ключевой" if key_date else "нет ключевой")
            )
    # Порядок: плановая дата, затем порядок в выгрузке (код присвоен в порядке строк).
    result.milestones = sorted(
        chosen, key=lambda m: (store.plan(m["milestone_code"]), m["milestone_code"])
    )
    return result


def stage_boundary(store, project, queue, stage, tab_no=None, individual=None):
    """Граница этапа: план ключевой вехи (или индивидуальный срок сотрудника)."""
    keys = [
        m
        for m in store.milestones
        if (m["project_code"], m["queue"], m["stage"]) == (project, queue, stage) and m["is_key"]
    ]
    if not keys:
        return None
    key = min(keys, key=lambda m: store.plan(m["milestone_code"]) or "9999-12-31")
    if tab_no and individual:
        own = individual.get((tab_no, key["milestone_code"]))
        if own:
            return own
    return store.plan(key["milestone_code"])


def default_header(store, project, queue, stage):
    previous = stage_boundary(store, project, queue, stage - 1) if stage > 1 else None
    return {
        "start_date": _next_day(previous) if previous else None,
        "continues_from_prev": bool(previous),
        "is_completion": False,
    }


def team_proposals(rows, store, project, queue, stage, start_date):
    """Переход с этапа N−1: живые назначения без даты ухода (NEW_CALC §5)."""
    individual = {
        (r["tab_no"], r["milestone_code"]): r["individual_plan"] for r in rows("individual_date")
    }
    people = {e["tab_no"]: e for e in rows("employee")}
    result = []
    for a in rows("assignment"):
        if (a["project_code"], a["queue"], a["stage"]) != (project, queue, stage - 1):
            continue
        if a["end_reason"]:
            continue
        boundary = stage_boundary(store, project, queue, stage - 1, a["tab_no"], individual)
        begin = _next_day(boundary) if boundary else start_date
        result.append(
            {
                "tab_no": a["tab_no"],
                "fio": people.get(a["tab_no"], {}).get("fio"),
                "category": a["category"],
                "role": a["role"],
                "position": a["position"],
                "sov_type": a["sov_type"],
                "date_from": begin,
                "individual": boundary != stage_boundary(store, project, queue, stage - 1),
                "source": f"этап {stage - 1}",
            }
        )
    return result


def build_records(rows, store, project, queue, stage, header, team, author):
    """Записи одной операции «Создать»: этап, набор вех, назначения, стартовый NPV."""
    selection = select_milestones(store, project, queue, stage)
    if selection.blocking:
        raise ValueError("; ".join(p["message"] for p in selection.blocking))
    if not header.get("start_date"):
        raise ValueError("Укажите старт этапа")
    key = selection.key
    records = [
        (
            "stage",
            {
                "project_code": project,
                "queue": queue,
                "stage": stage,
                "start_date": header["start_date"],
                "key_milestone_code": key["milestone_code"],
                "key_milestone_name": key["name"],
                "is_completion": int(bool(header.get("is_completion"))),
                "continues_from_prev": int(bool(header.get("continues_from_prev"))),
            },
        )
    ]
    for order, m in enumerate([*selection.milestones, key], 1):
        records.append(
            (
                "calc_milestone",
                {
                    "project_code": project,
                    "queue": queue,
                    "stage": stage,
                    "milestone_code": m["milestone_code"],
                    "order": order,
                    "source": "queue" if m["queue"] else "project",
                    "added_by": f"создание калькулятора, {author}",
                    "active": 1,
                },
            )
        )
    npv = {(r["project_code"], r["quarter"]): r for r in rows("npv")}
    for person in team:
        identity = assignment_id(person["tab_no"], project, queue, stage)
        from_prev = person.get("source", "").startswith("этап")
        records.append(
            (
                "assignment",
                {
                    "id": identity,
                    "tab_no": person["tab_no"],
                    "project_code": project,
                    "queue": queue,
                    "stage": stage,
                    "sov_type": person["sov_type"],
                    "category": person["category"],
                    "role": person["role"],
                    "position": person["position"],
                    "date_from": person["date_from"],
                    "date_to": None,
                    "end_reason": None,
                    "from_prev_stage": int(from_prev),
                },
            )
        )
        if person["category"].startswith("КУПК"):
            records.extend(start_npv_records(npv, identity, project, stage, person["date_from"]))
    return records, selection


def start_npv_records(npv, identity, project, stage, date_from):
    """Сл. 22: стартовый NPV этапа — по ФМ квартала накануне старта. npv — {(проект, квартал)}."""
    fixed = (date.fromisoformat(date_from) - timedelta(days=1)).isoformat()
    source = npv.get((project, quarter(fixed)))
    if not source or source["npv_forecast_paur"] is None:
        return []
    return [
        (
            "start_npv",
            {
                "assignment_id": identity,
                "npv_start": source["npv_forecast_paur"],
                "fixed_date": fixed,
                "basis": f"старт этапа {stage}, ФМ {quarter(fixed)}",
                "corrections": None,
            },
        )
    ]


def set_changes(store):
    """NEWCALC_SET_CHANGED: калькуляторы, для которых отбор дал бы другой набор."""
    result = []
    for (project, queue, stage), rows in sorted(store.calc.items()):
        current = {r["milestone_code"] for r in rows if r["active"]}
        selection = select_milestones(store, project, queue, stage)
        wanted = set(selection.codes)
        add, remove = sorted(wanted - current), sorted(current - wanted)
        if add or remove:
            result.append(
                {
                    "code": "NEWCALC_SET_CHANGED",
                    "blocking": False,
                    "message": MESSAGES["NEWCALC_SET_CHANGED"],
                    "calc": f"{project}/{queue}/{stage}",
                    "add": add,
                    "remove": remove,
                }
            )
    return result


def set_change_records(store, payload):
    """Записи решения calc_set_change: добавить и убрать вехи набора."""
    project, queue, stage = payload["calc"].rsplit("/", 2)
    stage = int(stage)
    rows = store.calc.get((project, queue, stage))
    if not rows:
        raise ValueError("Калькулятор не найден")
    existing = {r["milestone_code"]: r for r in rows}
    codes = {m["milestone_code"]: m for m in store.milestones}
    order = max(r["order"] for r in rows)
    records = []
    for code in payload.get("add", []):
        if code not in codes:
            raise ValueError(f"Веха {code} не найдена в хранилище")
        order += 1
        base = existing.get(code) or {
            "project_code": project,
            "queue": queue,
            "stage": stage,
            "milestone_code": code,
            "order": order,
            "source": "queue" if codes[code]["queue"] else "project",
        }
        records.append(
            ("calc_milestone", {**base, "added_by": "решение calc_set_change", "active": 1})
        )
    for code in payload.get("remove", []):
        if code not in existing:
            raise ValueError(f"Вехи {code} нет в наборе калькулятора")
        records.append(("calc_milestone", {**existing[code], "active": 0}))
    return records


def stage_due(store, as_of, horizon_days=92):
    """NEWCALC_STAGE_DUE: этап N+1 пора создать (NEW_CALC §3)."""
    result = []
    limit = (date.fromisoformat(as_of) + timedelta(days=horizon_days)).isoformat()
    groups = {}
    for m in store.milestones:
        if m["queue"]:
            groups.setdefault((m["project_code"], m["queue"]), set()).add(m["stage"])
    for (project, queue), stages in sorted(groups.items()):
        existing = {s for (p, q, s) in store.stages if (p, q) == (project, queue)}
        if not existing:
            result.append(
                {
                    "code": "NEWCALC_STAGE_DUE",
                    "blocking": False,
                    "message": "В хранилище есть вехи очереди, а калькуляторов нет",
                    "project_code": project,
                    "queue": queue,
                    "stage": min(stages),
                }
            )
            continue
        current = max(existing)
        if current + 1 not in stages or (project, queue, current + 1) in store.stages:
            continue
        boundary = stage_boundary(store, project, queue, current)
        if boundary and boundary <= limit:
            result.append(
                {
                    "code": "NEWCALC_STAGE_DUE",
                    "blocking": False,
                    "message": MESSAGES["NEWCALC_STAGE_DUE"],
                    "project_code": project,
                    "queue": queue,
                    "stage": current + 1,
                    "key_date": boundary,
                }
            )
    return result


BLOCKS = ("БПП", "БПСК", "ДРП", "БС")
DEFAULT_SETTINGS = {
    "templates": {block: "reference/template/bonus_template_empty.xlsx" for block in BLOCKS},
    "folder": "build/calculators",
    "name": "bonus_{project}_{queue}_{stage}",
}


def project_block(blocks_text):
    """Блок шаблона по полю «Блоки» проекта; по умолчанию БПП."""
    text = (blocks_text or "").upper()
    return next((b for b in ("БПСК", "ДРП", "БС", "БПП") if re.search(rf"\b{b}\b", text)), "БПП")


def latest_raw_rows(raw_rows, names, project_code):
    """Строки выгрузки проекта: по каждой вехе — последняя, в порядке выгрузки (NEW_CALC §7).

    `raw_rows` — [(загрузка, номер строки, словарь)] от новых загрузок к старым;
    `names` — названия проекта в 1С (из реестра и справочника project_alias)."""
    wanted = {clean(n).lower() for n in names}
    seen, result = set(), []
    for _, _, raw in raw_rows:
        code = clean(raw.get("Код проекта"))
        if code != project_code and not (not code and clean(raw.get("Проект")).lower() in wanted):
            continue
        key = (
            clean(raw.get("Очередь/корпус/строение")).lower(),
            clean(raw.get("Этап")).lower(),
            clean(raw.get("Наименование")).lower().replace("ё", "е"),
        )
        if key not in seen:
            seen.add(key)
            result.append(raw)
    return result


def synthetic_rows(store, project_code, project_name):
    """Если строк выгрузки нет (вехи загружены с кодами): строки из хранилища."""
    words = {
        1: "Первый этап",
        2: "Второй этап",
        3: "Третий этап",
        4: "Четвертый этап",
        5: "Пятый этап",
    }
    rows = []
    for m in sorted(
        (m for m in store.milestones if m["project_code"] == project_code),
        key=lambda m: m["milestone_code"],
    ):
        rows.append(
            {
                "Статус": m["status"],
                "Наименование": m["name"],
                "Шаблон": m.get("template"),
                "Ключевая": "Да" if m["is_key"] else "Нет",
                "Вид": m.get("kind"),
                "Проект": project_name,
                "Очередь/корпус/строение": m.get("queue_raw")
                if m.get("queue_raw") is not None
                else m["queue"],
                "Этап": words[m["stage"]],
                "Плановый срок": store.plan(m["milestone_code"]),
                "Фактический срок": m["fact_date"],
            }
        )
    return rows


def calc_file_data(store, project_row, queue, stage, raw_rows, header=None):
    """Данные файла калькулятора из зафиксированного набора (или отбора, если набора нет)."""
    project, name = project_row["project_code"], project_row["name"]
    fixed = [
        r
        for r in sorted(store.calc.get((project, queue, stage), []), key=lambda r: r["order"])
        if r["active"]
    ]
    by_code = {m["milestone_code"]: m for m in store.milestones}
    if fixed:
        items = [by_code[r["milestone_code"]] for r in fixed if r["milestone_code"] in by_code]
        key = next((m for m in reversed(items) if m["is_key"] and m["queue"] == queue), None)
        middle = [m for m in items if m is not key]
    else:
        selection = select_milestones(store, project, queue, stage)
        key, middle = selection.key, selection.milestones
    if key is None:
        raise ValueError(MESSAGES["NEWCALC_NO_KEY"])

    def queue_text(m):
        return m.get("queue_raw") if m.get("queue_raw") is not None else m["queue"]

    names = {name} | {p["from"] for p in store.project_aliases if p["to"] == project}
    sheet_rows = latest_raw_rows(raw_rows, names, project) or synthetic_rows(store, project, name)
    # A1 — название проекта как в 1С: по нему формулы шаблона ищут даты.
    name_1c = next((clean(r.get("Проект")) for r in sheet_rows if r.get("Проект")), name)
    return {
        "project_name": name_1c,
        "queue_raw": queue_text(key),
        "stage": stage,
        "milestones": [{"name": m["name"], "queue_raw": queue_text(m)} for m in middle],
        "key": {"name": key["name"], "queue_raw": queue_text(key)},
        "header": header or {},
        "sheet_rows": sheet_rows,
    }
