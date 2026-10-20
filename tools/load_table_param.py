"""Таблица загрузки по должностям (CSV макрокниги) → параметр СОВ `position_loads`.

CSV: UTF-8, «;», проценты числами с запятой. Колонки: Подразделение; Должность;
Этап 1–5, %; Дострой, %; СКБ, ЛЦ, %. Пустая ячейка — значения нет.

    uv run python tools/load_table_param.py config/load_table_macrobook.csv 2025-07-01 \
        --edition SOV-v2-2026Q3 --effective-from 2026-Q3 > build/position_loads_param.csv

Результат загружается как файл параметров (`edition;name;value`) и заменяет таблицу
без правки кода. Без `--edition` печатается Python-литерал для `engine/params.py`.
"""

import argparse
import csv
import json
import sys
from pathlib import Path

# Названия одной должности в разных книгах (D-060).
ALIASES = {"Руководитель проекта по продукту": ("Руководитель группы разработки продукта",)}


def _pct(text):
    text = (text or "").strip()
    return None if not text else round(float(text.replace(",", ".")) / 100, 10)


def read_table(path, begin):
    rows = []
    with Path(path).open(encoding="utf-8-sig", newline="") as source:
        for record in csv.DictReader(source, delimiter=";"):
            if not (record.get("Должность") or "").strip():
                continue
            position = record["Должность"].strip()
            stages = tuple(_pct(record[f"Этап {n}, %"]) for n in range(1, 6))
            if any(value is None for value in stages):
                raise ValueError(f"Не заполнены этапы: {position}")
            rows.append(
                (
                    begin,
                    record["Подразделение"].strip(),
                    (position, *ALIASES.get(position, ())),
                    stages,
                    _pct(record["Дострой, %"]),
                    _pct(record["СКБ, ЛЦ, %"]),
                )
            )
    return tuple(rows)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("csv")
    parser.add_argument("begin", help="дата начала действия ГГГГ-ММ-ДД")
    parser.add_argument("--edition")
    parser.add_argument("--effective-from")
    args = parser.parse_args(argv)
    table = read_table(args.csv, args.begin)
    if args.edition:
        writer = csv.writer(sys.stdout, delimiter=";", lineterminator="\n")
        writer.writerow(("edition", "name", "value"))
        writer.writerow((args.edition, "effective_from", args.effective_from))
        writer.writerow((args.edition, "position_loads", json.dumps(table, ensure_ascii=False)))
    else:
        for row in table:
            print(f"    {row!r},")


if __name__ == "__main__":
    main()
