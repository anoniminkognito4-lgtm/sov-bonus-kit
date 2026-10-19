"""Реестр → вход чистого движка. Исходные кейсы и эталоны здесь не читаются."""

import json
from datetime import date, timedelta

from sqlalchemy import select

from sov.engine.calculate import calculate_case
from sov.engine.params import DEFAULT_PARAMS
from sov.engine.rules.kpi import milestone_kpis, quarter, quarter_add  # noqa: F401
from sov.registry.models import snapshot
from sov.registry.session import json_text


class RegistryAdapter:
    def __init__(self, registry, connection=None, params=DEFAULT_PARAMS):
        self.params = params
        if connection is not None:
            self.state = registry.domain_state(connection)
            self.closed = [json.loads(v) for v in connection.scalars(select(snapshot.c.data_json))]
        else:
            with registry.engine.connect() as current:
                self.state = registry.domain_state(current)
                self.closed = [json.loads(v) for v in current.scalars(select(snapshot.c.data_json))]

    def rows(self, table, **fields):
        return [r for r in self.state[table] if all(r.get(k) == v for k, v in fields.items())]

    def one(self, table, **fields):
        rows = self.rows(table, **fields)
        if len(rows) != 1:
            raise ValueError(f"Нужна одна запись {table}: {fields}, найдено {len(rows)}")
        return rows[0]

    def npv(self, code, period):
        rows = self.rows("npv", project_code=code, quarter=period)
        if not rows:
            return None
        if len(rows) > 1:
            raise ValueError(f"Несколько версий ФМ {code}, {period}: требуется выбор версии")
        # СОВ, слайд 23: текущий NPV с прогнозными ПАУР.
        return rows[0]["npv_forecast_paur"]

    def bs_share(self, code, period):
        rows = self.rows("bs_share", project_code=code, quarter=period)
        return rows[0]["share_transferred"] if rows else None

    def periods(self, assignment, employee, end, extend=False):
        loads = sorted(
            self.rows("assignment_load", assignment_id=assignment["id"]),
            key=lambda r: r["date_from"],
        )
        bgbs = sorted(
            self.rows("bgb_period", tab_no=employee["tab_no"]), key=lambda r: r["date_from"]
        )
        leave = assignment["date_to"] if assignment["end_reason"] else None
        if extend and not leave and bgbs:
            bgbs = [dict(r) for r in bgbs]
            bgbs[-1]["date_to"] = max(end, bgbs[-1]["date_to"] or end)
        if leave:
            bgbs = [dict(r) for r in bgbs if r["date_from"] <= leave]
            if bgbs and (bgbs[-1]["date_to"] is None or bgbs[-1]["date_to"] >= leave):
                bgbs[-1]["date_to"] = end
        result = []
        for index, load in enumerate(loads):
            load_end = load["date_to"] or end
            if extend and index == len(loads) - 1 and not leave:
                load_end = max(load_end, end)
            if index == len(loads) - 1 and leave and load_end >= leave:
                load_end = end  # ПБ плановый: последняя загрузка продолжается после ухода.
            for bgb in bgbs:
                begin = max(assignment["date_from"], load["date_from"], bgb["date_from"])
                finish = min(end, load_end, bgb["date_to"] or end)
                if begin <= finish:
                    result.append(
                        {"from": begin, "to": finish, "bgb": bgb["bgb"], "load": load["load"]}
                    )
        return sorted(result, key=lambda r: r["from"])

    def build_case(self, identifier, include_redistribution=True):
        assignment = self.one("assignment", id=identifier)
        code, queue, stage_number = (assignment[k] for k in ("project_code", "queue", "stage"))
        project = self.one("project", project_code=code, queue=queue)
        stage = self.one("stage", project_code=code, queue=queue, stage=stage_number)
        employee = self.one("employee", tab_no=assignment["tab_no"])
        source_ms = self.rows("milestone", project_code=code, queue=queue, stage=stage_number)
        milestones, final, individual = [], None, {}
        extend = False
        excluded = any(
            v in (assignment["position"] or "").lower()
            for v in ("вице-президент", "директор", "начальник отдела")
        )
        for source in source_ms:
            plans = self.rows("milestone_plan_version", milestone_code=source["milestone_code"])
            chosen = min if excluded else max
            plan = chosen(plans, key=lambda r: r["version"])["plan_date"] if plans else None
            if len(plans) > 1 and plan > min(r["plan_date"] for r in plans):
                extend = True
            milestone = {
                "name": source["name"],
                "building": queue,
                "plan": plan,
                "fact": source["fact_date"],
                "closed_1c": source["closed_1c_date"],
            }
            if source["is_key"]:
                if final is not None:
                    raise ValueError("У этапа несколько ключевых вех")
                final = milestone
            else:
                milestones.append(milestone)
            for individual_row in self.rows(
                "individual_date",
                tab_no=employee["tab_no"],
                milestone_code=source["milestone_code"],
            ):
                individual["final" if source["is_key"] else source["name"]] = individual_row[
                    "individual_plan"
                ]
        milestones.sort(key=lambda r: (r["plan"] or "", r["name"]))
        if final is None:
            final = {
                "name": stage["key_milestone_name"],
                "plan": None,
                "fact": None,
                "closed_1c": None,
            }
        end = max(
            [m["plan"] for m in [*milestones, final] if m["plan"]]
            + list(individual.values())
            + [assignment["date_from"]]
        )
        final_date = final["closed_1c"] or final["fact"] or final["plan"] or end
        final_quarter = quarter(final_date)
        leave = assignment["date_to"] if assignment["end_reason"] else None
        npvs = {
            m["name"]: self.npv(code, quarter(m["closed_1c"] or m["fact"] or m["plan"]))
            for m in milestones
        }
        current_npv = next((v for v in npvs.values() if v is not None), None)
        starts = self.rows("start_npv", assignment_id=identifier)
        kpi_rows = {r["quarter"]: r["kpi"] for r in self.rows("kpi", tab_no=employee["tab_no"])}
        kpis = milestone_kpis(milestones, kpi_rows)
        checkpoint_base = final["fact"] or final["plan"] or end
        checkpoint_dates = [
            (
                date.fromisoformat(checkpoint_base)
                + timedelta(days=index * self.params.checkpoint_days)
            ).isoformat()
            for index in (1, 2)
        ]
        person = {
            "tab_no": employee["tab_no"],
            "fio": employee["fio"],
            "position": assignment["position"],
            "category": assignment["category"],
            "role": assignment["role"],
            "start": assignment["date_from"],
            "periods": self.periods(assignment, employee, end, extend),
            "leave_date": leave,
            "hr_dismissal_date": employee["dismissal_date"],
            "npv_start": starts[0]["npv_start"] if starts else None,
            "npv_current": current_npv,
            "npv_by_milestone": npvs,
            "npv_checkpoints": {
                "final": self.npv(code, final_quarter),
                "q1": self.npv(code, quarter(checkpoint_dates[0])),
                "q2": self.npv(code, quarter(checkpoint_dates[1])),
            },
            "individual_dates": individual,
            "kpi": kpis,
            "kpi_quarterly": kpi_rows,
            "probation_end": employee["probation_end"],
            "probation_result": employee["probation_result"],
            "legacy_sov": assignment["sov_type"]
            not in ("Вехи", "Вехи от NPV", "Вехи от KPI", "вр. KPI (Вехи)"),
        }
        frozen, frozen_payouts, frozen_interest = {}, {}, {}
        for payload in self.closed:
            value = payload.get("results", {}).get(identifier)
            if not value:
                continue
            for row in payload.get("entries", []):
                if (
                    row["assignment_id"] != identifier
                    or row["kind"] != "аванс за веху"
                    or row["calc_status"] != "рассчитано"
                ):
                    continue
                source = next(
                    (m for m in value["case"]["milestones"] if m["name"] == row["milestone"]), None
                )
                interest = next(
                    (
                        r
                        for r in value["result"]["employees"][0]["interest"]["rows"]
                        if r["milestone"] == row["milestone"]
                    ),
                    {},
                )
                if source:
                    original_item = next(
                        (
                            m
                            for m in value["result"]["employees"][0]["milestones"]
                            if m["name"] == row["milestone"]
                        ),
                        {},
                    )
                    frozen[row["milestone"]] = {
                        "amount": row["amount"],
                        "pay_date": row["pay_date"],
                        "event_date": original_item.get("recognition_date")
                        or source.get("closed_1c")
                        or source["fact"],
                        "deferred": interest.get("deferred"),
                        "base_amount": original_item.get("base_amount", row["amount"]),
                        "redistribution_pct": original_item.get("redistribution_pct", 0),
                    }
            accepted = {
                r["part"]
                for r in payload.get("entries", [])
                if r["assignment_id"] == identifier
                and r["part"]
                and r["calc_status"] == "рассчитано"
            }
            original_result = value["result"]["employees"][0]
            for index, part in enumerate(original_result["payouts"]):
                if part["part"] in accepted:
                    frozen_payouts[part["part"]] = {**part, "slot": index}
                    frozen_interest[part["part"]] = {
                        r["milestone"]: p
                        for r in original_result["interest"]["rows"]
                        for p in r["parts"]
                        if p["part"] == part["part"]
                    }
        if frozen:
            person["frozen_advances"] = frozen
        if frozen_payouts:
            person["frozen_payouts"] = frozen_payouts
            person["frozen_interest"] = frozen_interest
        if leave:
            person.update(
                npv_at_leave=self.npv(code, quarter(leave)),
                npv_stage_end=self.npv(code, final_quarter),
            )
        expenses = self.rows("stage5_expense", project_code=code, queue=queue, stage=stage_number)
        bs_enabled = "БС" in (project["blocks"] or "")
        case = {
            "id": identifier,
            "project": project["name"],
            "project_code": code,
            "queue": queue,
            "stage": stage_number,
            "is_exception": bool(project["is_exception"]),
            "handover_date": project["handover_date"],
            "sale": {
                "date": project["sale_date"],
                "work_end": leave,
                "npv_previous": self.npv(code, quarter_add(quarter(project["sale_date"]), -1)),
            }
            if project["sale_date"]
            else None,
            "new_stage_start": stage["start_date"],
            "continues_from_prev_stage": bool(assignment["from_prev_stage"]),
            "bs": {"enabled": bs_enabled, "share_transferred": self.bs_share(code, final_quarter)},
            "bs_by_milestone": {
                m["name"]: self.bs_share(code, quarter(m["closed_1c"] or m["fact"] or m["plan"]))
                for m in milestones
            },
            "milestones": milestones,
            "final": final,
            "employees": [person],
            "key_rate_history": [
                {"date_from": r["date_from"], "rate": r["rate"]} for r in self.state["key_rate"]
            ],
            "holidays": [r["date"] for r in self.state["holiday"]],
            "stage5_expense": (
                {
                    "remaining": bool(expenses[0]["expense_remaining"]),
                    "plan_budget": expenses[0]["plan_budget"],
                    "fact_budget": expenses[0]["fact_budget"],
                }
                if expenses
                else None
            ),
        }
        if include_redistribution:
            from sov.adapters.redistribution import build_context

            context = build_context(self, identifier)
            if context:
                case["redistribution_context"] = context
        return case

    def calculate_mapped(self, mapping):
        """Собрать результаты по карте образцов; карта содержит только ключи."""
        combined = {}
        for row in mapping:
            assignments = self.rows(
                "assignment", tab_no=row["tab_no"], project_code=row["project_code"]
            )
            if len(assignments) != 1:
                raise ValueError("Карта кейсов неоднозначна: ожидается одно назначение")
            result = calculate_case(self.build_case(assignments[0]["id"]))
            target = combined.setdefault(
                row["case_id"],
                {
                    "schema": "sov-result/1",
                    "id": row["case_id"],
                    "edition": result["edition"],
                    "variant": "spec",
                    "employees": [],
                    "problems": [],
                    "blocking_problems": [],
                },
            )
            target["employees"].extend(result["employees"])
            target["problems"].extend(result["problems"])
            target["blocking_problems"].extend(result["blocking_problems"])
        for result in combined.values():
            for key in ("problems", "blocking_problems"):
                result[key] = list({json_text(v): v for v in result[key]}.values())
        return combined
