"""Сопоставление исходных строк по ключам; несопоставленное не записывается."""

import hashlib
import json

from sov.loaders.formats import normalize_bool, normalize_date

KEYS = {
    "employee": ("tab_no",),
    "bgb_period": ("tab_no", "date_from"),
    "project": ("project_code", "queue"),
    "stage": ("project_code", "queue", "stage"),
    "milestone": ("milestone_code",),
    "milestone_plan_version": ("milestone_code", "version"),
    "assignment": ("id",),
    "assignment_load": ("assignment_id", "date_from"),
    "individual_date": ("tab_no", "milestone_code"),
    "npv": ("project_code", "quarter", "fm_version"),
    "start_npv": ("assignment_id",),
    "bs_share": ("project_code", "quarter"),
    "kpi": ("tab_no", "quarter"),
    "stage5_expense": ("project_code", "queue", "stage"),
    "key_rate": ("date_from",),
    "holiday": ("date",),
    "redistribution": ("tab_no", "milestone_code"),
    "rp_project_set": ("tab_no", "project_code", "queue"),
}


class Unmatched(ValueError):
    pass


def row_key(table, row):
    values = [row.get(field) for field in KEYS[table]]
    if any(v is None for v in values):
        raise ValueError(f"Не заполнен ключ {table}: {KEYS[table]}")
    return json.dumps(values, ensure_ascii=False, separators=(",", ":"))


def assignment_id(tab_no, project_code, queue, stage):
    value = json.dumps([tab_no, project_code, queue, stage], ensure_ascii=False)
    return "ASG-" + hashlib.sha256(value.encode()).hexdigest()[:24]


def find(state, table, **fields):
    return [r for r in state[table].values() if all(r.get(k) == v for k, v in fields.items())]


def require(state, table, **fields):
    rows = find(state, table, **fields)
    if len(rows) != 1:
        raise Unmatched(f"Не сопоставлено {table}: {fields} (найдено {len(rows)})")
    return rows[0]


def milestone_for(state, row):
    matches = find(state, "milestone", project_code=row["project_code"], stage=row["stage"])
    name = row["milestone"]
    matches = [m for m in matches if (m["is_key"] if name == "final" else m["name"] == name)]
    if len(matches) != 1:
        raise Unmatched(f"Не сопоставлена веха: {row['project_code']}, {name}")
    return matches[0]


def map_row(kind, row, state):
    warnings = []
    if kind == "milestone":
        row, warnings = map_milestone(row, state)
    if kind in ("bgb_period", "assignment", "kpi", "rp_project_set"):
        require(state, "employee", tab_no=row["tab_no"])
    if kind in ("stage", "assignment", "stage5_expense", "rp_project_set"):
        require(state, "project", project_code=row["project_code"], queue=row["queue"])
    if kind in ("npv", "bs_share") and not find(state, "project", project_code=row["project_code"]):
        raise Unmatched(f"Не сопоставлен проект {row['project_code']}")
    if kind in ("assignment", "milestone", "stage5_expense"):
        require(
            state, "stage", project_code=row["project_code"], queue=row["queue"], stage=row["stage"]
        )
    if kind == "project":
        row = {**row, "handover_date": row.pop("handover_to_completion_date")}
    if kind == "stage":
        keys = find(
            state,
            "milestone",
            project_code=row["project_code"],
            queue=row["queue"],
            stage=row["stage"],
            is_key=1,
        )
        row = {
            **row,
            "key_milestone_code": keys[0]["milestone_code"] if len(keys) == 1 else None,
            "key_milestone_name": row.pop("key_milestone"),
        }
    if kind == "assignment":
        identity = assignment_id(row["tab_no"], row["project_code"], row["queue"], row["stage"])
        previous = find(state, "assignment", id=identity)
        data = {k: v for k, v in row.items() if k != "load"}
        data["position"] = data.pop("position_on_project")
        data["id"] = identity
        if previous:
            old = previous[0]
            if any(old[k] != data[k] for k in ("category", "role", "sov_type")):
                raise ValueError("Изменение категории/роли требует отдельного назначения (M6)")
            data["date_from"] = min(old["date_from"], data["date_from"])
            data["date_to"] = (
                max(old["date_to"], data["date_to"]) if old["date_to"] and data["date_to"] else None
            )
        load = {
            "assignment_id": identity,
            "date_from": row["date_from"],
            "date_to": row["date_to"],
            "load": row["load"],
        }
        return [("assignment", data), ("assignment_load", load)], warnings
    if kind == "start_npv":
        matches = find(
            state,
            "assignment",
            tab_no=row["tab_no"],
            project_code=row["project_code"],
            stage=row["stage"],
        )
        if len(matches) != 1:
            raise Unmatched("Стартовый NPV: назначение не найдено или неоднозначно")
        row = {k: v for k, v in row.items() if k not in ("tab_no", "project_code", "stage")}
        row.update(assignment_id=matches[0]["id"], corrections=None)
    if kind in ("individual_date", "redistribution"):
        require(state, "employee", tab_no=row["tab_no"])
        milestone = milestone_for(state, row)
        row = {k: v for k, v in row.items() if k not in ("project_code", "stage", "milestone")}
        row["milestone_code"] = milestone["milestone_code"]
    if kind == "milestone":
        plan = row.pop("plan_date")
        versions = find(state, "milestone_plan_version", milestone_code=row["milestone_code"])
        latest = max(versions, key=lambda r: r["version"]) if versions else None
        records = [(kind, row)]
        if row["is_key"]:
            stage = require(
                state,
                "stage",
                project_code=row["project_code"],
                queue=row["queue"],
                stage=row["stage"],
            )
            records.append(("stage", {**stage, "key_milestone_code": row["milestone_code"]}))
        if latest is None or latest["plan_date"] != plan:
            records.append(
                (
                    "milestone_plan_version",
                    {
                        "milestone_code": row["milestone_code"],
                        "version": latest["version"] + 1 if latest else 1,
                        "plan_date": plan,
                        "basis": "Загрузка файла вех",
                    },
                )
            )
        return records, warnings
    return [(kind, row)], warnings


def map_milestone(raw, state):
    stages = {
        "Первый этап": 1,
        "Второй этап": 2,
        "Третий этап": 3,
        "Четвертый этап": 4,
        "Четвёртый этап": 4,
        "Пятый этап": 5,
    }
    stage = stages.get(str(raw["Этап"]).strip())
    if stage is None:
        raise ValueError("Не распознан этап вехи")
    warnings = []
    queue = str(raw["Очередь/корпус/строение"] or "1")
    code = raw.get("Код проекта")
    if not code:
        project = require(state, "project", name=raw["Проект"], queue=queue)
        code = project["project_code"]
        warnings.append("Проект сопоставлен по названию, а не коду 1С")
    require(state, "project", project_code=str(code), queue=queue)
    milestone_code = raw.get("Код вехи")
    if not milestone_code:
        matches = find(
            state,
            "milestone",
            project_code=str(code),
            queue=queue,
            stage=stage,
            name=raw["Наименование"],
        )
        if len(matches) == 1:
            milestone_code = matches[0]["milestone_code"]
        elif not matches:
            key = json.dumps([code, queue, stage, raw["Наименование"]], ensure_ascii=False)
            milestone_code = "MS-" + hashlib.sha256(key.encode()).hexdigest()[:24]
        else:
            raise Unmatched("Неоднозначное сопоставление вехи")
        warnings.append("Веха сопоставлена по составному ключу, а не коду 1С")
    fact = normalize_date(raw["Фактический срок"])
    closed = (
        normalize_date(raw.get("Дата закрытия в 1С"))
        if "Дата закрытия в 1С" in raw
        else (fact if raw["Статус"] == "Выполнена" else None)
    )
    plan = normalize_date(raw["Плановый срок"])
    if plan is None:
        raise ValueError("Не задан плановый срок вехи")
    if raw["Статус"] not in ("Выполнена", "Назначена"):
        raise ValueError("Не распознан статус вехи")
    return {
        "milestone_code": str(milestone_code),
        "project_code": str(code),
        "queue": queue,
        "stage": stage,
        "name": str(raw["Наименование"]),
        "is_key": normalize_bool(raw["Ключевая"]),
        "status": raw["Статус"],
        "fact_date": fact,
        "closed_1c_date": closed,
        "plan_date": plan,
    }, warnings
