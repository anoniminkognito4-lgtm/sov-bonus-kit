"""Загрузка комплекта образцов в порядке зависимостей."""

from pathlib import Path

from sov.loaders.service import apply_preview, prepare_upload

SAMPLE_ORDER = (
    "hr_employees.csv",
    "hr_bgb_history.csv",
    "projects.csv",
    "stages.csv",
    "assignments.csv",
    "milestones_1c.xlsx",
    "individual_dates.csv",
    "start_npv.csv",
    "npv_quarterly.csv",
    "bs_transfer.csv",
    "kpi_mp.csv",
    "stage5_expense.csv",
    "key_rates.csv",
    "holidays.csv",
    "redistribution.csv",
    "rp_project_sets.csv",
)


def load_samples(registry, folder, author="Группа СОВ"):
    reports = []
    for name in SAMPLE_ORDER:
        preview = prepare_upload(registry, Path(folder) / name, author)
        if preview.errors:
            raise ValueError(f"{name}: {preview.errors}")
        report = apply_preview(registry, preview)
        reports.append(
            {
                "file": name,
                **report,
                "summary": preview.summary,
                "unmatched_rows": preview.unmatched,
                "warnings": preview.warnings,
            }
        )
    return reports
