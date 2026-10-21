"""Загрузка по правилам СОВ, вехи из двух частей и неизвестные статусы вех."""

import json
from copy import deepcopy
from dataclasses import replace

import pytest
from conftest import ROOT, read_json
from sqlalchemy import update

from sov.adapters.load import (
    department_block,
    mp_loads,
    position_load,
    rp_set_load,
    table_loads,
)
from sov.engine.calculate import calculate_case
from sov.engine.params import DEFAULT_PARAMS, params_for_quarter
from sov.engine.rules.budget import project_bonus
from sov.registry.models import TABLES
from sov.registry.session import Registry
from sov.services.panel import Actor, PanelService


def assignment(identifier, project, queue, stage, date_from, date_to=None, position="ГИП"):
    return {
        "id": identifier,
        "project_code": project,
        "queue": queue,
        "stage": stage,
        "date_from": date_from,
        "date_to": date_to,
        "position": position,
    }


def test_department_and_position_tables_by_date():
    assert department_block("ОРП 2 ДРП БРП") == "ДРП"
    assert department_block("Отдел управления строительством БС") == "БС"
    assert department_block("ДУП БПСК") == "БПСК"
    assert department_block("Условное подразделение") is None
    rppp = "Руководитель проекта по продукту"
    assert position_load("ДРП", rppp, 4, False, "2025-07-01") == 0.1665
    assert (
        position_load("ДРП", "Руководитель группы разработки продукта", 4, False, "2026-01-01")
        == 0.1665
    )
    # С 01.07.2026 — слайд 18 v2 (D-059): ДРП снижена, второе название РППП тоже.
    assert position_load("ДРП", rppp, 4, False, "2026-07-01") == 0.14
    assert (
        position_load("ДРП", "Руководитель группы разработки продукта", 4, False, "2026-08-01")
        == 0.14
    )
    assert position_load("ДРП", rppp, 4, True, "2026-01-01") == 0.08325  # дострой
    assert position_load("ДРП", rppp, 4, False, "2025-06-30") is None  # до 01.07.2025 таблицы нет
    gip = "Главный инженер проекта"
    assert position_load("БПСК", gip, 5, False, "2026-10-01") == 0.5
    assert position_load("БПСК", "Главный архитектор проекта", 5, False, "2026-01-01") == 0.15
    # Главного архитектора на слайде нет: после 01.07.2026 остаётся значение макрокниги.
    assert position_load("БПСК", "Главный архитектор проекта", 5, False, "2026-08-01") == 0.15
    junior = "Младший менеджер по разработке продукта"
    assert position_load("ДРП", junior, 2, False, "2026-08-01") is None
    senior = "Старший менеджер по строительству (фасады)"
    assert position_load("БС", senior, 2, False, "2026-06-30") == 0.25  # макрокнига
    assert position_load("БС", senior, 2, False, "2026-08-01") == 0  # слайд 18 v2
    assert position_load("БС", "Младший менеджер по строительству", 3, False, "2026-08-01") is None


def test_completion_rule_against_macrobook_file():
    # Дострой = этап 5 × 0,5; СКБ/ЛЦ = этап 2 × 0,5. Расхождения файла — D-061.
    mismatches = set()
    rows = [r for r in DEFAULT_PARAMS.position_loads if r[0] == "2025-07-01"]
    for _, block, names, stages, completion, special in rows:
        if completion is not None and completion != pytest.approx(stages[4] * 0.5):
            mismatches.add((block, names[0], "дострой"))
        if special is not None and special != pytest.approx(stages[1] * 0.5):
            mismatches.add((block, names[0], "СКБ/ЛЦ"))
    assert mismatches == {
        ("БПСК", "Руководитель проекта проектирования", "дострой"),
        ("БПП", "Руководитель проекта", "дострой"),
        ("БПП", "Руководитель проекта", "СКБ/ЛЦ"),
        ("БПП", "Заместитель руководителя проекта", "дострой"),
        ("БПП", "Заместитель руководителя проекта", "СКБ/ЛЦ"),
        ("БПП", "Менеджер проекта", "СКБ/ЛЦ"),
    }
    assert sum(row[1] == "БС" for row in rows) == 21
    v2 = [r for r in DEFAULT_PARAMS.position_loads if r[0] == "2026-07-01"]
    assert len(v2) == 27 and not any(r[1] == "БПП" for r in v2)
    v2_mismatches = {
        (block, names[0])
        for _, block, names, stages, completion, special in v2
        if (completion is not None and completion != pytest.approx(stages[4] * 0.5))
        or (special is not None and special != pytest.approx(stages[1] * 0.5))
    }
    assert v2_mismatches == {
        ("БПСК", "Руководитель проекта проектирования"),  # дострой 5% по слайду
        ("БПСК", "Главный инженер проекта ИС"),  # СКБ/ЛЦ 25% по слайду
    }


def test_queues_split_and_normalised_to_100_percent():
    # Сл. 15, пример 1: очередь 1 — 100%, очередь 2 — 75% → 57% и 43%.
    rows = [
        assignment("a1", "P", "1", 3, "2026-01-01", position="Главный инженер проекта"),
        assignment("a2", "P", "2", 4, "2026-01-01", position="Главный инженер проекта"),
    ]
    loads, issues = table_loads(rows, "БПСК", lambda a: False)
    assert loads["a1"][0]["load"] == pytest.approx(0.5 / 0.875)
    assert loads["a2"][0]["load"] == pytest.approx(0.375 / 0.875)
    assert not issues["a1"] and not issues["a2"]


def test_second_project_above_100_percent_keeps_actual_shares():
    # Сл. 16, пример 2: две очереди проекта А и проект Б → 50%, 37,5%, 50%.
    gip = "Главный инженер проекта"
    rows = [
        assignment("a1", "A", "1", 3, "2026-01-01", position=gip),
        assignment("a2", "A", "2", 4, "2026-01-01", position=gip),
        assignment("b", "B", "1", 5, "2026-03-01", position=gip),
    ]
    loads, _ = table_loads(rows, "БПСК", lambda a: False)
    before, after = loads["a1"][0], loads["a1"][1]
    assert before["date_to"] == "2026-02-28" and before["load"] == pytest.approx(0.5 / 0.875)
    assert after["load"] == pytest.approx(0.5)
    assert loads["a2"][1]["load"] == pytest.approx(0.375)
    assert loads["b"][0]["load"] == pytest.approx(0.5)


QUEUES_ONLY = replace(DEFAULT_PARAMS, load_normalization="только деление на очереди")


def test_single_project_below_100_percent_by_mode():
    # И-9: «буквально» (по умолчанию, как макрокнига) поднимает 33,3% до 100%.
    rows = [assignment("a", "P", "1", 4, "2025-07-01", position="Менеджер по разработке продукта")]
    loads, _ = table_loads(rows, "ДРП", lambda a: False)
    assert loads["a"] == [{"date_from": "2025-07-01", "date_to": None, "load": 1.0}]
    loads, _ = table_loads(rows, "ДРП", lambda a: False, QUEUES_ONLY)
    assert loads["a"] == [
        {"date_from": "2025-07-01", "date_to": "2026-06-30", "load": 0.333},  # макрокнига
        {"date_from": "2026-07-01", "date_to": None, "load": 0.25},  # слайд 18 v2
    ]


GIP, MRP, RPP = (
    "Главный инженер проекта",
    "Менеджер по разработке продукта",
    ("Руководитель проекта проектирования"),
)


@pytest.mark.parametrize(
    "rows, block, literal, queues_only",
    [
        # К1: БПСК, ГИП, один проект на 5 этапе (50%).
        ([("A", "1", 5, GIP)], "БПСК", 2_400_000, 1_200_000),
        # К2: ДРП, МРП, один проект на 4 этапе (33,3%).
        ([("A", "1", 4, MRP)], "ДРП", 2_400_000, 600_000),  # v2: 25%
        # К3: ГИП, проект А на 5 этапе + проект Б на 2 этапе: 150% — режимы совпадают.
        ([("A", "1", 5, GIP), ("B", "1", 2, GIP)], "БПСК", 3_600_000, 3_600_000),
        # К4: ГИП, две очереди проекта А на 4 этапе: 37,5% + 37,5% → 50% + 50% в обоих.
        ([("A", "1", 4, GIP), ("A", "2", 4, GIP)], "БПСК", 2_400_000, 2_400_000),
        # К5: БПСК, РПП, один проект на 5 этапе (5%).
        ([("A", "1", 5, RPP)], "БПСК", 2_400_000, 120_000),
    ],
)
def test_pb_difference_between_modes(rows, block, literal, queues_only):
    items = [
        assignment(f"a{i}", p, q, st, "2026-10-01", "2027-09-30", position)
        for i, (p, q, st, position) in enumerate(rows)
    ]
    for params, expected in ((DEFAULT_PARAMS, literal), (QUEUES_ONLY, queues_only)):
        loads, _ = table_loads(items, block, lambda a: False, params)
        total = sum(
            project_bonus(
                [{"from": "2026-10-01", "to": "2027-09-30", "bgb": 2_400_000, "load": r["load"]}],
                "2026-10-01",
                "2027-09-30",
                params,
            )[0]
            for a in items
            for r in loads[a["id"]]
        )
        assert total == pytest.approx(expected)


@pytest.mark.parametrize(
    "projects",
    [
        [("A", "1")],
        [("A", "1"), ("B", "1")],
        [("A", "1"), ("B", "1"), ("C", "1")],
        [("A", "1"), ("A", "2"), ("B", "1")],
    ],
)
def test_mp_table_literal_equals_mp_rule(projects):
    # МП: 50% на всех этапах по таблице + «буквально» = правило «1 проект 100%, 2+ по 50%».
    items = [
        assignment(f"a{i}", p, q, 3, "2026-01-01", position="Менеджер проекта")
        for i, (p, q) in enumerate(projects)
    ]
    table, _ = table_loads(items, "БПП", lambda a: False)
    rule = mp_loads(items)
    assert {k: [r["load"] for r in v] for k, v in table.items()} == pytest.approx(
        {k: [r["load"] for r in v] for k, v in rule.items()}
    )


def test_mp_table_and_rule_differ_with_completion_stage():
    # Дострой у МП — 25%: проект на 3 этапе + проект в достройе → 66,7%/33,3% против 50%/50%.
    items = [
        assignment("a", "A", "1", 3, "2026-01-01", position="Менеджер проекта"),
        assignment("b", "B", "1", 5, "2026-01-01", position="Менеджер проекта"),
    ]
    table, _ = table_loads(items, "БПП", lambda a: a["id"] == "b")
    assert [table["a"][0]["load"], table["b"][0]["load"]] == pytest.approx([2 / 3, 1 / 3])
    rule = mp_loads(items)
    assert [rule["a"][0]["load"], rule["b"][0]["load"]] == [0.5, 0.5]


def test_table_v2_difference_is_its_own_reason():
    mrp = [assignment("a", "P", "1", 4, "2026-01-01", position="Менеджер по разработке продукта")]
    _, issues = table_loads(mrp, "ДРП", lambda a: False)
    assert issues["a"] == {"LOAD_TABLE_V2"}  # 33,3% → 25% с 01.07.2026
    gip = [assignment("b", "P", "1", 4, "2026-01-01", position="Главный инженер проекта")]
    _, issues = table_loads(gip, "БПСК", lambda a: False)
    assert not issues["b"]  # у ГИП на 4 этапе таблицы совпадают


def test_no_table_before_2025_07_01():
    rows = [assignment("a", "P", "1", 4, "2025-04-01", position="Менеджер по разработке продукта")]
    loads, issues = table_loads(rows, "ДРП", lambda a: False, QUEUES_ONLY)
    assert [(r["date_from"], r["load"]) for r in loads["a"]] == [
        ("2025-07-01", 0.333),
        ("2026-07-01", 0.25),
    ]
    assert issues["a"] == {"LOAD_BEFORE_TABLE", "LOAD_TABLE_V2"}


def test_unknown_position_reported():
    rows = [
        assignment(
            "a", "P", "1", 2, "2026-07-01", position="Младший менеджер по разработке продукта"
        )
    ]
    loads, issues = table_loads(rows, "ДРП", lambda a: False)
    assert loads["a"] == [] and issues["a"] == {"LOAD_POSITION_UNKNOWN"}


def test_mp_two_projects_half_each():
    rows = [assignment("a", "A", "1", 4, "2026-01-01"), assignment("b", "B", "1", 3, "2026-04-01")]
    loads = mp_loads(rows)
    assert [r["load"] for r in loads["a"]] == [1.0, 0.5]
    assert loads["b"][0]["load"] == 0.5


def test_rp_divisor_projects_then_calculators():
    base = {"rank": None, "class": "Премиум", "area_k_m2": 70, "stage": 4, "extra_objects": 0}
    rows = [
        {**base, "project_code": "A", "queue": "1", "is_queue": 0, "is_logistics": 0},
        *(
            {**base, "project_code": "B", "queue": q, "is_queue": 1, "is_logistics": 0}
            for q in ("1", "2", "3")
        ),
        {**base, "project_code": "C", "queue": "1", "is_queue": 0, "is_logistics": 0},
    ]
    result = rp_set_load(rows, "ЗРП")
    pct = result["load_pct"]
    assert pct == pytest.approx((result["x"] - 1) * 2 + 1)  # y = 1,0 для ЗРП (D-047)
    assert result["rows"][("A", "1")] == pytest.approx(pct / 3)
    assert result["rows"][("B", "2")] == pytest.approx(pct / 3 / 3)


def test_position_loads_overridable_by_edition():
    table = [
        ["2026-10-01", "ДРП", ["Менеджер по разработке продукта"], [1, 1, 1, 0.5, 0.5], None, None]
    ]
    rows = [
        {"edition": "SOV-test", "name": "effective_from", "value": "2026-Q4"},
        {"edition": "SOV-test", "name": "position_loads", "value": json.dumps(table)},
    ]
    params = params_for_quarter(rows, "2026-Q4")
    assert (
        position_load("ДРП", "Менеджер по разработке продукта", 4, False, "2026-10-01", params)
        == 0.5
    )
    rows[1]["value"] = json.dumps([["2026-10-01", "ДРП", [], [1], None, None]])
    with pytest.raises(ValueError):
        params_for_quarter(rows, "2026-Q4")


def split_case():
    case = deepcopy(read_json(ROOT / "tests/cases/T01.json"))
    case["milestones"][4]["name"] = "Завершены работы ЖБ (часть 1)"
    case["milestones"][5]["name"] = "Завершены работы ЖБ (часть 2)"
    return case


def test_two_part_milestones_share_one_weight():
    result = calculate_case(split_case())["employees"][0]
    weights = [m["weight"] for m in result["milestones"]]
    assert weights[:4] == [pytest.approx(0.2)] * 4
    assert weights[4:] == [pytest.approx(0.1)] * 2
    assert sum(weights) == pytest.approx(1)


def test_second_part_plan_is_key_milestone_and_early_close_ignored():
    case = split_case()
    case["milestones"][5]["fact"] = "2026-08-31"  # за 30 дней до РнВ
    result = calculate_case(case)
    part2 = result["employees"][0]["milestones"][5]
    assert part2["deviation_days"] == 30
    pb = result["employees"][0]["pb"]
    assert part2["amount"] == pytest.approx(pb * 0.67 * 0.1)  # опережение не увеличивает
    assert any(p["code"] == "MILESTONE_PART_PLAN" for p in result["problems"])


@pytest.fixture
def panel(tmp_path):
    registry = Registry(tmp_path / "sov.sqlite")
    service = PanelService(registry, Actor("Тестовый методолог"))
    service.demo(ROOT / "samples")
    yield service
    registry.dispose()


def cancel_milestone(panel, code="TP-T01-M06", status="Отменена"):
    table = TABLES["milestone"]
    with panel.registry.transaction() as connection:
        connection.execute(
            update(table).where(table.c.milestone_code == code).values(status=status)
        )
    return next(a["id"] for a in panel.registry.rows("assignment") if a["tab_no"] == "T0001")


def test_unknown_status_kept_in_weights_but_not_paid(panel):
    assignment_id = cancel_milestone(panel)
    calculation = panel.calculate("2026-Q3", [assignment_id])
    employee = calculation.results[assignment_id]["result"]["employees"][0]
    # Веса остальных вех до решения не пересчитываются: 6 вех по 1/6.
    assert [m["weight"] for m in employee["milestones"]] == [pytest.approx(1 / 6)] * 6
    unknown = employee["milestones"][5]
    assert unknown["calc_status"] == "требует решения" and unknown["amount"] > 0
    paid = sum(m["amount"] for m in employee["milestones"] if m["calc_status"] == "рассчитано")
    assert paid < employee["total_payable"] + 1e-6
    codes = {(p["code"], p["blocking"]) for p in calculation.problems}
    assert ("MILESTONE_STATUS_UNKNOWN", True) in codes


def test_status_alias_inactive_removes_and_reweights(panel):
    assignment_id = cancel_milestone(panel)
    panel.save_status_alias("Отменена", "inactive", "Условное письмо 1С", "условно.pdf")
    calculation = panel.calculate("2026-Q3", [assignment_id])
    employee = calculation.results[assignment_id]["result"]["employees"][0]
    assert [m["weight"] for m in employee["milestones"]] == [pytest.approx(0.2)] * 5
    assert "MILESTONE_STATUS_UNKNOWN" not in {p["code"] for p in calculation.problems}


def test_status_alias_working_status_counts_normally(panel):
    assignment_id = cancel_milestone(panel, status="Закрыта")
    panel.save_status_alias("Закрыта", "done", "Условное письмо 1С", "условно.pdf")
    employee = panel.calculate("2026-Q3", [assignment_id]).results[assignment_id]["result"][
        "employees"
    ][0]
    assert employee["milestones"][5]["calc_status"] == "рассчитано"
    with pytest.raises(ValueError):
        panel.save_status_alias("Закрыта", "другое", "основание", "файл")


def test_default_params_unchanged_for_goldens():
    assert DEFAULT_PARAMS.edition == "SOV-v2-2026Q3"
    assert DEFAULT_PARAMS.milestone_statuses == ("Назначена", "Выполнена")


def set_employee(panel, tab_no, **values):
    table = TABLES["employee"]
    with panel.registry.transaction() as connection:
        connection.execute(update(table).where(table.c.tab_no == tab_no).values(**values))


def test_registry_uses_position_table_for_dup(panel):
    set_employee(panel, "T0001", department="ДУП БПСК", position="Главный инженер проекта")
    table = TABLES["assignment"]
    with panel.registry.transaction() as connection:
        connection.execute(update(table).where(table.c.tab_no == "T0001").values(position=None))
    identifier = next(a["id"] for a in panel.registry.rows("assignment") if a["tab_no"] == "T0001")
    value = panel.calculate("2026-Q3", [identifier]).results[identifier]
    person = value["case"]["employees"][0]
    # 4 этап ГИП — 75%; один проект, режим «буквально» доводит до 100% (И-9).
    assert {p["load"] for p in person["periods"]} == {1.0}
    assert person["load_source"].startswith("таблица загрузки (БПСК")
    # С 01.04.2025 по 30.06.2025 таблицы ещё нет — этот период взят из файла.
    assert person["load_source"].endswith("+ файл назначений")
    assert {i["code"] for i in person["load_issues"]} == {"LOAD_BEFORE_TABLE"}


def test_registry_position_without_load_is_incomplete(panel):
    # Должность есть в справочнике, загрузки нет ни в одной таблице:
    # решение пользователя — ASSIGNMENT_INCOMPLETE, пока загрузку не скажут (D-098).
    set_employee(
        panel, "T0001", department="ОРП 1 ДРП", position="Младший менеджер по разработке продукта"
    )
    identifier = next(a["id"] for a in panel.registry.rows("assignment") if a["tab_no"] == "T0001")
    calculation = panel.calculate("2026-Q3", [identifier])
    person = calculation.results[identifier]["case"]["employees"][0]
    assert person["load_source"] == "файл назначений"
    codes = {(p["code"], p["blocking"]) for p in calculation.problems}
    assert ("ASSIGNMENT_INCOMPLETE", True) in codes
    assert "LOAD_POSITION_UNKNOWN" not in {c for c, _ in codes}


def test_macrobook_csv_is_the_default_table_and_replaceable(capsys):
    import csv
    import io
    import sys

    sys.path.insert(0, str(ROOT / "tools"))
    from load_table_param import main, read_table

    source = ROOT / "config/load_table_macrobook.csv"
    v2 = ROOT / "config/load_table_slide18_v2.csv"
    tables = read_table(source, "2025-07-01") + read_table(v2, "2026-07-01")
    assert tables == DEFAULT_PARAMS.position_loads
    main(
        [str(source), "2025-07-01", "--add", str(v2), "2026-07-01"]
        + ["--edition", "SOV-test", "--effective-from", "2026-Q3"]
    )
    rows = list(csv.DictReader(io.StringIO(capsys.readouterr().out), delimiter=";"))
    assert params_for_quarter(rows, "2026-Q3").position_loads == DEFAULT_PARAMS.position_loads
