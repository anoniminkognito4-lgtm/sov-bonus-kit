"""Реестр одним взглядом: кто на каком проекте, загрузка, сроки и просрочки.

Только чтение реестра; дата среза — параметр, без даты компьютера.
"""

from datetime import date, timedelta

from sov.engine.params import DEFAULT_PARAMS, SovParams
from sov.engine.rules.dates import milestone_part


def _latest_plans(rows):
    latest = {}
    for plan in rows("milestone_plan_version"):
        code = plan["milestone_code"]
        if code not in latest or plan["version"] > latest[code]["version"]:
            latest[code] = plan
    return {code: plan["plan_date"] for code, plan in latest.items()}


def _load_on(loads, as_of):
    current = [
        r for r in loads if r["date_from"] <= as_of and (not r["date_to"] or r["date_to"] >= as_of)
    ]
    chosen = max(current or loads, key=lambda r: r["date_from"], default=None)
    return chosen["load"] if chosen else None


def _days(later, earlier):
    return (date.fromisoformat(later) - date.fromisoformat(earlier)).days


class RegistryView:
    """Срез реестра на дату `as_of` (ISO)."""

    def __init__(self, rows, params: SovParams = DEFAULT_PARAMS):
        self.rows = rows
        self.params = params
        self.plans = _latest_plans(rows)
        self.employees = {e["tab_no"]: e for e in rows("employee")}
        self.projects = {(p["project_code"], p["queue"]): p for p in rows("project")}
        self.stages = {(s["project_code"], s["queue"], s["stage"]): s for s in rows("stage")}
        self.milestones = {}
        for m in rows("milestone"):
            key = (m["project_code"], m["queue"], m["stage"])
            self.milestones.setdefault(key, []).append(m)
        self.individual = {
            (r["tab_no"], r["milestone_code"]): r["individual_plan"]
            for r in rows("individual_date")
        }
        self.loads = {}
        for r in rows("assignment_load"):
            self.loads.setdefault(r["assignment_id"], []).append(r)

    def milestones_for(self, assignment, as_of):
        """Вехи этапа глазами сотрудника: план, инд. срок, факт, участие, отклонение."""
        key = (assignment["project_code"], assignment["queue"], assignment["stage"])
        stage = self.stages.get(key) or {}
        continued = bool(assignment["from_prev_stage"]) and assignment["date_from"] == stage.get(
            "start_date"
        )
        cutoff = (
            date.fromisoformat(assignment["date_from"]) + timedelta(days=self.params.newcomer_days)
        ).isoformat()
        result = []
        stage_milestones = self.milestones.get(key, [])
        key_plan = next(
            (
                self.individual.get((assignment["tab_no"], m["milestone_code"]))
                or self.plans.get(m["milestone_code"])
                for m in stage_milestones
                if m["is_key"]
            ),
            None,
        )
        for m in stage_milestones:
            plan = self.plans.get(m["milestone_code"])
            individual = self.individual.get((assignment["tab_no"], m["milestone_code"]))
            plan_eff = individual or plan
            if milestone_part(m["name"]) == 2 and assignment["stage"] == 4 and key_plan:
                plan_eff = key_plan  # сл. 55 п. 19: вторая часть — к сроку РнВ
            unknown = bool(m["status"]) and m["status"] not in self.params.milestone_statuses
            fact = m["closed_1c_date"] or m["fact_date"]
            if fact and fact > as_of:
                fact = None  # на дату среза веха ещё не закрыта
            key_milestone = bool(m["is_key"])
            participates = key_milestone or continued or bool(plan_eff and plan_eff >= cutoff)
            if fact and plan_eff:
                deviation = _days(plan_eff, fact)
            elif plan_eff and plan_eff < as_of:
                deviation = -_days(as_of, plan_eff)
            else:
                deviation = None
            if unknown:
                state = "статус 1С неизвестен"
            elif not participates:
                state = "не участвует"
            elif fact:
                state = (
                    "закрыта с просрочкой"
                    if deviation is not None and deviation < 0
                    else ("закрыта")
                )
            elif deviation is not None:
                state = "просрочена"
            else:
                state = "в работе"
            result.append(
                {
                    "milestone_code": m["milestone_code"],
                    "name": m["name"],
                    "is_key": key_milestone,
                    "plan": plan,
                    "individual_plan": individual,
                    "fact": fact,
                    "deviation_days": deviation,
                    "state": state,
                }
            )
        return sorted(result, key=lambda r: (r["is_key"], r["individual_plan"] or r["plan"] or ""))

    def overview(self, as_of):
        """Одна строка на назначение: сотрудник × проект (очередь) × этап."""
        rows = []
        for a in self.rows("assignment"):
            person = self.employees.get(a["tab_no"], {})
            project = self.projects.get((a["project_code"], a["queue"]), {})
            milestones = self.milestones_for(a, as_of)
            participating = [
                m for m in milestones if m["state"] not in ("не участвует", "статус 1С неизвестен")
            ]
            overdue = [m for m in participating if m["state"] == "просрочена"]
            upcoming = [m for m in participating if m["state"] == "в работе"]
            key = next((m for m in milestones if m["is_key"]), None)
            if a["date_from"] > as_of:
                status = "ещё не начал"
            elif a["date_to"] and a["date_to"] < as_of:
                # Без причины ухода назначение просто закончилось вместе с этапом.
                status = "ушёл" if a["end_reason"] else "этап завершён"
            else:
                status = "работает"
            rows.append(
                {
                    "assignment_id": a["id"],
                    "tab_no": a["tab_no"],
                    "fio": person.get("fio"),
                    "department": person.get("department"),
                    "project": project.get("name") or a["project_code"],
                    "project_code": a["project_code"],
                    "queue": a["queue"],
                    "stage": a["stage"],
                    "category": a["category"],
                    "role": a["role"],
                    "status": status,
                    "date_from": a["date_from"],
                    "date_to": a["date_to"],
                    "leave_date": a["date_to"] if a["end_reason"] else None,
                    "end_reason": a["end_reason"],
                    "load": _load_on(self.loads.get(a["id"], []), as_of),
                    "stage_end": key and (key["individual_plan"] or key["plan"]),
                    "individual_count": sum(1 for m in milestones if m["individual_plan"]),
                    "closed": sum(1 for m in participating if m["fact"]),
                    "total": len(participating),
                    "overdue_count": len(overdue),
                    "max_overdue_days": max((-m["deviation_days"] for m in overdue), default=0),
                    "unknown_status": sum(
                        1 for m in milestones if m["state"] == "статус 1С неизвестен"
                    ),
                    "late_closed": sum(
                        1 for m in participating if m["state"] == "закрыта с просрочкой"
                    ),
                    "next_milestone": upcoming[0]["name"] if upcoming else None,
                    "next_date": (upcoming[0]["individual_plan"] or upcoming[0]["plan"])
                    if upcoming
                    else None,
                }
            )
        return sorted(rows, key=lambda r: (r["fio"] or "", r["project_code"], r["stage"]))

    def stage_options(self):
        """Проекты-очереди-этапы, по которым есть назначения, для выбора калькулятора."""
        options = {(a["project_code"], a["queue"], a["stage"]) for a in self.rows("assignment")}
        return sorted(options)

    def stage_label(self, option):
        code, queue, stage = option
        name = self.projects.get((code, queue), {}).get("name") or code
        return f"{name} · очередь {queue} · этап {stage}"

    def assignments_for(self, options, as_of=None, active_only=False):
        chosen = set(options)
        result = []
        for a in self.rows("assignment"):
            if (a["project_code"], a["queue"], a["stage"]) not in chosen:
                continue
            if (
                active_only
                and as_of
                and (a["date_from"] > as_of or (a["date_to"] and a["date_to"] < as_of))
            ):
                continue
            result.append(a["id"])
        return sorted(result)
