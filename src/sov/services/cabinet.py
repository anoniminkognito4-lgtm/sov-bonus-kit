"""Ограниченные представления и выгрузки; проверка области доступа до расчёта."""

from copy import deepcopy

from sov.engine.calculate import calculate_case
from sov.engine.rules.dates import closure_date
from sov.exports.files import statement
from sov.loaders.service import _audit
from sov.registry.session import timestamp
from sov.services.panel import Actor, PanelService
from sov.services.scenario import scenario_case

EMPLOYEE = "Сотрудник"
MANAGER = "Руководитель"


def public_case(case):
    value = deepcopy(case)
    value.pop("redistribution_context", None)
    # Исходные периоды БГБ не передаются клиентам кабинетных сервисов.
    for person in value["employees"]:
        person.pop("periods", None)
        person.pop("hr_dismissal_date", None)
    return value


class CabinetService:
    def __init__(self, registry, actor):
        if actor.role not in (EMPLOYEE, MANAGER) or not actor.person_id:
            raise PermissionError("Укажите учётную запись кабинета")
        self.registry, self.actor = registry, actor
        self._panel = PanelService(registry, Actor(actor.name))

    def assignments(self, connection=None):
        rows = self.registry.rows("assignment", connection)
        if self.actor.role == EMPLOYEE:
            return [r for r in rows if r["person_id"] == self.actor.person_id]
        return [
            r
            for r in rows
            if (r["project_code"], r["queue"]) in self.actor.projects
            and r["person_id"] in self.actor.staff
        ]

    def _assignment(self, identifier, connection=None):
        rows = [r for r in self.assignments(connection) if r["id"] == identifier]
        if not rows:
            raise PermissionError("Назначение недоступно этой учётной записи")
        return rows[0]

    def card(self, identifier, selected_quarter, *, as_of, connection=None):
        if connection is None:
            with self.registry.engine.connect() as current:
                current.exec_driver_sql("BEGIN")
                return self.card(identifier, selected_quarter, as_of=as_of, connection=current)
        source = self._assignment(identifier, connection)
        calculation = self._panel.calculate(selected_quarter, [identifier], connection=connection)
        value = calculation.results[identifier]
        case = value["case"]
        person = case["employees"][0]
        dates = {
            m["name"]: person.get("individual_dates", {}).get(m["name"], m["plan"])
            for m in case["milestones"]
            if not closure_date(m)
        }
        if not closure_date(case["final"]) and case["final"].get("plan"):
            dates["final"] = person.get("individual_dates", {}).get("final", case["final"]["plan"])
        changes = {"dates": dates}
        if person["category"].startswith("КУПК") and person.get("npv_current") is not None:
            changes["npv_current"] = person["npv_current"]
        projected = scenario_case(case, changes)
        # Прогноз контрольных NPV при неизменном текущем NPV — явно указанное условие.
        if person["category"].startswith("КУПК") and person.get("npv_current") is not None:
            for key in ("q1", "q2"):
                projected["employees"][0].setdefault("npv_checkpoints", {}).setdefault(
                    key, person["npv_current"]
                )
        accruals = [
            r for r in self.registry.rows("accrual", connection) if r["assignment_id"] == identifier
        ]
        paid = [r for r in accruals if r["calc_status"] == "выплачено"]
        return {
            "assignment": {
                k: source[k]
                for k in (
                    "id",
                    "person_id",
                    "project_code",
                    "queue",
                    "stage",
                    "category",
                    "date_from",
                )
            },
            "case": public_case(case),
            "result": value["result"],
            "forecast": calculate_case(projected, calculation.params),
            "accruals": accruals,
            "totals": {
                "paid": sum(r["amount"] for r in paid) if paid else None,
                "accrued": sum(
                    r["amount"]
                    for r in accruals
                    if r["calc_status"] in ("рассчитано", "начислено", "выплачено")
                ),
            },
            "as_of": as_of,
        }

    def simulate(self, identifier, changes, *, as_of, historical=False, selected_quarter=None):
        with self.registry.engine.connect() as connection:
            connection.exec_driver_sql("BEGIN")
            return self._simulate(
                identifier, changes, as_of, historical, connection, selected_quarter
            )

    def _simulate(self, identifier, changes, as_of, historical, connection, selected_quarter):
        self._assignment(identifier, connection)
        if self.actor.role != EMPLOYEE:
            raise PermissionError("Моделирование доступно сотруднику по своим назначениям")
        if historical or set(changes) & {"leave_date", "periods"}:
            raise PermissionError("Исторические даты, БГБ, загрузка и уход доступны Группе СОВ")
        result = self._panel.simulate(
            identifier,
            changes,
            as_of=as_of,
            connection=connection,
            selected_quarter=selected_quarter,
        )
        result["case"] = public_case(result["case"])
        return result

    def view(self, identifier):
        assignment = self._assignment(identifier)
        if self.actor.role == MANAGER or assignment["person_id"] != self.actor.person_id:
            self._panel.view("employee", assignment["person_id"])

    def statement(self, identifier, selected_quarter, *, as_of):
        card = self.card(identifier, selected_quarter, as_of=as_of)
        case = card["case"]
        return statement({identifier: card}, case["project_code"], case["stage"])

    def export_event(self, kind, identifier):
        self._assignment(identifier)
        with self.registry.transaction() as connection:
            _audit(
                connection,
                self.actor.name,
                timestamp(),
                "export",
                identifier,
                None,
                {"kind": kind},
                "Выгрузка из кабинета",
            )
