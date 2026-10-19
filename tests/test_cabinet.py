"""M5: область доступа, отсутствие БГБ и обходов через сценарии/выгрузки."""

import json
from hashlib import sha256
from io import BytesIO

import pytest
from openpyxl import load_workbook
from test_panel import panel  # noqa: F401

from sov.services.cabinet import CabinetService
from sov.services.panel import Actor


def employee(panel, tab="T0002"):  # noqa: F811
    return CabinetService(panel.registry, Actor(f"Сотрудник {tab}", "Сотрудник", tab))


def manager(panel):  # noqa: F811
    return CabinetService(
        panel.registry,
        Actor(
            "Тестовый руководитель", "Руководитель", "T0003", (("TP-T02", "1"),), ("T0002", "T0003")
        ),
    )


def test_employee_own_only_and_manager_project_intersect_staff(panel):  # noqa: F811
    own, lead = employee(panel), manager(panel)
    assert {a["tab_no"] for a in own.assignments()} == {"T0002"}
    assert {a["tab_no"] for a in lead.assignments()} == {"T0002", "T0003"}
    assert {a["project_code"] for a in lead.assignments()} == {"TP-T02"}
    no_staff = CabinetService(
        panel.registry, Actor("РП", "Руководитель", "T0003", (("TP-T02", "1"),), ())
    )
    assert not no_staff.assignments()
    other = next(a["id"] for a in panel.registry.rows("assignment") if a["tab_no"] == "T0001")
    for service in (own, lead):
        for action in (
            lambda: service.card(other, "2026-Q3", as_of="2026-09-30"),
            lambda: service.statement(other, "2026-Q3", as_of="2026-09-30"),
            lambda: service.simulate(other, {}, as_of="2026-09-30"),
            lambda: service.view(other),
            lambda: service.export_event("xlsx", other),
        ):
            with pytest.raises(PermissionError):
                action()


def test_public_payload_and_excel_never_contain_bgb_or_other_people(panel):  # noqa: F811
    for service in (employee(panel), manager(panel)):
        identifier = service.assignments()[0]["id"]
        card = service.card(identifier, "2026-Q3", as_of="2026-09-30")
        serialized = json.dumps(card, ensure_ascii=False)
        assert '"bgb"' not in serialized and '"periods"' not in serialized
        assert "Тестов Один" not in serialized
        assert card["totals"]["paid"] is None
        book = load_workbook(BytesIO(service.statement(identifier, "2026-Q3", as_of="2026-09-30")))
        values = str([[cell.value for row in sheet for cell in row] for sheet in book])
        assert "БГБ" not in values and "Тестов Один" not in values
        book.close()


def test_employee_scenario_rejects_admin_fields_and_keeps_hash(panel):  # noqa: F811
    service = employee(panel)
    identifier = service.assignments()[0]["id"]
    original = sha256(panel.registry.path.read_bytes()).hexdigest()
    for changes, historical in (
        ({"periods": {0: {"bgb": 1}}}, False),
        ({"leave_date": "2026-03-31"}, False),
        ({}, True),
    ):
        with pytest.raises(PermissionError):
            service.simulate(identifier, changes, as_of="2026-09-30", historical=historical)
    result = service.simulate(
        identifier, {"npv_checkpoints": {"q2": 4_900_000_000}}, as_of="2026-09-30"
    )
    assert result["scenario"] != result["baseline"]
    assert '"bgb"' not in json.dumps(result)
    assert sha256(panel.registry.path.read_bytes()).hexdigest() == original
    with pytest.raises(PermissionError):
        manager(panel).simulate(identifier, {}, as_of="2026-09-30")


def test_manager_views_and_exports_are_audited(panel):  # noqa: F811
    service = manager(panel)
    identifier = service.assignments()[0]["id"]
    before = len(panel.journal())
    service.view(identifier)
    service.export_event("statement", identifier)
    assert len(panel.journal()) == before + 2
