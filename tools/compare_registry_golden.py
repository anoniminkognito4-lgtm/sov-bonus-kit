"""Сверка M2. T16/T20 — без процентов, согласно CLAUDE.md (одна ставка samples)."""

import argparse
import json
from copy import deepcopy
from pathlib import Path

if __package__:
    from tools.compare_golden import compare
else:
    from compare_golden import compare


def compare_registry(expected, actual):
    expected, actual = deepcopy(expected), deepcopy(actual)
    if expected["id"] in ("T16", "T20"):
        for result in (expected, actual):
            for employee in result["employees"]:
                employee.pop("interest")
                employee.pop("total_payable")
                for payout in employee["payouts"]:
                    payout.pop("interest")
                    payout.pop("amount")
    return compare(expected, actual)


def main():
    parser = argparse.ArgumentParser(description="Сверка расчёта по реестру с эталонами M2")
    parser.add_argument("results", type=Path)
    parser.add_argument(
        "--golden", type=Path, default=Path(__file__).resolve().parents[1] / "tests/golden/spec"
    )
    args = parser.parse_args()
    failed = 0
    paths = sorted(args.golden.glob("*.json"))
    if not paths:
        parser.error("Не найдены эталоны")
    for path in paths:
        result_path = args.results / path.name
        if not result_path.exists():
            differences = ["Нет результата"]
        else:
            differences = compare_registry(
                json.loads(path.read_text(encoding="utf-8")),
                json.loads(result_path.read_text(encoding="utf-8")),
            )
        failed += bool(differences)
        suffix = " (без процентов)" if path.stem in ("T16", "T20") else ""
        print(f"{path.stem}: {'РАСХОЖДЕНИЯ' if differences else 'OK'}{suffix}")
        for difference in differences:
            print(f"  {difference}")
    print(f"Итого: {failed} из {len(paths)} кейсов с расхождениями")
    return int(bool(failed))


if __name__ == "__main__":
    raise SystemExit(main())
