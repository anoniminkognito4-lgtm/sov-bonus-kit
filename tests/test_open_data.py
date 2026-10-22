"""Производственный календарь и ключевая ставка: разбор источников и дата выплаты."""

import sys

from conftest import ROOT

from sov.engine.rules.dates import pay_date

sys.path.insert(0, str(ROOT / "tools"))
from fetch_open_data import calendar_days, key_rate_changes  # noqa: E402


def test_key_rate_table_to_changes():
    html = (
        "<tr><td>03.01.2026</td><td>16,00</td></tr>"
        "<tr><td>02.01.2026</td><td>16,00</td></tr>"
        "<tr><td>01.01.2026</td><td>17,50</td></tr>"
    )
    assert key_rate_changes(html) == [("2026-01-01", 0.175), ("2026-01-02", 0.16)]


def test_calendar_only_differences_from_normal_week():
    xml = (
        '<calendar year="2027"><days>'
        '<day d="01.01" t="1" h="1"/>'  # пятница, праздник
        '<day d="01.02" t="1"/>'  # суббота: и так выходной
        '<day d="02.20" t="3"/>'  # суббота: рабочий
        '<day d="02.22" t="2"/>'  # сокращённый: рабочий
        "</days></calendar>"
    )
    assert calendar_days(xml, 2027) == [("2027-01-01", "выходной"), ("2027-02-20", "рабочий")]


def test_pay_date_uses_holidays_and_working_weekends():
    # Выплата 10-го числа второго месяца после квартала; 10.05.2026 — воскресенье.
    assert pay_date("2026-02-10") == "2026-05-08"
    assert pay_date("2026-02-10", holidays=("2026-05-08",)) == "2026-05-07"
    assert pay_date("2026-02-10", workdays=("2026-05-10",)) == "2026-05-10"
