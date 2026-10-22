"""Выгрузка 1С УП ШР «Проектные команды» (D-103): сценарии загрузчика."""

import sys

import pytest
from conftest import ROOT
from openpyxl import Workbook

from sov.loaders.service import apply_records
from sov.registry.session import Registry
from sov.services.panel import Actor, PanelService

sys.path.insert(0, str(ROOT / "tools"))
from make_upshr_samples import HEADERS  # noqa: E402

SAMPLE = ROOT / "samples/upshr/upshr_team.xlsx"
PROJECT = "Тестовый проект Ю"


@pytest.fixture
def panel(tmp_path):
    registry = Registry(tmp_path / "sov.sqlite")
    service = PanelService(registry, Actor("Тестовый методолог"))
    for name in ("projects.csv", "stages.csv", "milestones_1c.xlsx", "positions.csv"):
        service.apply(service.preview(ROOT / "samples" / name))
    service.apply(service.preview(ROOT / "samples/position_fixes.csv"))
    # Условная очередь с этапами 3 и 4 для перехода между этапами.
    records = [
        ("project", {"project_code": "TP-U1", "queue": "1", "name": PROJECT, "blocks": "БС"}),
        ("project", {"project_code": "TP-U1", "queue": "2", "name": PROJECT, "blocks": "БС"}),
    ]
    for stage, plan in ((3, "2026-03-31"), (4, "2026-12-31")):
        code = f"TP-U1-K{stage}"
        records += [
            (
                "stage",
                {
                    "project_code": "TP-U1",
                    "queue": "1",
                    "stage": stage,
                    "key_milestone_code": code,
                    "key_milestone_name": "Ключевая",
                },
            ),
            (
                "milestone",
                {
                    "milestone_code": code,
                    "project_code": "TP-U1",
                    "queue": "1",
                    "stage": stage,
                    "name": "Ключевая",
                    "is_key": 1,
                },
            ),
            ("milestone_plan_version", {"milestone_code": code, "version": 1, "plan_date": plan}),
        ]
    apply_records(registry, records, "условная очередь", "тест", "test")
    yield service
    registry.dispose()


def team_file(tmp_path, rows, name="team.xlsx"):
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["Проектные команды"])
    sheet.append(HEADERS)
    for row in rows:
        sheet.append(row)
    path = tmp_path / name
    workbook.save(path)
    return path


def row(**values):
    base = {
        "Проект": PROJECT,
        "Очередь/строение": "1",
        "Мотивационный этап": "Четвертый этап",
        "ФИО": "Тестов Иван Иванович",
        "Подразделение": "ОУС БС",
        "Должность": "Руководитель строительства",
        "Дата начала": "01.04.2026",
        "Дата окончания": "31.12.2026",
        "Покинул проект": "Нет",
        "Уволен": "Нет",
        "Категория": "РУПК",
        "Общая расчетная загрузка": None,
    }
    base.update(values)
    return [base[h] for h in HEADERS]


def codes(preview):
    return {p["code"] for p in preview.problems}


def assignments(panel, **fields):
    return [
        a for a in panel.registry.rows("assignment") if all(a[k] == v for k, v in fields.items())
    ]


def test_u01_sample_file(panel):
    preview = panel.preview(SAMPLE)
    assert preview.kind == "upshr" and not preview.errors and not preview.unmatched
    assert preview.stats == {"rows": 4, "assignments": 4, "new_people": 3, "questions": 2}
    assert codes(preview) == {
        "UPSHR_FIO_SPACES",
        "UPSHR_LOAD_IGNORED",
        "UPSHR_NONSTANDARD_TERM",
        "UPSHR_CATEGORY_MISMATCH",
        "LEAVE_DATE_MISSING",
        "DISMISSAL_DATE_MISSING",
    }
    panel.apply(preview)
    people = {e["fio"]: e["person_id"] for e in panel.registry.rows("employee")}
    assert people == {
        "Тестов Ульян Петрович": "P0001",
        "Пробная Ирина Олеговна": "P0002",
        "Образцов Олег Олегович": "P0003",
    }
    # Двойной пробел в ФИО — тот же человек; «и.о.», хвост проекта и опечатка — по справочнику.
    assert {a["position"] for a in assignments(panel, person_id="P0001")} == {
        "Руководитель строительства"
    }
    assert assignments(panel, person_id="P0003")[0]["position"] == (
        "Менеджер по разработке продукта"
    )
    # Загрузка из колонки не берётся.
    assert not panel.registry.rows("assignment_load")
    # Повторная загрузка ничего не меняет.
    again = panel.preview(SAMPLE)
    assert not again.changes


def test_u02_leave_and_dismissal_questions_persist_until_answered(panel):
    panel.apply(panel.preview(SAMPLE))
    left = assignments(panel, person_id="P0001", project_code="TP-T02")[0]
    assert (left["left_1c"], left["date_to"], left["end_reason"]) == (1, "2026-10-31", None)
    calc = panel.calculate("2026-Q3")
    found = {(p["code"], p["person_id"], p["blocking"]) for p in calc.problems}
    assert ("LEAVE_DATE_MISSING", "P0001", True) in found
    assert ("DISMISSAL_DATE_MISSING", "P0003", True) in found
    # Дату ухода указывает Группа СОВ; сам загрузчик её не ставит.
    panel.remove_from_team(left["id"], "2026-08-15", "уход", "Условное письмо")
    calc = panel.calculate("2026-Q3")
    assert "LEAVE_DATE_MISSING" not in {p["code"] for p in calc.problems}
    # Повторная выгрузка с «Покинул проект = Да» подтверждает и дату не трогает.
    panel.apply(panel.preview(SAMPLE))
    left = assignments(panel, id=left["id"])[0]
    assert (left["date_to"], left["end_reason"]) == ("2026-08-15", "уход")


def test_u03_manual_leave_wins_over_file(panel, tmp_path):
    path = team_file(tmp_path, [row()])
    panel.apply(panel.preview(path))
    (assignment,) = assignments(panel, project_code="TP-U1")
    panel.remove_from_team(assignment["id"], "2026-06-30", "перевод", "Условный приказ")
    preview = panel.preview(path)
    (question,) = [p for p in preview.problems if p["code"] == "LEAVE_ACTIVE_IN_1C"]
    assert question["before"] == "2026-06-30"
    panel.apply(preview)
    (assignment,) = assignments(panel, project_code="TP-U1")
    assert (assignment["date_to"], assignment["end_reason"]) == ("2026-06-30", "перевод")
    calc = panel.calculate("2026-Q3", [assignment["id"]])
    assert ("LEAVE_ACTIVE_IN_1C", False) in {(p["code"], p["blocking"]) for p in calc.problems}


def test_u04_from_prev_stage_and_term_check(panel, tmp_path):
    rows = [
        row(
            **{
                "Мотивационный этап": "Третий этап",
                "Дата начала": "01.10.2025",
                "Дата окончания": "31.03.2026",
            }
        ),
        row(),
        row(
            **{
                "ФИО": "Пробнов Пётр Петрович",
                "Мотивационный этап": "Третий этап",
                "Дата начала": "01.10.2025",
                "Дата окончания": "31.03.2026",
            }
        ),
        row(
            **{
                "ФИО": "Пробнов Пётр Петрович",
                "Дата начала": "05.04.2026",
                "Дата окончания": "15.12.2026",
            }
        ),
    ]
    preview = panel.preview(team_file(tmp_path, rows))
    panel.apply(preview)
    stage4 = {
        a["person_id"]: a["from_prev_stage"]
        for a in assignments(panel, project_code="TP-U1", stage=4)
    }
    assert stage4 == {"P0001": 1, "P0002": 0}
    term = [p for p in preview.problems if p["code"] == "UPSHR_NONSTANDARD_TERM"]
    assert [(p["before"], p["after"]) for p in term] == [("2026-12-31", "2026-12-15")]


def test_u05_aliases_and_unmatched_projects(panel, tmp_path):
    rows = [
        row(**{"Проект": "Тестовый пр-т Ю", "Очередь/строение": "Корпус 1"}),
        row(**{"Проект": "Неизвестный проект"}),
        row(**{"Очередь/строение": ""}),  # у проекта две очереди — выбирать нельзя
    ]
    preview = panel.preview(team_file(tmp_path, rows))
    assert [u["line"] for u in preview.unmatched] == [3, 4, 5]
    panel.save_milestone_decision(
        "project_alias", {"from": "Тестовый пр-т Ю", "to": "TP-U1"}, "Условно", "письмо.pdf"
    )
    panel.save_milestone_decision(
        "queue_alias",
        {"project_code": "TP-U1", "code_raw": "Корпус 1", "queue": "1"},
        "Условно",
        "письмо.pdf",
    )
    preview = panel.preview(team_file(tmp_path, rows))
    assert [u["line"] for u in preview.unmatched] == [4, 5]


def test_u06_person_question_bad_values_and_roles(panel, tmp_path):
    panel.apply(panel.preview(team_file(tmp_path, [row()], "first.xlsx")))
    rows = [
        row(**{"Подразделение": "ДУП БПСК", "Должность": "Главный инженер проекта"}),
        row(**{"ФИО": "Иной Иван", "Мотивационный этап": "Шестой этап"}),
        row(**{"ФИО": "Иной Иван", "Дата начала": "01.01.2027"}),
        row(
            **{
                "ФИО": "Иная Анна Ивановна",
                "Должность": "Руководитель проекта строительства",
                "Категория": "КУПК",
            }
        ),
        row(**{"ФИО": "Иная Мария Ивановна", "Должность": "Начальник участка"}),
    ]
    preview = panel.preview(team_file(tmp_path, rows))
    assert {
        "PERSON_TRANSFER_OR_NAMESAKE",
        "UPSHR_BAD_VALUE",
        "UPSHR_START_AFTER_END",
        "POSITION_UNRECOGNIZED",
    } <= codes(preview)
    assert [u["line"] for u in preview.unmatched] == [3, 4, 5]
    panel.apply(preview)
    roles = {a["position"]: (a["role"], a["sov_type"]) for a in panel.registry.rows("assignment")}
    assert roles["Руководитель проекта строительства"] == ("РПС", "Вехи от NPV")
    # Нераспознанная должность загружается, вопрос остаётся в расчёте.
    assert roles["Начальник участка"] == ("другая", None)


def test_u07_csv_layout(panel, tmp_path):
    path = tmp_path / "team.csv"
    lines = [";".join(HEADERS), ";".join("" if v is None else str(v) for v in row())]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    preview = panel.preview(path)
    assert preview.kind == "upshr" and len(preview.changes) == 2
