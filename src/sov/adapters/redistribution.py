"""Подготовка полного фонда ФП из реестра для чистой проверки перераспределения."""

import json

from sov.engine.calculate import calculate_case


def build_context(adapter, identifier, overrides=()):
    source = adapter.one("assignment", id=identifier)
    scope = {key: source[key] for key in ("project_code", "queue", "stage")}
    assignments = adapter.rows("assignment", **scope)
    ids = {a["id"] for a in assignments}
    milestones = {m["milestone_code"]: m for m in adapter.rows("milestone", **scope)}
    changes = {}
    for row in adapter.state["redistribution"]:
        milestone = milestones.get(row["milestone_code"])
        if not milestone:
            continue
        targets = [
            a
            for a in assignments
            if a["tab_no"] == row["tab_no"]
            and (
                not a["end_reason"]
                or not milestone["closed_1c_date"]
                or a["date_to"] >= milestone["closed_1c_date"]
            )
            and (not milestone["closed_1c_date"] or a["date_from"] <= milestone["closed_1c_date"])
        ]
        if len(targets) != 1:
            raise ValueError("Перераспределение: назначение сотрудника неоднозначно")
        leaders = [
            e for e in adapter.state["employee"] if row["decided_by"] in (e["tab_no"], e["fio"])
        ]
        leader = leaders[0]["tab_no"] if len(leaders) == 1 else None
        key = (targets[0]["id"], milestone["name"])
        changes[key] = {**row, "assignment_id": key[0], "milestone": key[1], "decided_by": leader}
    for decision in sorted(adapter.state["manual_decision"], key=lambda r: r["created_at"]):
        if decision["kind"] == "redistribution_batch":
            for row in json.loads(decision["payload_json"])["rows"]:
                if row["assignment_id"] in ids:
                    changes[(row["assignment_id"], row["milestone"])] = row
    for row in overrides:
        if row["assignment_id"] not in ids:
            raise ValueError(
                "Все участники решения должны принадлежать одному проекту/очереди/этапу"
            )
        changes[(row["assignment_id"], row["milestone"])] = row
    if not changes:
        return None
    members = []
    for assignment in assignments:
        case = adapter.build_case(assignment["id"], include_redistribution=False)
        employee = adapter.one("employee", tab_no=assignment["tab_no"])
        members.append(
            {
                "id": assignment["id"],
                **scope,
                "tab_no": assignment["tab_no"],
                "fp": employee["department"],
                "category": assignment["category"],
                "employee": case["employees"][0],
                "result": calculate_case(case, adapter.params)["employees"][0],
            }
        )
    return {"members": members, "rows": list(changes.values())}
