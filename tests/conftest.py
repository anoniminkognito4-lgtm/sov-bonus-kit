"""Загрузка неизменяемых тестовых данных комплекта."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def unit_cases():
    for path in sorted((ROOT / "tests/unit").glob("*.json")):
        name = path.stem.removesuffix("_cases")
        cases = read_json(path)["cases"]
        groups = cases if isinstance(cases, dict) else {"": cases}
        for group, rows in groups.items():
            for index, row in enumerate(rows, 1):
                rule = f"{name}.{group}" if group else name
                yield rule, row, f"{rule}-{index:02d}"
