"""Сотрудник без табельного номера: ФИО → person_id, вопросы и решения (D-101)."""

import sqlite3

import pytest

from sov.exports.files import payroll_rows
from sov.loaders.persons import People, next_person_id, norm
from sov.registry.session import Registry
from sov.services.panel import Actor, PanelService

HR = "fio;position;department;hire_date;dismissal_date;probation_end;probation_result\n"


def staff(*rows):
    return {
        "employee": [
            {"person_id": p, "fio": f, "department": d, "position": pos} for p, f, d, pos in rows
        ],
        "person_alias": [],
    }


def test_norm_spaces_case_yo():
    assert norm("  Тестов  Пётр   Иванович ") == "тестов петр иванович"


def test_match_rules():
    people = People(
        staff(
            ("P0001", "Тестов Пётр Иванович", "ДРП", "Менеджер"),
            ("P0002", "Пробная Анна Сергеевна", "БС", "Менеджер"),
        )
    )
    assert people.match("тестов петр иванович").person_id == "P0001"
    assert people.match("ТЕСТОВ  ПЕТР ИВАНОВИЧ", "ДРП", "Менеджер").status == "found"
    moved = people.match("Тестов Петр Иванович", "БС")
    assert (moved.status, moved.code) == ("question", "PERSON_TRANSFER_OR_NAMESAKE")
    other_position = people.match("Тестов Петр Иванович", "ДРП", "Руководитель")
    assert other_position.code == "PERSON_TRANSFER_OR_NAMESAKE"
    # То же имя и фамилия, другое отчество — вопрос.
    assert people.match("Тестов Пётр Петрович").code == "PERSON_SIMILAR"
    # Другая фамилия при тех же имени, отчестве и подразделении — вопрос (смена фамилии).
    assert people.match("Новая Анна Сергеевна", "БС").code == "PERSON_SIMILAR"
    assert people.match("Новая Анна Сергеевна", "ДРП").status == "new"
    assert people.match("Иной Иван Иванович").status == "new"


def test_namesakes_and_aliases():
    state = staff(
        ("P0001", "Тестов Иван Иванович", "ДРП", "Менеджер"),
        ("P0002", "Тестов Иван Иванович", "БС", "Менеджер"),
    )
    people = People(state)
    assert people.match("Тестов Иван Иванович", "БС").person_id == "P0002"
    assert people.match("Тестов Иван Иванович").code == "PERSON_AMBIGUOUS"
    state["person_alias"] = [
        {"fio": "тестов иван иванович", "department": "", "person_id": "P0001"}
    ]
    assert People(state).match("Тестов Иван Иванович").person_id == "P0001"
    assert next_person_id(state) == "P0003"


@pytest.fixture
def panel(tmp_path):
    registry = Registry(tmp_path / "sov.sqlite")
    yield PanelService(registry, Actor("Тестовый методолог"))
    registry.dispose()


def source(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def load(panel, path):
    preview = panel.preview(path)
    assert not preview.errors, preview.errors
    panel.apply(preview)
    return preview


def test_hr_by_fio_creates_numbers_and_reload_changes_nothing(panel, tmp_path):
    path = source(
        tmp_path,
        "hr.csv",
        HR
        + "Тестов Один Иванович;Менеджер;ДРП;2020-01-01;;;\n"
        + "Тестова Два Петровна;ГИП;ДУП БПСК;2021-01-01;;;\n",
    )
    load(panel, path)
    people = {e["fio"]: e for e in panel.registry.rows("employee")}
    assert people["Тестов Один Иванович"]["person_id"] == "P0001"
    assert people["Тестова Два Петровна"]["person_id"] == "P0002"
    assert all(e["tab_no"] is None for e in people.values())
    again = panel.preview(path)
    assert not again.changes and again.unchanged == 2
    # ФИО с другими пробелами и ё — тот же человек; данные обновляются.
    load(
        panel,
        source(tmp_path, "hr2.csv", HR + "тестов  один иванович;Менеджер;ДРП;2020-01-02;;;\n"),
    )
    person = next(e for e in panel.registry.rows("employee") if e["person_id"] == "P0001")
    assert person["hire_date"] == "2020-01-02"
    assert len(panel.registry.rows("employee")) == 2


def test_transfer_question_merge_split_and_rollback(panel, tmp_path):
    load(
        panel, source(tmp_path, "hr.csv", HR + "Тестов Один Иванович;Менеджер;ДРП;2020-01-01;;;\n")
    )
    moved = source(tmp_path, "moved.csv", HR + "Тестов Один Иванович;Менеджер;БС;2020-01-01;;;\n")
    preview = panel.preview(moved)
    assert not preview.changes and len(preview.unmatched) == 1
    (question,) = preview.problems
    assert question["code"] == "PERSON_TRANSFER_OR_NAMESAKE"
    assert question["candidates"][0]["person_id"] == "P0001"
    with pytest.raises(ValueError):
        panel.person_merge(question, "P0001", " ")
    report = panel.person_merge(question, "P0001", "Условный приказ о переводе")
    load(panel, moved)
    (person,) = panel.registry.rows("employee")
    assert (person["person_id"], person["department"]) == ("P0001", "БС")
    # Откат загрузки и решения возвращает вопрос.
    panel.rollback(panel.uploads()[0]["id"])
    panel.rollback(report["upload_id"])
    assert panel.preview(moved).problems
    # «Другой человек»: однофамилец получает свой номер.
    split = panel.person_split(question, "Условно: однофамилец")
    assert split["person_id"] == "P0002"
    preview = panel.preview(moved)
    assert not preview.problems and not preview.unmatched


def test_other_loaders_match_people_by_fio(panel, tmp_path):
    load(panel, source(tmp_path, "hr.csv", HR + "Тестов Один Иванович;МП;ОУП;2020-01-01;;;\n"))
    load(panel, source(tmp_path, "kpi.csv", "fio;quarter;kpi\nТЕСТОВ ОДИН ИВАНОВИЧ;2026-Q3;0.9\n"))
    assert panel.registry.rows("kpi")[0]["person_id"] == "P0001"
    preview = panel.preview(
        source(tmp_path, "kpi2.csv", "fio;quarter;kpi\nНеведомый Н Н;2026-Q3;1\n")
    )
    assert "не найден" in preview.unmatched[0]["message"]


def test_team_form_new_person_gets_number_and_rejects_duplicates(panel):
    from conftest import ROOT

    panel.demo(ROOT / "samples")
    form = {
        "person_id": None,
        "new_employee": {"fio": "Тестов Новый Человек", "department": "ОУП", "position": "МП"},
        "project_code": "TP-T02",
        "queue": "1",
        "stage": 4,
        "date_from": "2026-08-01",
        "category": "МП",
        "sov_type": "Вехи от KPI",
        "basis": "Условно",
    }
    report = panel.add_to_team(form)
    added = [e for e in panel.registry.rows("employee") if e["fio"] == "Тестов Новый Человек"]
    assert added[0]["person_id"] == "P0001"
    assert report["assignment_id"]
    with pytest.raises(ValueError, match="уже есть"):
        panel.add_to_team({**form, "project_code": "TP-T01"})


def test_payroll_has_empty_tab_number():
    closed = {
        "id": "S1",
        "payload": {
            "entries": [
                {
                    "person_id": "P0001",
                    "fio": "Тестов Один",
                    "department": "ДРП",
                    "project": "Тестовый проект",
                    "queue": "1",
                    "stage": 4,
                    "kind": "аванс за веху",
                    "milestone": "Веха",
                    "quarter": "2026-Q3",
                    "pay_date": "2026-10-15",
                    "amount": 1000.0,
                    "calc_status": "рассчитано",
                    "edition": "E",
                }
            ]
        },
    }
    (row,) = payroll_rows(closed)
    assert row[0] == "" and row[1:3] == ["Тестов Один", "ДРП"]


def test_old_database_gets_person_id_from_tab_no(tmp_path):
    path = tmp_path / "old.sqlite"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE employee (row_key TEXT PRIMARY KEY, tab_no TEXT, fio TEXT, "
            "valid_from TEXT NOT NULL, valid_to TEXT, source_file_id TEXT, "
            "created_at TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO employee VALUES ('[\"T0001\"]', 'T0001', 'Тестов', 't', NULL, NULL, 't')"
        )
    registry = Registry(path)
    (row,) = registry.rows("employee")
    assert row["person_id"] == "T0001"
    registry.dispose()
