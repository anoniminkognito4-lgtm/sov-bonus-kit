"""M8: хранилище вех, сценарии M01–M14 (docs/MILESTONES.md §9)."""

import json

import pytest
from conftest import ROOT
from sqlalchemy import insert, select

from sov.loaders.milestones import parse_date, parse_stage, read_workbook
from sov.loaders.service import apply_preview, prepare_upload, rollback_upload
from sov.registry.models import TABLES, milestone_raw_row
from sov.registry.session import Registry, timestamp
from sov.services.panel import Actor, PanelService

FOLDER = ROOT / "samples/milestones"
DATES = {
    "ms_seed.xlsx": "2026-07-01",
    "ms_update1.xlsx": "2026-10-01",
    "ms_update2.xlsx": "2026-11-01",
    "ms_codes.xlsx": "2026-07-01",
}


@pytest.fixture
def registry(tmp_path):
    registry = Registry(tmp_path / "sov.sqlite")
    apply_preview(registry, prepare_upload(registry, FOLDER / "ms_projects.csv"))
    yield registry
    registry.dispose()


def preview(registry, name, mode="update"):
    return prepare_upload(
        registry, FOLDER / name, options={"export_date": DATES[name], "mode": mode}
    )


def load(registry, name, mode="update"):
    current = preview(registry, name, mode)
    return current, apply_preview(registry, current)


def codes(current):
    return {p["code"] for p in current.problems}


def milestone(registry, project, name, queue="1", stage=None):
    rows = [
        m
        for m in registry.rows("milestone")
        if m["project_code"] == project
        and m["name"] == name
        and m["queue"] == queue
        and (stage is None or m["stage"] == stage)
    ]
    assert len(rows) == 1, rows
    return rows[0]


def versions(registry, code):
    rows = [r for r in registry.rows("milestone_plan_version") if r["milestone_code"] == code]
    return sorted(rows, key=lambda r: r["version"])


def test_m01_layouts_recognized_header_in_second_row():
    seed = read_workbook(FOLDER / "ms_seed.xlsx")
    assert seed["layout"] == "MILESTONES_1C" and seed["header_row"] == 2
    assert "Отдел разработки продукта" in seed["headers"]  # пробел в конце убран
    assert seed["rows"][0][0] == 3  # данные с 3-й строки
    coded = read_workbook(FOLDER / "ms_codes.xlsx")
    assert coded["layout"] == "MILESTONES_1C_CODES" and coded["header_row"] == 1
    assert read_workbook(ROOT / "samples/milestones_1c.xlsx")["layout"] == "MILESTONES_1C_CODES"
    with pytest.raises(ValueError, match="xlsx"):
        read_workbook(FOLDER / "выгрузка.xls")


def test_m02_stage_words_digits_and_names(registry):
    for value in ("Четвертый этап", "4", 4, "4 этап", "4-й этап", "Четвёртый\xa0этап", "этап 4"):
        assert parse_stage(value) == 4
    assert parse_stage("третий этап") == 3
    with pytest.raises(ValueError):
        parse_stage("Шестой этап")
    load(registry, "ms_seed.xlsx", "seed")
    before = milestone(registry, "TP-M2", "Утверждён ЭП")
    load(registry, "ms_update1.xlsx")
    after = milestone(registry, "TP-M2", "Утвержден ЭП")  # ё/е и двойной пробел — та же веха
    assert after["milestone_code"] == before["milestone_code"]


def test_m03_dates():
    assert parse_date("30.06.2026") == "2026-06-30"
    assert parse_date("15.08.26") == "2026-08-15"
    assert parse_date("01.01.45") == "1945-01-01"
    assert parse_date("01/09/2026") == parse_date("01-09-2026") == parse_date("01\\09\\2026")
    assert parse_date(46234) == "2026-07-31"  # число Excel
    assert parse_date("") is None
    with pytest.raises(ValueError):
        parse_date("31.02.2026")  # не перенос на март, как DateSerial


def test_m04_seed_summary_and_export_date(registry):
    current, _ = load(registry, "ms_seed.xlsx", "seed")
    stats = current.stats
    assert (stats["projects"], stats["queues"], stats["stages"], stats["milestones"]) == (
        2,
        3,
        5,
        15,
    )
    problems = {(p["code"], p.get("queue"), p.get("stage")) for p in current.problems}
    assert ("MS_STAGE_NO_KEY", "2", 4) in problems
    assert ("MS_STAGE_MULTI_KEY", "1", 5) in problems
    assert {"MS_NO_CODES", "MS_NO_CLOSE_COLUMN", "MS_NO_PLAN_DATE"} <= codes(current)
    first = versions(registry, milestone(registry, "TP-M1", "Веха Б")["milestone_code"])[0]
    assert first["effective_date"] == "2026-07-01"
    assert first["basis"] == "начальная загрузка, выгрузка 1С от 01.07.2026"


def test_m05_natural_key_stable_and_duplicates_block(registry):
    load(registry, "ms_seed.xlsx", "seed")
    code = milestone(registry, "TP-M1", "Веха Б")["milestone_code"]
    assert code.startswith("TP-M1/1/4/")
    load(registry, "ms_update1.xlsx")
    assert milestone(registry, "TP-M1", "Веха Б")["milestone_code"] == code
    current = preview(registry, "ms_update2.xlsx")
    duplicates = [p for p in current.problems if p["code"] == "MS_DUPLICATE_KEY"]
    assert len(duplicates) == 2 and all(p["blocking"] for p in duplicates)


def test_m06_milestone_without_queue_belongs_to_project(registry):
    load(registry, "ms_seed.xlsx", "seed")
    common = milestone(registry, "TP-M1", "Общая веха проекта", queue="")
    assert common["queue_raw"] == "" and common["milestone_code"].startswith("TP-M1/-/4/")


def test_m07_plan_moved_new_version_old_kept(registry):
    load(registry, "ms_seed.xlsx", "seed")
    current, _ = load(registry, "ms_update1.xlsx")
    code = milestone(registry, "TP-M1", "Веха Б")["milestone_code"]
    history = versions(registry, code)
    assert [(v["version"], v["plan_date"], v["effective_date"]) for v in history] == [
        (1, "2026-09-30", "2026-07-01"),
        (2, "2026-10-31", "2026-10-01"),
    ]
    moved = next(p for p in current.problems if p["code"] == "MS_PLAN_MOVED")
    assert moved["shift_days"] == 31
    key = next(p for p in current.problems if p["code"] == "MS_KEY_PLAN_MOVED")
    assert key["shift_days"] == 31 and key["assignments"] == 0


def test_m08_fact_appears_changes_and_closed_quarter_blocks(registry):
    load(registry, "ms_seed.xlsx", "seed")
    load(registry, "ms_update1.xlsx")
    target = milestone(registry, "TP-M1", "Веха А", queue="2")
    assert target["fact_date"] == "2026-08-25" and target["status"] == "Выполнена"
    current = preview(registry, "ms_update2.xlsx")
    assert "MS_FACT_CHANGED" in codes(current)
    with registry.transaction() as connection:
        connection.execute(
            insert(TABLES["accrual"]).values(
                row_key="test-accrual",
                id="test-accrual",
                milestone_code=target["milestone_code"],
                kind="аванс за веху",
                valid_from=timestamp(),
                created_at=timestamp(),
            )
        )
    current = preview(registry, "ms_update2.xlsx")
    blocked = next(p for p in current.problems if p["code"] == "MS_FACT_CHANGED_CLOSED")
    assert blocked["blocking"] and blocked["after"] == "2026-08-26"
    apply_preview(registry, current)
    assert milestone(registry, "TP-M1", "Веха А", queue="2")["fact_date"] == "2026-08-25"


def test_m09_fact_removed_status_back_key_changed_not_applied(registry):
    load(registry, "ms_seed.xlsx", "seed")
    load(registry, "ms_update1.xlsx")
    current, _ = load(registry, "ms_update2.xlsx")
    assert {"MS_FACT_REMOVED", "MS_KEY_CHANGED"} <= codes(current)
    assert milestone(registry, "TP-M1", "Веха А")["fact_date"] == "2026-06-30"
    assert milestone(registry, "TP-M1", "Веха Б")["is_key"] == 0


def test_m10_missing_in_file_is_question_other_projects_untouched(registry):
    load(registry, "ms_seed.xlsx", "seed")
    load(registry, "ms_codes.xlsx")
    current, _ = load(registry, "ms_update1.xlsx")
    missing = [p for p in current.problems if p["code"] == "MS_MISSING_IN_FILE"]
    assert [p["milestone"] for p in missing] == ["Веха Г"] and not missing[0]["blocking"]
    assert milestone(registry, "TP-M2", "Веха Г")  # не удалена
    assert milestone(registry, "TP-M3", "Веха кода 1", stage=2)  # вне файла — не тронута
    assert "TP-M3" not in current.stats["by_project"]


def test_m11_unknown_status_then_status_alias(registry):
    load(registry, "ms_seed.xlsx", "seed")
    current, _ = load(registry, "ms_update1.xlsx")
    unknown = next(p for p in current.problems if p["code"] == "MS_STATUS_UNKNOWN")
    assert unknown["status"] == "Отменена" and unknown["blocking"]
    assert milestone(registry, "TP-M2", "Веха В")["status"] == "Отменена"  # строка загружена
    PanelService(registry, Actor("Тестовый методолог")).save_status_alias(
        "Отменена", "inactive", "Условное письмо 1С", "условно.pdf"
    )
    assert "MS_STATUS_UNKNOWN" not in codes(preview(registry, "ms_update1.xlsx"))


def test_m12_rename_and_move_need_decision(registry):
    load(registry, "ms_seed.xlsx", "seed")
    load(registry, "ms_update1.xlsx")
    current = preview(registry, "ms_update2.xlsx")
    renamed = next(p for p in current.problems if p["code"] == "MS_POSSIBLY_RENAMED")
    moved = next(p for p in current.problems if p["code"] == "MS_POSSIBLY_MOVED")
    assert renamed["milestone"] == "Веха В (новое имя)" and renamed["blocking"]
    assert moved["milestone"] == "Веха новая" and moved["blocking"]
    old = milestone(registry, "TP-M2", "Веха В")["milestone_code"]
    PanelService(registry, Actor("Тестовый методолог")).save_milestone_decision(
        "milestone_rename",
        {"milestone_code": old, "new_name": "Веха В (новое имя)"},
        "Условное письмо",
        "f.pdf",
    )
    apply_preview(registry, preview(registry, "ms_update2.xlsx"))
    renamed_row = milestone(registry, "TP-M2", "Веха В (новое имя)")
    assert renamed_row["milestone_code"] == old and len(versions(registry, old)) == 1


def test_m13_two_part_milestones(registry):
    current, _ = load(registry, "ms_seed.xlsx", "seed")
    one = milestone(registry, "TP-M1", "Завершены работы ЖБ (часть 1)")
    two = milestone(registry, "TP-M1", "Завершены работы ЖБ (часть 2)")
    assert one["milestone_code"] != two["milestone_code"]
    pairs = [p for p in current.problems if p["code"] == "MS_PART_PAIR"]
    assert [p["milestone"] for p in pairs] == ["Устройство кровли (часть 1)"]


def test_m14_reload_noop_and_rollback_restores(registry):
    load(registry, "ms_seed.xlsx", "seed")
    state = {t: registry.rows(t) for t in ("milestone", "milestone_plan_version", "milestone_key")}
    current, result = load(registry, "ms_update1.xlsx")
    again, second = load(registry, "ms_update1.xlsx")
    assert not again.changes and second["noop"]
    with registry.engine.connect() as connection:
        raw = connection.execute(
            select(milestone_raw_row).where(milestone_raw_row.c.upload_id == result["upload_id"])
        ).all()
    assert len(raw) == 15 and json.loads(raw[0].raw_json)["Наименование"] == "Веха А"
    rollback_upload(registry, result["upload_id"])
    assert {t: registry.rows(t) for t in state} == state
    with registry.engine.connect() as connection:
        assert not connection.execute(
            select(milestone_raw_row).where(milestone_raw_row.c.upload_id == result["upload_id"])
        ).all()
