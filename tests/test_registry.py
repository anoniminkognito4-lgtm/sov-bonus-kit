"""Приёмка M2: реальные загрузчики, SQLite, адаптер и независимые эталоны."""

import csv
import json
from copy import deepcopy

import pytest
from conftest import ROOT, read_json
from sqlalchemy import func, select

from sov.adapters.registry_case import RegistryAdapter
from sov.loaders.batch import SAMPLE_ORDER, load_samples
from sov.loaders.formats import read_source
from sov.loaders.service import apply_preview, prepare_upload, rollback_upload
from sov.registry.models import audit_log, record_version, snapshot, upload
from sov.registry.session import Registry
from tools.compare_golden import compare


def csv_file(path, headers, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream, delimiter=";")
        writer.writerow(headers)
        writer.writerows(rows)
    return path


@pytest.fixture
def registry(tmp_path):
    database = Registry(tmp_path / "Реестр с пробелами" / "СОВ.sqlite")
    yield database
    database.dispose()


@pytest.fixture(scope="module")
def loaded_registry(tmp_path_factory):
    database = Registry(tmp_path_factory.mktemp("registry") / "sov.sqlite")
    reports = load_samples(database, ROOT / "samples")
    assert all(not report["summary"]["errors"] for report in reports)
    yield database
    database.dispose()


@pytest.fixture(scope="module")
def registry_results(loaded_registry):
    with (ROOT / "samples/case_map.csv").open(encoding="utf-8-sig", newline="") as stream:
        mapping = list(csv.DictReader(stream, delimiter=";"))
    return RegistryAdapter(loaded_registry).calculate_mapped(mapping)


def without_interest(result):
    result = deepcopy(result)
    for employee in result["employees"]:
        employee.pop("interest")
        employee.pop("total_payable")
        for part in employee["payouts"]:
            part.pop("interest")
            part.pop("amount")
    return result


GOLDEN = sorted((ROOT / "tests/golden/spec").glob("*.json"))


@pytest.mark.parametrize("path", GOLDEN, ids=[p.stem for p in GOLDEN])
def test_registry_calculation_matches_golden(path, registry_results):
    expected, actual = read_json(path), registry_results[path.stem]
    if path.stem in ("T16", "T20"):
        expected, actual = without_interest(expected), without_interest(actual)
    differences = compare(expected, actual)
    assert not differences, "\n".join(differences)


def test_repeat_all_samples_changes_nothing(loaded_registry):
    before = loaded_registry.domain_state()
    with loaded_registry.engine.connect() as connection:
        upload_count = connection.scalar(select(func.count()).select_from(upload))
        audit_count = connection.scalar(select(func.count()).select_from(audit_log))
    reports = load_samples(loaded_registry, ROOT / "samples")
    assert all(report["noop"] for report in reports)
    assert loaded_registry.domain_state() == before
    with loaded_registry.engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(upload)) == upload_count
        assert connection.scalar(select(func.count()).select_from(audit_log)) == audit_count


def test_orphan_rp_examples_are_visible_but_not_inserted(loaded_registry):
    preview = prepare_upload(loaded_registry, ROOT / "samples/rp_project_sets.csv")
    assert preview.summary["unmatched"] == 3
    assert not preview.errors
    assert not loaded_registry.rows("rp_project_set")


def test_every_source_recognized_by_headers_under_another_filename(tmp_path):
    for index, name in enumerate(SAMPLE_ORDER):
        source = ROOT / "samples" / name
        target = tmp_path / f"другой файл {index}{source.suffix}"
        target.write_bytes(source.read_bytes())
        assert read_source(target)[0] == read_source(source)[0]


def test_preview_does_not_write_database_and_rollback_restores_state(registry):
    before = registry.domain_state()
    revision = registry.revision()
    preview = prepare_upload(registry, ROOT / "samples/hr_employees.csv", "Тестовый автор")
    assert preview.summary["added"] == 42
    assert registry.domain_state() == before
    assert registry.revision() == revision
    report = apply_preview(registry, preview)
    assert len(registry.rows("employee")) == 42
    assert rollback_upload(registry, report["upload_id"], "Тестовый автор")
    assert registry.domain_state() == before
    assert not rollback_upload(registry, report["upload_id"])
    with registry.engine.connect() as connection:
        events = connection.execute(select(audit_log)).mappings().all()
        assert events and all(event["who"] == "Тестовый автор" for event in events)


def test_stale_preview_and_changed_file_are_rejected(registry, tmp_path):
    source = tmp_path / "сотрудники.csv"
    source.write_bytes((ROOT / "samples/hr_employees.csv").read_bytes())
    preview = prepare_upload(registry, source)
    source.write_bytes(source.read_bytes().replace("Тестов Один".encode(), "Тестов Новый".encode()))
    with pytest.raises(ValueError, match="изменился"):
        apply_preview(registry, preview)
    other = prepare_upload(registry, ROOT / "samples/key_rates.csv")
    employee = prepare_upload(registry, source)
    apply_preview(registry, other)
    with pytest.raises(ValueError, match="устарел"):
        apply_preview(registry, employee)


def test_update_history_and_rollback(registry, tmp_path):
    apply_preview(registry, prepare_upload(registry, ROOT / "samples/hr_employees.csv"))
    before = registry.domain_state()
    source = tmp_path / "новое имя.csv"
    source.write_bytes(
        (ROOT / "samples/hr_employees.csv")
        .read_bytes()
        .replace("Тестов Один".encode(), "Тестов Обновлённый".encode())
    )
    preview = prepare_upload(registry, source)
    assert preview.summary["changed"] == 1
    report = apply_preview(registry, preview)
    with registry.engine.connect() as connection:
        versions = (
            connection.execute(
                select(record_version).where(
                    record_version.c.entity == "employee",
                    record_version.c.row_key == '["T0001"]',
                )
            )
            .mappings()
            .all()
        )
        assert len(versions) == 2
        assert sum(v["valid_to"] is None for v in versions) == 1
    rollback_upload(registry, report["upload_id"])
    assert registry.domain_state() == before


def test_rollback_cannot_leave_dependents_or_erase_newer_changes(registry, tmp_path):
    employees = apply_preview(registry, prepare_upload(registry, ROOT / "samples/hr_employees.csv"))
    bgbs = apply_preview(registry, prepare_upload(registry, ROOT / "samples/hr_bgb_history.csv"))
    before = registry.domain_state()
    with pytest.raises(ValueError, match="без сотрудника"):
        rollback_upload(registry, employees["upload_id"])
    assert registry.domain_state() == before
    rollback_upload(registry, bgbs["upload_id"])
    source = tmp_path / "employees.csv"
    source.write_bytes(
        (ROOT / "samples/hr_employees.csv")
        .read_bytes()
        .replace("Тестов Один".encode(), "Тестов Изменённый".encode())
    )
    changed = apply_preview(registry, prepare_upload(registry, source))
    with pytest.raises(ValueError, match="строки изменились"):
        rollback_upload(registry, employees["upload_id"])
    rollback_upload(registry, changed["upload_id"])
    rollback_upload(registry, employees["upload_id"])
    assert not registry.rows("employee")


def test_closed_snapshot_is_immutable_and_prohibits_rollback(registry):
    report = apply_preview(registry, prepare_upload(registry, ROOT / "samples/hr_employees.csv"))
    identifier = registry.close_snapshot("2026-Q3", "Тестовый автор")
    with registry.engine.connect() as connection:
        before = connection.scalar(select(snapshot.c.data_json).where(snapshot.c.id == identifier))
    with pytest.raises(ValueError, match="закрытии квартала"):
        rollback_upload(registry, report["upload_id"])
    apply_preview(registry, prepare_upload(registry, ROOT / "samples/key_rates.csv"))
    with registry.engine.connect() as connection:
        assert (
            connection.scalar(select(snapshot.c.data_json).where(snapshot.c.id == identifier))
            == before
        )
    assert json.loads(before)["employee"]


def test_bad_values_and_duplicate_conflicts_do_not_apply(registry, tmp_path):
    source = csv_file(
        tmp_path / "ставки.csv",
        ["date_from", "rate"],
        [["2026-01-01", "nan"], ["2026-01-02", "0,16"]],
    )
    preview = prepare_upload(registry, source)
    assert len(preview.errors) == 1
    with pytest.raises(ValueError):
        apply_preview(registry, preview)
    assert not registry.rows("key_rate")
    csv_file(source, ["date_from", "rate"], [["2026-01-01", 0.16], ["2026-01-01", 0.2]])
    preview = prepare_upload(registry, source)
    assert preview.errors
    with pytest.raises(ValueError):
        apply_preview(registry, preview)
    assert not registry.rows("key_rate")


def test_write_failure_rolls_back_everything(registry, monkeypatch):
    import sov.loaders.service as service

    preview = prepare_upload(registry, ROOT / "samples/hr_employees.csv")
    before = registry.domain_state()

    def fail(*args, **kwargs):
        raise RuntimeError("Условная ошибка записи журнала")

    monkeypatch.setattr(service, "_audit", fail)
    with pytest.raises(RuntimeError):
        apply_preview(registry, preview)
    assert registry.domain_state() == before
    assert registry.revision() == 0


def test_macro_files_rejected_before_reading(tmp_path):
    path = tmp_path / "не открывать.xlsm"
    with pytest.raises(ValueError, match="без макросов"):
        read_source(path)


def test_legacy_milestone_headers_use_existing_composite_keys(loaded_registry, tmp_path):
    from openpyxl import load_workbook

    workbook = load_workbook(ROOT / "samples/milestones_1c.xlsx")
    worksheet = workbook["Лист для загрузки вех"]
    worksheet.delete_cols(14, 3)
    path = tmp_path / "Вехи без кодов.xlsx"
    workbook.save(path)
    workbook.close()
    preview = prepare_upload(loaded_registry, path)
    assert not preview.errors and not preview.unmatched
    assert {"MS_NO_CODES", "MS_NO_CLOSE_COLUMN"} <= {p["code"] for p in preview.problems}
    # Вехи найдены по проекту, очереди, этапу и наименованию: добавляются только связи ключей.
    assert preview.changes and {c["table"] for c in preview.changes} == {"milestone_key"}
    apply_preview(loaded_registry, preview)
    assert not prepare_upload(loaded_registry, path).changes


def test_quarter_npv_and_bs_are_used_for_each_milestone(loaded_registry):
    from sov.engine.calculate import calculate_case

    adapter = RegistryAdapter(loaded_registry)
    assignment = adapter.one("assignment", tab_no="T0002")
    baseline = calculate_case(adapter.build_case(assignment["id"]))["employees"][0]
    for row in adapter.state["npv"]:
        if row["project_code"] == "TP-T02" and row["quarter"] == "2025-Q3":
            row["npv_forecast_paur"] = 6_000_000_000
    changed = calculate_case(adapter.build_case(assignment["id"]))["employees"][0]
    assert changed["milestones"][1]["amount"] == pytest.approx(
        baseline["milestones"][1]["amount"] * 1.2
    )
    assert changed["milestones"][0]["amount"] == baseline["milestones"][0]["amount"]
    assignment = adapter.one("assignment", tab_no="T0017")
    baseline = calculate_case(adapter.build_case(assignment["id"]))["employees"][0]
    for row in adapter.state["bs_share"]:
        if row["project_code"] == "TP-T10b" and row["quarter"] == "2025-Q2":
            row["share_transferred"] = 0.95
    changed = calculate_case(adapter.build_case(assignment["id"]))["employees"][0]
    assert changed["milestones"][0]["amount"] == pytest.approx(
        baseline["milestones"][0]["amount"] / 0.94
    )
    assert changed["milestones"][1]["amount"] == baseline["milestones"][1]["amount"]


def test_all_uploads_can_be_rolled_back_in_reverse_dependency_order(registry):
    before = registry.domain_state()
    reports = load_samples(registry, ROOT / "samples")
    for report in reversed(reports):
        rollback_upload(registry, report["upload_id"])
    assert registry.domain_state() == before
