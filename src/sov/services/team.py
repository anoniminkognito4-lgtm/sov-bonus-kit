"""Справочник должностей, признак ЛЦ/СКБ и ручная правка команды (D-098–D-100).

Функции собирают записи для `apply_records`: журнал и откат — как у загрузки файла.
"""

from datetime import date

from sov.adapters.load import department_block
from sov.adapters.positions import PositionBook, clean, resolve
from sov.loaders.mapping import assignment_id
from sov.services.new_calc import start_npv_records

CATEGORIES = ("КУПК (РП/ЗРП)", "КУПК", "РУПК", "МП")
SOV_TYPES = ("Вехи", "Вехи от NPV", "Вехи от KPI", "вр. KPI (Вехи)")
END_REASONS = ("уход", "перевод")


def book(rows):
    projects = [p["name"] for p in rows("project")] + [p["project_code"] for p in rows("project")]
    return PositionBook(rows("position_ref"), rows("position_fix"), projects)


def position_questions(rows, params):
    """Названия должностей блоков с таблицей загрузки, которые справочник не распознал."""
    positions = book(rows)
    if not positions:
        return []
    employees = {e["tab_no"]: e for e in rows("employee")}
    result = {}
    for a in rows("assignment"):
        employee = employees.get(a["tab_no"])
        if not employee:
            continue
        block = department_block(employee["department"], params)
        raw = employee["position"] or a["position"]
        if not block or positions.recognize(raw, block)["ref"]:
            continue
        item = result.setdefault(
            (block, clean(raw)), {"block": block, "position": clean(raw), "staff": set()}
        )
        item["staff"].add(a["tab_no"])
    return [
        {**item, "staff": ", ".join(sorted(item["staff"]))} for _, item in sorted(result.items())
    ]


def directory_records(edited):
    records = []
    for row in edited:
        block, position = clean(row.get("block")), clean(row.get("position"))
        if not block and not position:
            continue
        if not block or not position:
            raise ValueError("У строки справочника нужны подразделение и должность")
        category = row.get("category") or None
        if category and category not in CATEGORIES:
            raise ValueError(f"Неизвестная категория: {category}")
        records.append(
            (
                "position_ref",
                {
                    "block": block,
                    "position": position,
                    "category": category,
                    "sov_type": row.get("sov_type") or None,
                    "load_row": row.get("load_row") or None,
                    "comment": row.get("comment") or None,
                },
            )
        )
    return records


def fix_records(edited):
    records = []
    for row in edited:
        wrong, right = clean(row.get("wrong")), clean(row.get("right"))
        if not wrong and not right:
            continue
        if not wrong or not right:
            raise ValueError("У исправления нужны «как написано» и «как правильно»")
        records.append(("position_fix", {"wrong": wrong, "right": right}))
    return records


def lc_skb_state(rows, project, queue, on_date):
    """(флажок, строка решения) на дату по журналу project_flag."""
    state, chosen = False, None
    flags = [r for r in rows("project_flag") if (r["project_code"], r["queue"]) == (project, queue)]
    for row in sorted(flags, key=lambda r: r["date_from"]):
        if row["date_from"] <= on_date:
            state, chosen = bool(row["is_lc_skb"]), row
    return state, chosen


def lc_skb_record(project, queue, flag, date_from, basis):
    if not basis.strip():
        raise ValueError("Укажите основание")
    date.fromisoformat(date_from)
    return (
        "project_flag",
        {
            "project_code": project,
            "queue": queue,
            "date_from": date_from,
            "is_lc_skb": int(bool(flag)),
            "basis": basis.strip(),
        },
    )


def defaults(rows, params, department, position):
    """Категория и вид СОВ по справочнику для формы «Добавить в команду»."""
    block = department_block(department, params)
    found = book(rows).recognize(position, block) if block else {"ref": None}
    ref = found["ref"]
    return {
        "block": block,
        "recognized": ref is not None,
        "position": ref["position"] if ref else clean(position),
        "category": ref["category"] if ref else None,
        "sov_type": ref["sov_type"] if ref else None,
    }


def add_records(rows, params, form):
    """Записи «Добавить в команду»: сотрудник (если новый), назначение, загрузка."""
    project, queue, stage = form["project_code"], form["queue"], int(form["stage"])
    if not any(
        s["project_code"] == project and s["queue"] == queue and s["stage"] == stage
        for s in rows("stage")
    ):
        raise ValueError(f"Этапа {stage} у {project}/{queue} нет в реестре")
    if not (form.get("basis") or "").strip():
        raise ValueError("Укажите основание")
    date_from = form["date_from"]
    date.fromisoformat(date_from)
    records = []
    employee = next((e for e in rows("employee") if e["tab_no"] == form["tab_no"]), None)
    if employee is None:
        new = form.get("new_employee") or {}
        if not (form["tab_no"] and new.get("fio")):
            raise ValueError("Новый сотрудник: нужны табельный номер и ФИО")
        employee = {
            "tab_no": form["tab_no"],
            "fio": clean(new["fio"]),
            "position": clean(new.get("position")) or None,
            "department": clean(new.get("department")) or None,
            "hire_date": new.get("hire_date") or None,
            "dismissal_date": None,
            "probation_end": None,
            "probation_result": None,
        }
        records.append(("employee", employee))
    identity = assignment_id(employee["tab_no"], project, queue, stage)
    if any(a["id"] == identity for a in rows("assignment")):
        raise ValueError("Сотрудник уже есть в команде этого этапа")
    base = {
        "category": form.get("category") or None,
        "sov_type": form.get("sov_type") or None,
    }
    position = form.get("position") or employee["position"]
    hint = defaults(rows, params, employee["department"], position)
    filled = resolve(base, position, None)
    filled["category"] = filled["category"] or hint["category"]
    filled["sov_type"] = filled["sov_type"] or hint["sov_type"]
    if filled["sov_type"] == "вр. KPI (Вехи)":
        filled["category"] = "МП"
    if not filled["category"] or not filled["sov_type"]:
        raise ValueError("Категория и вид СОВ не определены: укажите их в форме")
    if filled["category"] not in CATEGORIES:
        raise ValueError(f"Неизвестная категория: {filled['category']}")
    records.append(
        (
            "assignment",
            {
                "id": identity,
                "tab_no": employee["tab_no"],
                "project_code": project,
                "queue": queue,
                "stage": stage,
                "sov_type": filled["sov_type"],
                "category": filled["category"],
                "role": form.get("role") or "другая",
                "position": hint["position"] or None,
                "date_from": date_from,
                "date_to": None,
                "end_reason": None,
                "from_prev_stage": 0,
            },
        )
    )
    if filled["category"].startswith("КУПК"):
        npv = {(r["project_code"], r["quarter"]): r for r in rows("npv")}
        records.extend(start_npv_records(npv, identity, project, stage, date_from))
    if form.get("load") not in (None, ""):
        load = float(form["load"])
        if not 0 < load <= 1:
            raise ValueError("Загрузка — от 1 до 100%")
        records.append(
            (
                "assignment_load",
                {"assignment_id": identity, "date_from": date_from, "date_to": None, "load": load},
            )
        )
    return records, identity


def remove_record(rows, identifier, date_to, end_reason):
    """«Убрать с проекта»: назначение закрывается датой и причиной, не удаляется."""
    assignment = next((a for a in rows("assignment") if a["id"] == identifier), None)
    if assignment is None:
        raise ValueError("Назначение не найдено")
    if end_reason not in END_REASONS:
        raise ValueError(f"Причина — одна из: {', '.join(END_REASONS)}")
    date.fromisoformat(date_to)
    if date_to < assignment["date_from"]:
        raise ValueError("Дата ухода раньше даты прихода на проект")
    if assignment["end_reason"]:
        raise ValueError("Сотрудник уже убран с этого этапа")
    fields = {k: v for k, v in assignment.items() if k not in _META}
    return ("assignment", {**fields, "date_to": date_to, "end_reason": end_reason})


_META = {"row_key", "valid_from", "valid_to", "source_file_id", "created_at"}
