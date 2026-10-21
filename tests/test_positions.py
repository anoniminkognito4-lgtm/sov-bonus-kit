"""Справочник должностей: распознавание названий, категория и вид СОВ (D-098)."""

import pytest
from conftest import ROOT
from sqlalchemy import update

from sov.adapters.positions import PositionBook, clean, resolve
from sov.registry.models import TABLES
from sov.registry.session import Registry
from sov.services.panel import Actor, PanelService

REFS = [
    {
        "block": "БС",
        "position": "Руководитель строительства",
        "category": "РУПК",
        "sov_type": "Вехи",
    },
    {
        "block": "БС",
        "position": "Руководитель проекта строительства",
        "category": "КУПК",
        "sov_type": "Вехи от NPV",
    },
    {
        "block": "БС",
        "position": "Менеджер по строительству (ОВ и ВК)",
        "category": "РУПК",
        "sov_type": "Вехи",
    },
    {
        "block": "ДРП",
        "position": "Менеджер по разработке продукта",
        "category": "РУПК",
        "sov_type": "Вехи",
    },
]
FIXES = [
    {"wrong": "мнеджер", "right": "менеджер"},
    {"wrong": "строительтсву", "right": "строительству"},
    {"wrong": "ОК и ВК", "right": "ОВ и ВК"},
    {"wrong": "Руководителя проекта строительства", "right": "Руководитель проекта строительства"},
]


@pytest.fixture
def book():
    return PositionBook(REFS, FIXES, ["Twist", "Тестовый проект T01"])


def found(book, raw, block="БС"):
    ref = book.recognize(raw, block)["ref"]
    return ref and ref["position"]


def test_spaces_newlines_and_nbsp(book):
    assert clean("Руководитель\n строительства  ") == "Руководитель строительства"
    assert found(book, "Руководитель\nстроительства") == "Руководитель строительства"
    assert found(book, "руководитель строительства") == "Руководитель строительства"


def test_acting_prefix_removed(book):
    assert found(book, "и.о. Руководитель строительства") == "Руководитель строительства"
    assert found(book, "И. о.  руководитель строительства") == "Руководитель строительства"


def test_project_tail_removed_only_for_known_project(book):
    assert found(book, "Руководитель строительства Twist") == "Руководитель строительства"
    result = book.recognize("Руководитель проекта строительства Тестовый проект T01", "БС")
    assert result["ref"]["position"] == "Руководитель проекта строительства"
    assert "отрезан проект" in result["steps"]
    # Неизвестный хвост — вопрос, не угадываем.
    assert found(book, "Руководитель строительства Неведомый") is None


def test_typos_from_fix_table(book):
    assert (
        found(book, "Мнеджер по строительтсву (ОК и ВК)") == "Менеджер по строительству (ОВ и ВК)"
    )
    assert found(book, "Руководителя проекта строительства") == "Руководитель проекта строительства"


def test_unknown_and_other_block_is_a_question(book):
    assert found(book, "Руководитель строитльства") is None  # опечатки нет в таблице
    assert found(book, "Менеджер по разработке продукта", "БС") is None
    assert (
        found(book, "Менеджер по разработке продукта", "ДРП") == "Менеджер по разработке продукта"
    )


def test_category_and_sov_type_resolution():
    ref = REFS[0]
    filled = resolve({"category": None, "sov_type": None}, "x", ref)
    assert (filled["category"], filled["sov_type"], filled["position"]) == (
        "РУПК",
        "Вехи",
        "Руководитель строительства",
    )
    # Вид СОВ из 1С важнее справочника; «вр. KPI (Вехи)» — категория МП.
    own = resolve({"category": "РУПК", "sov_type": "вр. KPI (Вехи)"}, "x", ref)
    assert (own["category"], own["sov_type"]) == ("МП", "вр. KPI (Вехи)")


@pytest.fixture
def panel(tmp_path):
    registry = Registry(tmp_path / "sov.sqlite")
    service = PanelService(registry, Actor("Тестовый методолог"))
    service.demo(ROOT / "samples")
    yield service
    registry.dispose()


def setup_bs(panel, position, sov_type="Вехи"):
    employee, assignment = TABLES["employee"], TABLES["assignment"]
    with panel.registry.transaction() as connection:
        connection.execute(
            update(employee)
            .where(employee.c.tab_no == "T0001")
            .values(department="Отдел управления строительством БС", position=position)
        )
        connection.execute(
            update(assignment)
            .where(assignment.c.tab_no == "T0001")
            .values(position=None, sov_type=sov_type)
        )
    return next(a["id"] for a in panel.registry.rows("assignment") if a["tab_no"] == "T0001")


def test_samples_directory_loaded(panel):
    assert len(panel.registry.rows("position_ref")) == 33
    assert len(panel.registry.rows("position_fix")) == 4


def test_registry_recognizes_acting_with_project_tail(panel):
    identifier = setup_bs(panel, "и.о. Руководитель\nстроительства Тестовый проект T01")
    calculation = panel.calculate("2026-Q3", [identifier])
    person = calculation.results[identifier]["case"]["employees"][0]
    assert person["load_source"].startswith("таблица загрузки (БС")
    assert "POSITION_UNRECOGNIZED" not in {p["code"] for p in calculation.problems}


def test_registry_unrecognized_position_is_a_question(panel):
    identifier = setup_bs(panel, "Руководитель строитльства")
    calculation = panel.calculate("2026-Q3", [identifier])
    person = calculation.results[identifier]["case"]["employees"][0]
    assert person["load_source"] == "файл назначений"
    assert ("POSITION_UNRECOGNIZED", False) in {
        (p["code"], p["blocking"]) for p in calculation.problems
    }


def test_registry_temporary_kpi_is_mp(panel):
    identifier = setup_bs(panel, "Менеджер по строительству (фасады)", "вр. KPI (Вехи)")
    value = panel.calculate("2026-Q3", [identifier]).results[identifier]
    person = value["case"]["employees"][0]
    assert person["category"] == "МП"
    assert person["load_source"] == "правило МП (сл. 20)"


def test_lc_skb_column_for_bpsk_by_date():
    from sov.adapters.load import lc_skb_of, position_load, table_loads

    gip = "Главный инженер проекта"
    assert position_load("БПСК", gip, 4, False, "2026-08-01") == 0.75
    assert position_load("БПСК", gip, 4, False, "2026-08-01", lc_skb=True) == 0.5
    # Нет столбца «ЛЦ, объекты СКБ» — обычное значение этапа.
    rs = "Руководитель строительства"
    assert position_load("БС", rs, 4, False, "2026-08-01", lc_skb=True) == 1.0
    flags = [
        {
            "project_code": "A",
            "queue": "1",
            "date_from": "2026-08-01",
            "is_lc_skb": 1,
            "created_at": "1",
        },
        {
            "project_code": "A",
            "queue": "1",
            "date_from": "2026-09-01",
            "is_lc_skb": 0,
            "created_at": "2",
        },
    ]
    lookup, dates = lc_skb_of(flags)
    rows = [
        {
            "id": i,
            "project_code": p,
            "queue": "1",
            "stage": 4,
            "date_from": "2026-07-01",
            "date_to": "2026-09-30",
            "position": gip,
        }
        for i, p in ((1, "A"), (2, "B"))
    ]
    loads, _ = table_loads(rows, "БПСК", lambda a: False, lc_skb=lookup, flag_dates=dates)
    assert [(r["date_from"], r["load"]) for r in loads[1]] == [
        ("2026-07-01", 0.75),
        ("2026-08-01", 0.5),
        ("2026-09-01", 0.75),
    ]
    assert {r["load"] for r in loads[2]} == {0.75}


def test_lc_skb_flag_journal_and_rollback(panel):
    panel.set_lc_skb("TP-T01", "1", True, "2026-08-01", "Условное решение")
    state, row = panel.lc_skb("TP-T01", "1", "2026-09-30")
    assert state and row["basis"] == "Условное решение"
    assert not panel.lc_skb("TP-T01", "1", "2026-07-31")[0]
    report = panel.set_lc_skb("TP-T01", "1", False, "2026-09-01", "Снято условно")
    assert not panel.lc_skb("TP-T01", "1", "2026-09-30")[0]
    panel.rollback(report["upload_id"])
    assert panel.lc_skb("TP-T01", "1", "2026-09-30")[0]
    with pytest.raises(ValueError):
        panel.set_lc_skb("TP-T01", "1", True, "2026-08-01", " ")


def test_directory_edit_and_questions(panel):
    setup_bs(panel, "Руководитель строитльства")
    questions = panel.position_questions()
    assert [(q["block"], q["position"], q["staff"]) for q in questions] == [
        ("БС", "Руководитель строитльства", "T0001")
    ]
    fixes = [
        {"wrong": f["wrong"], "right": f["right"]} for f in panel.registry.rows("position_fix")
    ]
    report = panel.save_position_fixes(
        [*fixes, {"wrong": "строитльства", "right": "строительства"}], "Условная правка"
    )
    assert report["changed"] == 1
    assert panel.position_questions() == []
    panel.rollback(report["upload_id"])
    assert len(panel.position_questions()) == 1
    refs = [
        {k: r[k] for k in ("block", "position", "category", "sov_type", "load_row", "comment")}
        for r in panel.registry.rows("position_ref")
    ]
    refs[0]["comment"] = "уточнено"
    assert panel.save_positions(refs, "Условная правка")["changed"] == 1
    with pytest.raises(ValueError):
        panel.save_positions([{"block": "БС", "position": ""}], "x")
    with pytest.raises(ValueError):
        panel.save_positions(refs, "")


def rows(panel, table, **fields):
    return [r for r in panel.registry.rows(table) if all(r[k] == v for k, v in fields.items())]


def team_form(**values):
    return {
        "tab_no": "T0001",
        "new_employee": None,
        "project_code": "TP-T02",
        "queue": "1",
        "stage": 4,
        "position": None,
        "date_from": "2026-08-01",
        "category": "РУПК",
        "sov_type": "Вехи",
        "role": "другая",
        "load": 0.5,
        "basis": "Условный приказ",
        **values,
    }


def test_add_to_team_existing_and_new_employee(panel):
    report = panel.add_to_team(team_form())
    added = next(a for a in panel.registry.rows("assignment") if a["id"] == report["assignment_id"])
    assert (added["tab_no"], added["project_code"], added["date_from"]) == (
        "T0001",
        "TP-T02",
        "2026-08-01",
    )
    assert rows(panel, "assignment_load", assignment_id=report["assignment_id"])
    calc = panel.calculate("2026-Q3", [report["assignment_id"]])
    assert report["assignment_id"] in calc.results
    with pytest.raises(ValueError, match="уже есть"):
        panel.add_to_team(team_form())
    # Новый сотрудник БС: категория и вид СОВ — по справочнику.
    report = panel.add_to_team(
        team_form(
            tab_no="T9001",
            new_employee={
                "fio": "Тестов Новый",
                "department": "Отдел управления строительством БС",
                "position": "и.о. Руководитель строительства",
            },
            category=None,
            sov_type=None,
            load=None,
        )
    )
    added = next(a for a in panel.registry.rows("assignment") if a["id"] == report["assignment_id"])
    assert (added["category"], added["sov_type"], added["position"]) == (
        "РУПК",
        "Вехи",
        "Руководитель строительства",
    )
    assert rows(panel, "employee", tab_no="T9001")[0]["fio"] == "Тестов Новый"
    panel.rollback(report["upload_id"])
    assert not rows(panel, "employee", tab_no="T9001")
    with pytest.raises(ValueError, match="Этапа"):
        panel.add_to_team(team_form(stage=5))
    with pytest.raises(ValueError, match="основание"):
        panel.add_to_team(team_form(tab_no="T0003", basis=""))


def test_remove_from_team_closes_assignment_and_survives_reload(panel):
    identifier = next(a["id"] for a in panel.registry.rows("assignment") if a["tab_no"] == "T0001")
    with pytest.raises(ValueError, match="раньше"):
        panel.remove_from_team(identifier, "2000-01-01", "уход", "Условно")
    panel.remove_from_team(identifier, "2026-08-31", "перевод", "Условный приказ")
    row = rows(panel, "assignment", id=identifier)[0]
    assert (row["date_to"], row["end_reason"]) == ("2026-08-31", "перевод")
    with pytest.raises(ValueError, match="уже убран"):
        panel.remove_from_team(identifier, "2026-08-31", "уход", "Условно")
    # Повторная загрузка файла назначений без даты ухода не отменяет ручное решение.
    preview = panel.preview(ROOT / "samples" / "assignments.csv")
    assert any("сохранено «перевод»" in w["message"] for w in preview.warnings)
    panel.apply(preview)
    row = rows(panel, "assignment", id=identifier)[0]
    assert (row["date_to"], row["end_reason"]) == ("2026-08-31", "перевод")
