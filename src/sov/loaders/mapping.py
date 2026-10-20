"""Сопоставление исходных строк по ключам; несопоставленное не записывается."""

import hashlib
import json
from calendar import monthrange
from datetime import date, timedelta

KEYS = {
    "sov_param": ("edition", "name"),
    "employee": ("tab_no",),
    "bgb_period": ("tab_no", "date_from"),
    "project": ("project_code", "queue"),
    "stage": ("project_code", "queue", "stage"),
    "milestone": ("milestone_code",),
    "milestone_plan_version": ("milestone_code", "version"),
    "milestone_key": ("natural_key",),
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
    if kind in ("bgb_period", "assignment", "kpi", "rp_project_set"):
        require(state, "employee", tab_no=row["tab_no"])
    if kind in ("stage", "assignment", "stage5_expense", "rp_project_set"):
        require(state, "project", project_code=row["project_code"], queue=row["queue"])
    if kind in ("npv", "bs_share") and not find(state, "project", project_code=row["project_code"]):
        raise Unmatched(f"Не сопоставлен проект {row['project_code']}")
    if kind in ("assignment", "stage5_expense"):
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
        if previous and any(previous[0][k] != row[k] for k in ("category", "role", "sov_type")):
            old = previous[0]
            if old["category"] != "РУПК" or not row["category"].startswith("КУПК"):
                raise ValueError("Для смены категории требуется согласованный перевод РУПК → КУПК")
            if (
                old["end_reason"] != "перевод"
                or not old["date_to"]
                or (date.fromisoformat(old["date_to"]) + timedelta(days=1)).isoformat()
                != row["date_from"]
            ):
                raise ValueError("Сначала закройте прежнее назначение как перевод накануне нового")
            identity += (
                "-"
                + hashlib.sha256(f"{row['date_from']}:{row['category']}".encode()).hexdigest()[:12]
            )
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
                row["date_to"]
                if row.get("end_reason")
                else (
                    max(old["date_to"], data["date_to"])
                    if old["date_to"] and data["date_to"]
                    else None
                )
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
        if len(matches) > 1:
            matches = [r for r in matches if r["category"].startswith("КУПК")]
        if len(matches) != 1:
            raise Unmatched("Стартовый NPV: назначение не найдено или неоднозначно")
        if "-" in matches[0]["id"][4:]:
            begin = date.fromisoformat(matches[0]["date_from"])
            expected = date(
                begin.year, begin.month, monthrange(begin.year, begin.month)[1]
            ).isoformat()
            if row.get("fixed_date") != expected:
                raise ValueError("NPV перевода должен быть зафиксирован на конец месяца перевода")
        row = {k: v for k, v in row.items() if k not in ("tab_no", "project_code", "stage")}
        row.update(assignment_id=matches[0]["id"], corrections=None)
    if kind in ("individual_date", "redistribution"):
        require(state, "employee", tab_no=row["tab_no"])
        milestone = milestone_for(state, row)
        if kind == "redistribution" and any(
            row.get(k) is None for k in ("pct", "decided_by", "approved_date", "basis")
        ):
            raise ValueError("Нужны процент, РФП, дата согласования и основание перераспределения")
        if kind == "redistribution" and milestone["is_key"]:
            raise ValueError(
                "Проценты задаются по промежуточным вехам; "
                "коэффициент финала считается автоматически"
            )
        row = {k: v for k, v in row.items() if k not in ("project_code", "stage", "milestone")}
        row["milestone_code"] = milestone["milestone_code"]
    return [(kind, row)], warnings
