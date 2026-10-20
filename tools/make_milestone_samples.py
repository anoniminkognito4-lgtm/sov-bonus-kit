"""Условные выгрузки вех для M8: tests/milestones/scenarios.json → samples/milestones/.

Вёрстка как у реальной выгрузки (docs/MILESTONES.md §9): строка 1 — номера колонок,
шапка во 2-й строке, данные с колонки B, даты текстом ДД.ММ.ГГГГ, этап словами,
заголовок с пробелом на конце, колонки отделов. ms_codes.xlsx — макет с кодами.

    uv run python tools/make_milestone_samples.py
"""

import csv
import json
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook

ROOT = Path(__file__).resolve().parents[1]
HEADERS = (
    "Статус",
    "Наименование",
    "Шаблон",
    "Ключевая",
    "Вид",
    "Проект",
    "Очередь/корпус/строение",
    "Этап",
    "Заказчик",
    "Исполнитель",
    "Плановый срок",
    "Критерий закрытия",
    "Фактический срок",
    "Руководитель проекта",
    "Отдел разработки продукта ",
    "Отдел Интерьеров",
)
CODE_HEADERS = (*HEADERS[:13], "Код проекта", "Код вехи", "Дата закрытия в 1С")


def _value(value):
    if isinstance(value, dict):
        return value["excel"]  # дата числом Excel
    return value if value != "" else None


def write_file(path, spec):
    workbook = Workbook()
    workbook.properties.created = workbook.properties.modified = datetime(2000, 1, 1)
    sheet = workbook.active
    sheet.title = "Лист для загрузки вех" if spec.get("codes") else "Выгрузка"
    for row in spec["rows"]:
        status, name, key, project, queue, stage, plan, fact, template = row[:9]
        values = {
            "Статус": status,
            "Наименование": name,
            "Шаблон": template,
            "Ключевая": key,
            "Вид": "Основная",
            "Проект": project,
            "Очередь/корпус/строение": queue,
            "Этап": stage,
            "Плановый срок": plan,
            "Фактический срок": fact,
            "Руководитель проекта": "Да",
            "Отдел разработки продукта ": "Нет",
            "Отдел Интерьеров": "Нет",
        }
        if spec.get("codes"):
            values.update({"Код проекта": row[9], "Код вехи": row[10], "Дата закрытия в 1С": fact})
        row.append(values)
    if spec.get("codes"):
        sheet.append(CODE_HEADERS)
        for row in spec["rows"]:
            sheet.append([_value(row[-1].get(h, "")) for h in CODE_HEADERS])
    else:
        sheet.append([None, *range(1, len(HEADERS) + 1)])
        sheet.append([None, *HEADERS])
        for row in spec["rows"]:
            sheet.append([None, *(_value(row[-1].get(h, "")) for h in HEADERS)])
    workbook.save(path)


def main():
    scenarios = json.loads((ROOT / "tests/milestones/scenarios.json").read_text(encoding="utf-8"))
    folder = ROOT / "samples/milestones"
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "ms_projects.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.writer(stream, delimiter=";", lineterminator="\n")
        writer.writerow(
            ("project_code", "name", "queue", "blocks", "is_exception", "sale_date",
             "handover_to_completion_date")
        )
        for code, name, queue in scenarios["projects"]:
            writer.writerow((code, name, queue, "БПП ОУП", "Нет", "", ""))
    for name, spec in scenarios["files"].items():
        write_file(folder / name, json.loads(json.dumps(spec)))
        print(folder / name)


if __name__ == "__main__":
    main()
