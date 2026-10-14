"""Воспроизводимый стенд M2 на условных файлах samples."""

import argparse
import json
from pathlib import Path

from sov.adapters.registry_case import RegistryAdapter
from sov.loaders.batch import load_samples
from sov.loaders.formats import read_source
from sov.registry.session import Registry


def main():
    parser = argparse.ArgumentParser(description="Загрузка условных образцов и расчёт из SQLite")
    parser.add_argument("samples", type=Path)
    parser.add_argument("database", type=Path)
    parser.add_argument("results", type=Path)
    args = parser.parse_args()
    if args.samples.resolve() == args.results.resolve():
        parser.error("Папка результатов должна отличаться от samples")
    registry = Registry(args.database)
    try:
        reports = load_samples(registry, args.samples)
        kind, _, mapped, errors = read_source(args.samples / "case_map.csv")
        if kind != "case_map" or errors:
            raise ValueError(f"Неверная карта кейсов: {errors}")
        results = RegistryAdapter(registry).calculate_mapped([r for _, r in mapped])
        args.results.mkdir(parents=True, exist_ok=True)
        for identifier, result in results.items():
            (args.results / f"{identifier}.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                encoding="utf-8",
            )
        for report in reports:
            print(f"{report['file']}: {report['summary']}")
        print(f"Рассчитано кейсов из реестра: {len(results)}")
    except (ValueError, OSError) as error:
        parser.exit(2, f"Ошибка: {error}\n")
    finally:
        registry.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
