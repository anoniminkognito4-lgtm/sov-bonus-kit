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
    assert department_block("Отдел управления строительством БС") == "ДУС"
    assert department_block("Условное подразделение") is None
    rppp = "Руководитель проекта по продукту"
    assert position_load("ДРП", rppp, 4, False, "2026-06-30") == 0.1665  # до v2
    assert position_load("ДРП", rppp, 4, False, "2026-07-01") == 0.14  # v2
    assert position_load("ДРП", rppp, 1, True, "2026-06-30") is None  # дострой до v2 не задан
    gip = "Главный инженер проекта"
    assert position_load("ДУП", gip, 5, False, "2026-09-30") == 0.5
    assert position_load("ДУП", gip, 5, False, "2026-10-01") == 0.2  # v3
    assert (
        position_load("ДУП", "Руководитель проекта проектирования", 5, False, "2026-10-01") == 0.05
    )
    junior = "Младший менеджер по разработке продукта"
    assert position_load("ДРП", junior, 2, False, "2026-08-01") is None
    assert position_load("ДУС", "Младший менеджер по строительству", 3, False, "2026-08-01") == 1


def test_queues_split_and_normalised_to_100_percent():
    # Сл. 15, пример 1: очередь 1 — 100%, очередь 2 — 75% → 57% и 43%.
    rows = [
        assignment("a1", "P", "1", 3, "2026-01-01", position="Главный инженер проекта"),
        assignment("a2", "P", "2", 4, "2026-01-01", position="Главный инженер проекта"),
    ]
    loads, issues = table_loads(rows, "ДУП", lambda a: False)
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
    loads, _ = table_loads(rows, "ДУП", lambda a: False)
    before, after = loads["a1"][0], loads["a1"][1]
    assert before["date_to"] == "2026-02-28" and before["load"] == pytest.approx(0.5 / 0.875)
    assert after["load"] == pytest.approx(0.5)
    assert loads["a2"][1]["load"] == pytest.approx(0.375)
    assert loads["b"][0]["load"] == pytest.approx(0.5)


QUEUES_ONLY = replace(DEFAULT_PARAMS, load_normalization="только деление на очереди")


def test_single_project_below_100_percent_by_mode():
    # И-9: «буквально» (по умолчанию, как макрокнига) поднимает 25% до 100%.
    rows = [assignment("a", "P", "1", 4, "2026-07-01", position="Менеджер по разработке продукта")]
    loads, _ = table_loads(rows, "ДРП", lambda a: False)
    assert loads["a"] == [{"date_from": "2026-07-01", "date_to": None, "load": 1.0}]
    loads, _ = table_loads(rows, "ДРП", lambda a: False, QUEUES_ONLY)
    assert loads["a"] == [{"date_from": "2026-07-01", "date_to": None, "load": 0.25}]


@pytest.mark.parametrize(
    "rows, block, literal, queues_only",
    [
        # К1: ДУП, ГИП, один проект на 5 этапе (v3: 20%).
        ([("A", "1", 5)], "ДУП", 2_400_000, 480_000),
        # К3: проект А на 5 этапе + проект Б на 2 этапе: сумма 120% — режимы совпадают.
        ([("A", "1", 5), ("B", "1", 2)], "ДУП", 2_880_000, 2_880_000),
        # К4: две очереди проекта А на 4 этапе: 37,5% + 37,5% → 50% + 50% в обоих режимах.
        ([("A", "1", 4), ("A", "2", 4)], "ДУП", 2_400_000, 2_400_000),
    ],
)
def test_pb_difference_between_modes(rows, block, literal, queues_only):
    items = [
        assignment(f"a{i}", p, q, st, "2026-10-01", "2027-09-30", "Главный инженер проекта")
        for i, (p, q, st) in enumerate(rows)
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


def test_edition_change_splits_period():
    rows = [assignment("a", "P", "1", 4, "2026-01-01", position="Менеджер по разработке продукта")]
    loads, _ = table_loads(rows, "ДРП", lambda a: False, QUEUES_ONLY)
    assert [(r["date_from"], r["load"]) for r in loads["a"]] == [
        ("2026-01-01", 0.333),
        ("2026-07-01", 0.25),
    ]


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


def test_unknown_milestone_status_excluded_and_blocking(panel):
    table = TABLES["milestone"]
    with panel.registry.transaction() as connection:
        connection.execute(
            update(table).where(table.c.milestone_code == "TP-T01-M06").values(status="Отменена")
        )
    assignment_id = next(
        a["id"] for a in panel.registry.rows("assignment") if a["tab_no"] == "T0001"
    )
    calculation = panel.calculate("2026-Q3", [assignment_id])
    employee = calculation.results[assignment_id]["result"]["employees"][0]
    assert len(employee["milestones"]) == 5
    assert [m["weight"] for m in employee["milestones"]] == [pytest.approx(0.2)] * 5
    codes = {(p["code"], p["blocking"]) for p in calculation.problems}
    assert ("MILESTONE_STATUS_UNKNOWN", True) in codes


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
    assert person["load_source"].startswith("таблица загрузки СОВ (ДУП")
    assert not person["load_issues"]


def test_registry_unknown_position_falls_back_to_file(panel):
    set_employee(
        panel, "T0001", department="ОРП 1 ДРП", position="Младший менеджер по разработке продукта"
    )
    identifier = next(a["id"] for a in panel.registry.rows("assignment") if a["tab_no"] == "T0001")
    calculation = panel.calculate("2026-Q3", [identifier])
    person = calculation.results[identifier]["case"]["employees"][0]
    assert person["load_source"] == "файл назначений"
    assert ("LOAD_POSITION_UNKNOWN", False) in {
        (p["code"], p["blocking"]) for p in calculation.problems
    }
