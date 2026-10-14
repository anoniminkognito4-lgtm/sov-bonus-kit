"""Условные исправления для демонстрации M3; исходные samples не изменяются."""

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def make_panel_demo(folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    result = []
    for name in ("npv_quarterly.csv", "kpi_mp.csv"):
        with (ROOT / "samples" / name).open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream, delimiter=";")
            fields, rows = reader.fieldnames, list(reader)
        if name.startswith("npv"):
            for row in rows:
                row["fub_approved_date"] = "2026-10-01"
        else:
            rows.append({"tab_no": "T0022", "quarter": "2025-Q4", "kpi": ".8"})
        path = folder / name
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields, delimiter=";")
            writer.writeheader()
            writer.writerows(rows)
        result.append(path)
    return result


if __name__ == "__main__":
    for path in make_panel_demo(ROOT / "build/demo-corrections"):
        print(path)
