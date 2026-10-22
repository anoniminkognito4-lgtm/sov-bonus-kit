"""Открытые данные для комплекта: ключевая ставка ЦБ и производственный календарь РФ.

Запускается один раз на компьютере с доступом в интернет; приложению сеть не нужна.
Результат — CSV в формате загрузчиков реестра, его можно проверить глазами и положить
в репозиторий:

    config/key_rate_cbr.csv   date_from;rate   (ставка долей: 0.21 = 21%)
    config/calendar_ru.csv    date;kind        (kind: «выходной» — нерабочий будний день,
                                                «рабочий» — рабочая суббота или воскресенье)

    uv run python tools/fetch_open_data.py                 # 2026–2027
    uv run python tools/fetch_open_data.py --years 2025 2026 2027

Источники: www.cbr.ru (таблица «Ключевая ставка Банка России») и xmlcalendar.ru
(производственный календарь в XML: t=1 — нерабочий день, t=2 — сокращённый рабочий,
t=3 — рабочий день в субботу или воскресенье).
"""

import argparse
import csv
import re
import urllib.request
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KEY_RATE_URL = (
    "https://www.cbr.ru/hd_base/KeyRate/?UniDbQuery.Posted=True"
    "&UniDbQuery.From=17.09.2013&UniDbQuery.To={to}"
)
CALENDAR_URL = "https://xmlcalendar.ru/data/ru/{year}/calendar.xml"


def fetch(url):
    request = urllib.request.Request(url, headers={"User-Agent": "sov-bonus-kit"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read().decode("utf-8")


def key_rate_changes(html):
    """Таблица ЦБ (дата; ставка по дням) → только даты смены ставки, по возрастанию."""
    pairs = re.findall(r"<td>\s*(\d{2}\.\d{2}\.\d{4})\s*</td>\s*<td>\s*([\d\s]+,\d+)\s*</td>", html)
    if not pairs:
        raise ValueError("В ответе ЦБ не найдена таблица ключевой ставки")
    days = sorted(
        (date(int(y), int(m), int(d)), float(rate.replace(" ", "").replace(",", ".")) / 100)
        for (d, m, y), rate in ((day.split("."), rate) for day, rate in pairs)
    )
    changes = []
    for day, rate in days:
        if not changes or abs(changes[-1][1] - rate) > 1e-12:
            changes.append((day, rate))
    return [(day.isoformat(), round(rate, 6)) for day, rate in changes]


def calendar_days(xml, year):
    """XML календаря → [(дата, «выходной» | «рабочий»)]: только отличия от обычной недели."""
    result = []
    for month, day, kind in re.findall(r'<day\s+d="(\d{2})\.(\d{2})"\s+t="(\d)"', xml):
        current = date(year, int(month), int(day))
        if kind == "1" and current.weekday() < 5:
            result.append((current.isoformat(), "выходной"))
        elif kind == "3" and current.weekday() >= 5:
            result.append((current.isoformat(), "рабочий"))
    if not re.search(r'<day\s+d="', xml):
        raise ValueError(f"В календаре {year} нет дней")
    return sorted(result)


def write(path, header, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream, delimiter=";", lineterminator="\n")
        writer.writerow(header)
        writer.writerows(rows)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--years", nargs="+", type=int, default=[2026, 2027])
    parser.add_argument("--out", type=Path, default=ROOT / "config")
    args = parser.parse_args(argv)
    today = date.today().strftime("%d.%m.%Y")
    rates = key_rate_changes(fetch(KEY_RATE_URL.format(to=today)))
    write(args.out / "key_rate_cbr.csv", ("date_from", "rate"), rates)
    days = []
    for year in args.years:
        days += calendar_days(fetch(CALENDAR_URL.format(year=year)), year)
    write(args.out / "calendar_ru.csv", ("date", "kind"), days)
    print(f"Ключевая ставка: {len(rates)} изменений, последняя {rates[-1]}")
    print(f"Календарь {args.years}: {len(days)} особых дней")


if __name__ == "__main__":
    main()
