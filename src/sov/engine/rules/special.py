"""Ограничения выплаты по ИС и сохранение ранее зафиксированных авансов."""

from sov.engine.rules.dates import as_date, closure_date


def freeze_advance(employee, item):
    fixed = employee.get("frozen_advances", {}).get(item["name"])
    if fixed:
        item.update(
            amount=fixed["amount"],
            pay_date=fixed["pay_date"],
            calc_status="рассчитано",
            recognition_date=fixed["event_date"],
            frozen=True,
        )
        if fixed.get("deferred") is not None:
            item["frozen_deferred"] = fixed["deferred"]
        if "base_amount" in item or fixed.get("redistribution_pct"):
            item["base_amount"] = fixed.get("base_amount", fixed["amount"])
        item["redistribution_pct"] = fixed.get("redistribution_pct", 0)


def frozen_payout(employee, item, index):
    fixed = employee.get("frozen_payouts", {})
    return next((p for p in fixed.values() if p.get("slot") == index), fixed.get(item["part"]))


def release_dates(case, employee, items, *, parts=False):
    end = employee.get("probation_end")
    release = employee.get("probation_release_date")
    if not end or employee.get("probation_result") != "пройден" or not release:
        return
    if release < end:
        raise ValueError("Дата выплаты после ИС раньше окончания ИС")
    for index, item in enumerate(items):
        if item.get("frozen") or (parts and frozen_payout(employee, item, index)):
            continue
        if item.get("pay_date") and item["pay_date"] <= release:
            source = next((m for m in case["milestones"] if m["name"] == item.get("name")), {})
            item["financial_event"] = item.get("event_date") if parts else closure_date(source)
            item["pay_date"] = release
            item["event_date" if parts else "recognition_date"] = release


def restrict_probation(case, source, result):
    end = source.get("probation_end")
    status = source.get("probation_result")
    if not end and not status:
        return
    as_of = case.get("as_of") or end
    failed = status == "не пройден"
    waiting = status != "пройден" or (end and as_of and as_of < end)
    for item in [*result["milestones"], *result["payouts"]]:
        if item.get("frozen"):
            continue
        if item.get("amount") is None or item["calc_status"] == "не участвует":
            continue
        before_end = end and item.get("pay_date") and item["pay_date"] < end
        release_pending = (
            source.get("probation_release_date")
            and as_of
            and source["probation_release_date"] > as_of
        )
        if failed:
            item.update(forfeited_amount=item["amount"], amount=0.0, calc_status="сгорело")
            if "bonus" in item:
                item.update(bonus=0.0, interest=0.0)
        elif waiting or before_end or release_pending:
            if item["calc_status"] in ("рассчитано", "приостановлено"):
                item["calc_status"] = "ожидает окончания ИС"
    if failed:
        result["final"].update(amount=0.0, calc_status="сгорело")
        result["interest"] = {
            "rows": [],
            "total": sum(p.get("interest") or 0 for p in result["payouts"] if p.get("frozen")),
        }
    elif waiting or any(p["calc_status"] == "ожидает окончания ИС" for p in result["payouts"]):
        result["final"]["calc_status"] = "ожидает окончания ИС"


def redistribution_factor(employee, milestones):
    percentages = employee.get("redistribution") or {}
    completed = [
        m
        for m in milestones
        if m.get("weight") is not None
        and m.get("fact")
        and m["fact"] >= employee["start"]
        and (
            not employee.get("leave_date")
            or (m.get("recognition_date") or m["fact"]) <= employee["leave_date"]
        )
    ]
    return (
        1 + sum(percentages.get(m["name"], 0) for m in completed) / len(completed)
        if completed
        else 1
    )


def sale_details(case, employee, params):
    sale = case.get("sale")
    if not sale:
        return None
    end = sale.get("work_end")
    limit = as_date(sale["date"])
    if not end or end < employee["start"]:
        raise ValueError("Продажа: РП должен указать дату завершения работы")
    extra = (as_date(end) - limit).days
    if extra > 0 and (extra > params.sale_extension_days or not sale.get("extension_approved")):
        raise ValueError("Работа после продажи требует согласования, не более 14 дней")
    if employee.get("leave_date"):
        end = min(end, employee["leave_date"])
    return {**sale, "work_end": end}


def handover_active(case):
    day = case.get("handover_date")
    finish = closure_date(case.get("final", {}))
    return bool(day and (not finish or day < finish))
