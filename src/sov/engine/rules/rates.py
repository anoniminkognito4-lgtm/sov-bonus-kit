"""Интеграл ключевой ставки на полуоткрытом интервале, CALC_RULES §10.2."""

from sov.engine.rules.dates import as_date


def rate_on(value, history):
    eligible = [r for r in history if r["date_from"] <= value]
    return max(eligible, key=lambda r: r["date_from"])["rate"] if eligible else None


def rate_integral(start, end, history):
    left, right = as_date(start), as_date(end)
    if left == right:
        return 0.0
    sign = 1 if left < right else -1
    left, right = min(left, right), max(left, right)
    rates = sorted(history, key=lambda row: row["date_from"])
    if not rates or as_date(rates[0]["date_from"]) > left:
        return None
    total = 0.0
    for index, row in enumerate(rates):
        begin = max(left, as_date(row["date_from"]))
        finish = min(
            right, as_date(rates[index + 1]["date_from"]) if index + 1 < len(rates) else right
        )
        if finish > begin:
            total += row["rate"] * (finish - begin).days
    return sign * total
