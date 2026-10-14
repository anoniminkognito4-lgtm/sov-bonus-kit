"""Сценарии панели: права, ручные решения, расчёт и атомарное закрытие."""

import json
from dataclasses import asdict, dataclass
from uuid import uuid4

from sqlalchemy import insert, select

from sov.adapters.registry_case import RegistryAdapter, quarter, quarter_add
from sov.engine.calculate import calculate_case
from sov.engine.params import DEFAULT_PARAMS
from sov.loaders.batch import load_samples
from sov.loaders.service import (
    _audit,
    _write_version,
    apply_preview,
    prepare_upload,
    rollback_upload,
)
from sov.registry.models import TABLES, audit_log, snapshot, upload
from sov.registry.session import json_text, timestamp

ADMIN = "Группа СОВ"


@dataclass(frozen=True)
class Actor:
    name: str
    role: str = ADMIN


@dataclass(frozen=True)
class Calculation:
    quarter: str
    revision: int
    assignment_ids: tuple
    entries: tuple
    problems: tuple
    results: dict

    @property
    def blocking(self):
        return [p for p in self.problems if p["blocking"]]


def require_admin(actor):
    if actor.role != ADMIN:
        raise PermissionError("Действие доступно только Группе СОВ")


def _domain_insert(connection, table_name, data, time, actor, basis):
    table = TABLES[table_name]
    after = {
        **data,
        "row_key": data["id"],
        "valid_from": time,
        "valid_to": None,
        "created_at": time,
        "source_file_id": None,
    }
    connection.execute(insert(table).values(**after))
    _write_version(connection, table_name, data["id"], after, time, None)
    _audit(connection, actor.name, time, table_name, data["id"], None, after, basis)


class PanelService:
    def __init__(self, registry, actor):
        self.registry, self.actor = registry, actor

    def preview(self, path):
        require_admin(self.actor)
        return prepare_upload(self.registry, path, self.actor.name)

    def apply(self, preview):
        require_admin(self.actor)
        if preview.author != self.actor.name:
            raise PermissionError("Применять предпросмотр должен его автор")
        return apply_preview(self.registry, preview)

    def rollback(self, identifier):
        require_admin(self.actor)
        return rollback_upload(self.registry, identifier, self.actor.name)

    def demo(self, folder):
        require_admin(self.actor)
        return load_samples(self.registry, folder, self.actor.name)

    def uploads(self):
        require_admin(self.actor)
        with self.registry.engine.connect() as connection:
            return [
                dict(r)
                for r in connection.execute(
                    select(upload).order_by(upload.c.applied_at.desc())
                ).mappings()
            ]

    def journal(self):
        require_admin(self.actor)
        with self.registry.engine.connect() as connection:
            return [
                dict(r)
                for r in connection.execute(
                    select(audit_log).order_by(audit_log.c.id.desc())
                ).mappings()
            ]

    def view(self, entity, identifier):
        require_admin(self.actor)
        with self.registry.transaction() as connection:
            _audit(
                connection,
                self.actor.name,
                timestamp(),
                "view",
                str(identifier),
                None,
                {"entity": entity},
                "Просмотр карточки",
            )

    def decisions(self):
        require_admin(self.actor)
        return self.registry.rows("manual_decision")

    def save_decision(self, kind, assignment_id, payload, basis, file_ref):
        require_admin(self.actor)
        if kind not in (
            "early_stage",
            "start_npv",
            "stage5_expense",
            "outside_session",
            "redistribution",
        ):
            raise ValueError("Неизвестный вид решения")
        if not basis.strip() or not file_ref.strip():
            raise ValueError("Укажите основание и ссылку на подтверждающий файл")
        with self.registry.transaction() as connection:
            adapter = RegistryAdapter(self.registry, connection)
            assignment = adapter.one("assignment", id=assignment_id)
            case = adapter.build_case(assignment_id)
            payload = dict(payload)
            payload["assignment_id"] = assignment_id
            if kind == "start_npv":
                payload["value"] = float(payload["value"])
            if kind == "early_stage" and payload.get("approved") is not True:
                raise ValueError("Нужно явное подтверждение решения по опережению")
            if kind == "outside_session":
                from datetime import date

                date.fromisoformat(payload["pay_date"])
                if payload["milestone"] not in [m["name"] for m in case["milestones"]]:
                    raise ValueError("Веха не принадлежит назначению")
                source = next(m for m in case["milestones"] if m["name"] == payload["milestone"])
                if not source.get("closed_1c"):
                    raise ValueError("Выплата вне сессии требует закрытия вехи в 1С")
                if payload["pay_date"] < source["closed_1c"]:
                    raise ValueError("Дата выплаты не может быть раньше закрытия вехи")
            if kind == "stage5_expense":
                if case["stage"] != 5 or payload["plan_budget"] < 0 or payload["fact_budget"] <= 0:
                    raise ValueError("Нужен пятый этап и корректный бюджет")
            time, identifier = timestamp(), str(uuid4())
            _domain_insert(
                connection,
                "manual_decision",
                {
                    "id": identifier,
                    "kind": kind,
                    "tab_no": assignment["tab_no"],
                    "payload_json": json_text(payload),
                    "basis": basis.strip(),
                    "file_ref": file_ref.strip(),
                    "author": self.actor.name,
                },
                time,
                self.actor,
                basis,
            )
            self.registry.bump_revision(connection)
            return identifier

    def _case_with_decisions(self, adapter, identifier):
        case = adapter.build_case(identifier)
        target = adapter.one("assignment", id=identifier)
        decisions = []
        for row in adapter.state["manual_decision"]:
            payload = json.loads(row["payload_json"])
            scope_match = payload.get("assignment_id") == identifier
            if row["kind"] == "stage5_expense" and not scope_match:
                source = adapter.one("assignment", id=payload["assignment_id"])
                scope_match = all(
                    source[k] == target[k] for k in ("project_code", "queue", "stage")
                )
            if scope_match:
                decisions.append((row, payload))
        decisions.sort(key=lambda value: value[0]["created_at"])
        for row, payload in decisions:
            if row["kind"] == "start_npv":
                case["employees"][0]["npv_start"] = payload["value"]
            if row["kind"] == "stage5_expense":
                case["stage5_expense"] = {
                    "remaining": True,
                    "plan_budget": payload["plan_budget"],
                    "fact_budget": payload["fact_budget"],
                }
            if row["kind"] == "outside_session":
                case.setdefault("manual_pay_dates", {})[payload["milestone"]] = payload["pay_date"]
            if row["kind"] == "early_stage":
                case["early_stage_approved"] = True
        return case, decisions

    def calculate(self, selected_quarter, assignment_ids=None, connection=None, previous=None):
        require_admin(self.actor)
        if connection is None:
            with self.registry.transaction() as current:
                return self.calculate(selected_quarter, assignment_ids, current, previous)
        # Проверка формата без зависимости от текущей даты компьютера.
        if quarter_add(selected_quarter, 0) != selected_quarter:
            raise ValueError("Неверный квартал")
        adapter = RegistryAdapter(self.registry, connection)
        all_ids = sorted(r["id"] for r in adapter.state["assignment"])
        identifiers = sorted(all_ids if assignment_ids is None else assignment_ids)
        if not identifiers or not set(identifiers) <= set(all_ids):
            raise ValueError("Нет назначений для расчёта")
        entries, problems, results = [], [], {}
        for identifier in identifiers:
            case, decisions = self._case_with_decisions(adapter, identifier)
            result = calculate_case(case)
            employee = result["employees"][0]
            for row, payload in decisions:
                if row["kind"] == "redistribution":
                    result["problems"].append(
                        {
                            "code": "MANUAL_CALC",
                            "tab_no": employee["tab_no"],
                            "blocking": True,
                            "message": "Перераспределение требует расчёта; автоматизация — M6",
                        }
                    )
            source = adapter.one("assignment", id=identifier)
            hr = adapter.one("employee", tab_no=source["tab_no"])
            project = adapter.one(
                "project", project_code=source["project_code"], queue=source["queue"]
            )
            source_ms = {m["name"]: m for m in case["milestones"]}
            relevant = any(
                m.get("closed_1c") and quarter(m["closed_1c"]) == selected_quarter
                for m in [*case["milestones"], case["final"]]
            )
            relevant |= any(
                quarter(p["event_date"]) == selected_quarter for p in employee["payouts"]
            )
            relevant |= not case["final"].get("plan") and bool(result["problems"])
            if relevant:
                problems.extend(
                    {**p, "assignment_id": identifier, "project_code": source["project_code"]}
                    for p in result["problems"]
                )
            for item in employee["milestones"]:
                event = source_ms[item["name"]].get("closed_1c")
                if event and quarter(event) == selected_quarter and item["amount"] is not None:
                    entries.append(
                        self._entry(
                            adapter,
                            source,
                            hr,
                            project,
                            identifier,
                            item,
                            "аванс за веху",
                            item["name"],
                            "",
                            selected_quarter,
                            event,
                            item["amount"],
                        )
                    )
            for part in employee["payouts"]:
                if quarter(part["event_date"]) != selected_quarter:
                    continue
                label = (
                    employee["final"]["kind"] if part["part"] == "100%" else f"финал {part['part']}"
                )
                for kind, amount in (
                    (label, part["bonus"]),
                    ("проценты на отложенную часть", part["interest"]),
                ):
                    if amount is not None:
                        entries.append(
                            self._entry(
                                adapter,
                                source,
                                hr,
                                project,
                                identifier,
                                part,
                                kind,
                                case["final"]["name"],
                                part["part"],
                                selected_quarter,
                                part["event_date"],
                                amount if part["calc_status"] != "не выплачивается" else 0,
                            )
                        )
            results[identifier] = {"case": case, "result": result}
        for entry in entries:
            if entry["calc_status"] == "прогноз":
                problems.append(
                    {
                        "code": "FM_NOT_APPROVED",
                        "blocking": True,
                        "tab_no": entry["tab_no"],
                        "assignment_id": entry["assignment_id"],
                        "project_code": entry["project_code"],
                        "message": "ФМ квартала не утверждена ФЮБ",
                    }
                )
        unique = {json_text(p): p for p in problems}
        if previous and previous.quarter == selected_quarter:
            old_totals, new_totals = {}, {}
            for totals, rows in ((old_totals, previous.entries), (new_totals, entries)):
                for row in rows:
                    if row["calc_status"] in ("рассчитано", "прогноз"):
                        key = row["assignment_id"]
                        totals[key] = totals.get(key, 0) + row["amount"]
            for identifier, amount in new_totals.items():
                old = old_totals.get(identifier, 0)
                if old and abs(amount / old - 1) > DEFAULT_PARAMS.amount_change_threshold:
                    source = adapter.one("assignment", id=identifier)
                    problem = {
                        "code": "AMOUNT_CHANGE_10PCT",
                        "blocking": False,
                        "tab_no": source["tab_no"],
                        "assignment_id": identifier,
                        "project_code": source["project_code"],
                        "message": (
                            "Сумма изменилась более чем на 10% с прошлого расчёта этого квартала"
                        ),
                    }
                    unique[json_text(problem)] = problem
        return Calculation(
            selected_quarter,
            self.registry.revision(connection),
            tuple(identifiers),
            tuple(entries),
            tuple(unique.values()),
            results,
        )

    def _entry(
        self,
        adapter,
        source,
        hr,
        project,
        identifier,
        item,
        kind,
        milestone_name,
        part,
        selected_quarter,
        event,
        amount,
    ):
        status = item["calc_status"]
        if source["category"].startswith("КУПК") and status == "рассчитано":
            fm = adapter.rows("npv", project_code=source["project_code"], quarter=quarter(event))
            if not fm or not fm[0]["fub_approved_date"]:
                status = "прогноз"
        ms = adapter.rows(
            "milestone",
            project_code=source["project_code"],
            queue=source["queue"],
            stage=source["stage"],
            name=milestone_name,
        )
        return {
            "tab_no": source["tab_no"],
            "fio": hr["fio"],
            "department": hr["department"],
            "project_code": source["project_code"],
            "project": project["name"],
            "queue": source["queue"],
            "stage": source["stage"],
            "assignment_id": identifier,
            "kind": kind,
            "milestone": milestone_name,
            "milestone_code": ms[0]["milestone_code"] if ms else None,
            "part": part,
            "quarter": selected_quarter,
            "pay_date": item["pay_date"],
            "amount": amount,
            "calc_status": status,
            "edition": DEFAULT_PARAMS.edition,
        }

    def close(self, calculation):
        require_admin(self.actor)
        with self.registry.transaction() as connection:
            if calculation.revision != self.registry.revision(connection):
                raise ValueError("Данные изменились: пересчитайте перед закрытием")
            adapter = RegistryAdapter(self.registry, connection)
            if set(calculation.assignment_ids) != {r["id"] for r in adapter.state["assignment"]}:
                raise ValueError("Перед закрытием рассчитайте все назначения")
            if connection.scalar(
                select(snapshot.c.id).where(snapshot.c.quarter == calculation.quarter)
            ):
                raise ValueError("Квартал уже закрыт")
            current = self.calculate(calculation.quarter, connection=connection)
            if current.blocking:
                raise ValueError("Закрытие запрещено: есть блокирующие проблемы")
            if not current.entries:
                raise ValueError("Нет начислений за выбранный квартал")
            identifier, time = str(uuid4()), timestamp()
            rows = []
            for entry in current.entries:
                data = {
                    k: entry[k]
                    for k in (
                        "tab_no",
                        "assignment_id",
                        "kind",
                        "milestone_code",
                        "part",
                        "quarter",
                        "pay_date",
                        "amount",
                        "calc_status",
                        "edition",
                    )
                }
                data.update(id=str(uuid4()), snapshot_id=identifier)
                _domain_insert(connection, "accrual", data, time, self.actor, "Закрытие квартала")
                rows.append({**entry, "id": data["id"], "snapshot_id": identifier})
            payload = {
                "data": adapter.state,
                "params": asdict(DEFAULT_PARAMS),
                "entries": rows,
                "results": current.results,
                "problems": current.problems,
            }
            connection.execute(
                insert(snapshot).values(
                    id=identifier,
                    quarter=current.quarter,
                    closed_at=time,
                    closed_by=self.actor.name,
                    edition=DEFAULT_PARAMS.edition,
                    data_json=json_text(payload),
                )
            )
            _audit(
                connection,
                self.actor.name,
                time,
                "snapshot",
                identifier,
                None,
                {"quarter": current.quarter, "entries": len(rows)},
                "Закрытие квартала",
            )
            self.registry.bump_revision(connection)
            return identifier

    def closed(self, selected_quarter):
        require_admin(self.actor)
        with self.registry.engine.connect() as connection:
            row = (
                connection.execute(select(snapshot).where(snapshot.c.quarter == selected_quarter))
                .mappings()
                .first()
            )
            return {**dict(row), "payload": json.loads(row["data_json"])} if row else None

    def export_event(self, kind, identifier):
        require_admin(self.actor)
        with self.registry.transaction() as connection:
            _audit(
                connection,
                self.actor.name,
                timestamp(),
                "export",
                identifier,
                None,
                {"kind": kind},
                "Подготовка выгрузки",
            )
