"""Пакетный запуск расчёта на кейсах; формулы появятся на этапе M1."""

import argparse
import json
from pathlib import Path

from sov.engine.calculate import calculate_case


def main() -> int:
    parser = argparse.ArgumentParser(description="Расчёт условных кейсов СОВ")
    parser.add_argument("cases", type=Path, help="Папка входных JSON-кейсов")
    parser.add_argument("results", type=Path, help="Папка результатов sov-result/1")
    args = parser.parse_args()
    if args.cases.resolve() == args.results.resolve():
        parser.error("Папка результатов должна отличаться от папки исходных кейсов")
    paths = sorted(args.cases.glob("*.json"))
    if not paths:
        parser.error(f"Нет JSON-кейсов: {args.cases}")
    results = []
    for path in paths:
        try:
            case = json.loads(path.read_text(encoding="utf-8-sig"))
            results.append((path.name, calculate_case(case)))
        except (ValueError, KeyError, TypeError, OSError) as error:
            parser.exit(2, f"Ошибка в кейсе {path.name}: {error}\n")
    args.results.mkdir(parents=True, exist_ok=True)
    for name, result in results:
        (args.results / name).write_text(
            json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
            encoding="utf-8",
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
