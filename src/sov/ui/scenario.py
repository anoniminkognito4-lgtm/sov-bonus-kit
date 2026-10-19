"""Карточка моделирования. Черновик существует только в session_state."""

import hashlib
import json
from datetime import date, timedelta
from time import perf_counter

import streamlit as st

from sov.engine.rules.dates import closure_date
from sov.engine.rules.financial import is_kupk
from sov.engine.rules.kpi import quarter_add
from sov.services.scenario import quarterly_periods


def reset_scenario():
    for key in list(st.session_state):
        if key.startswith("wi_") and key not in ("wi_open", "wi_context"):
            del st.session_state[key]


def money(value):
    return "—" if value is None else f"{value:,.2f}".replace(",", " ")


def render_scenario(service, identifier, case, selected_quarter):
    context = hashlib.sha256(
        json.dumps(
            [str(service.registry.path), service.registry.revision(), service.actor.name, case],
            sort_keys=True,
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()
    if st.session_state.get("wi_context") != context:
        reset_scenario()
        st.session_state["wi_context"] = context
        st.session_state["wi_open"] = False
    if st.button("Что если", key="whatif_open"):
        st.session_state["wi_open"] = True
    if not st.session_state["wi_open"]:
        return
    st.divider()
    st.subheader("Что если")
    st.warning("Моделирование, не является обещанием выплаты")
    st.button("Сбросить сценарий", key="wi_reset", on_click=reset_scenario)
    historical = st.checkbox("Исторический сценарий (включая закрытые вехи)", key="wi_historical")
    st.caption(
        "В обычном сценарии факты, NPV и доля БС закрытых вех сохраняются. "
        "Исторический сценарий позволяет сравнить другие условия уже завершённых событий. "
        "Дата в сценарии одновременно означает факт и закрытие в 1С."
    )
    person = case["employees"][0]
    changes, dates = {}, {}
    with st.expander("Сроки вех", expanded=True):
        for index, (name, milestone) in enumerate(
            [*((m["name"], m) for m in case["milestones"]), ("final", case["final"])]
        ):
            plan = person.get("individual_dates", {}).get(name, milestone.get("plan"))
            if not plan:
                st.caption(f"{milestone['name']}: нет плановой даты")
                continue
            if closure_date(milestone) and not historical:
                st.caption(f"{milestone['name']} · закрыта {closure_date(milestone)}")
                continue
            active = st.checkbox(f"Моделировать: {milestone['name']}", key=f"wi_date_on_{index}")
            if not active:
                continue
            mode = st.radio(
                "Задать факт",
                ["Датой", "Сдвигом от плана"],
                horizontal=True,
                key=f"wi_date_mode_{index}",
            )
            if mode == "Датой":
                value = st.date_input(
                    f"Факт · {milestone['name']}",
                    value=date.fromisoformat(milestone.get("fact") or plan),
                    key=f"wi_date_{index}",
                    format="DD.MM.YYYY",
                )
            else:
                offset = st.slider(
                    "Дней после плана (минус — раньше)", -100, 100, 0, key=f"wi_offset_{index}"
                )
                value = date.fromisoformat(plan) + timedelta(days=offset)
                st.caption(f"Факт: {value:%d.%m.%Y} · срок для сравнения: {plan}")
            if value.isoformat() != milestone.get("fact") or not closure_date(milestone):
                dates[name] = value.isoformat()
    if dates:
        changes["dates"] = dates
    with st.expander("Условия расчёта", expanded=True):
        if person["category"] == "РУПК" and not case.get("bs", {}).get("enabled"):
            st.caption("Для этого назначения доступны сроки, дата ухода, БГБ и загрузка.")
        if is_kupk(person["category"]):
            for key, label, original in [
                ("current", "Текущий NPV, ₽", person.get("npv_current")),
                ("q1", "NPV через квартал, ₽", person.get("npv_checkpoints", {}).get("q1")),
                ("q2", "NPV через полгода, ₽", person.get("npv_checkpoints", {}).get("q2")),
            ]:
                value = st.number_input(
                    label,
                    value=float(original) if original is not None else None,
                    step=1_000_000.0,
                    key=f"wi_npv_{key}",
                )
                if value is not None and value != original:
                    if key == "current":
                        changes["npv_current"] = value
                    else:
                        changes.setdefault("npv_checkpoints", {})[key] = value
            st.caption("Стартовый NPV фиксирован. Контрольные NPV меняют условия частей 25%.")
        if person["category"] == "МП":
            for period in quarterly_periods(case):
                original = person.get("kpi_quarterly", {}).get(period)
                active = original is not None or st.checkbox(
                    f"Задать отсутствующий KPI · {period}", key=f"wi_kpi_on_{period}"
                )
                if active:
                    value = st.slider(
                        f"KPI · {period}, %",
                        0.0,
                        150.0,
                        float(original * 100) if original is not None else 80.0,
                        step=1.0,
                        key=f"wi_kpi_{period}",
                    )
                    if original is None or value != original * 100:
                        changes.setdefault("kpi_quarterly", {})[period] = value / 100
        if case.get("bs", {}).get("enabled") and person["category"] in ("КУПК", "РУПК"):
            original = case["bs"].get("share_transferred")
            active = original is not None or st.checkbox(
                "Задать отсутствующую долю БС", key="wi_bs_on"
            )
            if active:
                value = st.slider(
                    "Доля перевыставления БС, %",
                    0.0,
                    100.0,
                    float(original * 100) if original is not None else 0.0,
                    step=1.0,
                    key="wi_bs",
                )
                if original is None or value != original * 100:
                    changes["bs_share"] = value / 100
    with st.expander("Дата ухода, БГБ и загрузка · Группа СОВ"):
        original = person.get("leave_date")
        leave = st.checkbox("Уход с проекта", value=bool(original), key="wi_leave_on")
        value = None
        if leave:
            value = st.date_input(
                "Дата ухода",
                value=date.fromisoformat(original or person["start"]),
                key="wi_leave",
                format="DD.MM.YYYY",
            ).isoformat()
        if value != original:
            changes["leave_date"] = value
        for index, period in enumerate(person["periods"]):
            st.caption(f"Период {period['from']} — {period['to']}")
            left, right = st.columns(2)
            bgb = left.number_input(
                "БГБ, ₽",
                min_value=0.0,
                value=float(period["bgb"]) if period["bgb"] is not None else None,
                step=100_000.0,
                key=f"wi_bgb_{index}",
            )
            load = right.number_input(
                "Загрузка, %",
                min_value=0.0,
                max_value=100.0,
                value=float(period["load"] * 100) if period["load"] is not None else None,
                key=f"wi_load_{index}",
            )
            values = {}
            if bgb is not None and bgb != period["bgb"]:
                values["bgb"] = bgb
            if load is not None and (period["load"] is None or load != period["load"] * 100):
                values["load"] = load / 100
            if values:
                changes.setdefault("periods", {})[index] = values
    next_quarter = quarter_add(selected_quarter, 1)
    reference = date(int(next_quarter[:4]), int(next_quarter[-1]) * 3 - 2, 1) - timedelta(days=1)
    as_of = st.date_input(
        "Ближайшая выплата начиная с", value=reference, key="wi_as_of", format="DD.MM.YYYY"
    ).isoformat()
    started = perf_counter()
    try:
        result = service.simulate(identifier, changes, as_of=as_of, historical=historical)
    except ValueError as error:
        st.error(str(error))
        return
    st.session_state["wi_result"] = result
    st.caption(f"Пересчёт: {perf_counter() - started:.3f} с · сценарий в реестр не записывается")
    now, projected = result["baseline"]["employees"][0], result["scenario"]["employees"][0]
    for column, label, employee, nearest in zip(
        st.columns(2),
        ["Сейчас", "По сценарию"],
        [now, projected],
        [result["nearest_now"], result["nearest_scenario"]],
    ):
        with column:
            st.markdown(f"**{label}**")
            delta = (
                employee["total_payable"] - now["total_payable"] if label == "По сценарию" else None
            )
            st.metric(
                "Итого за этап, ₽",
                money(employee["total_payable"]),
                money(delta) if delta is not None else None,
            )
            st.write(f"Проценты: {money(employee['interest']['total'])} ₽")
            st.write(
                f"Ближайшая выплата: {nearest['date'] or 'нет рассчитанной выплаты'} · "
                f"{money(nearest['amount'])} ₽"
            )
            st.caption(
                f"ПБ: {money(employee['pb'])} ₽ · "
                f"К фин: {employee['k_fin'] if employee['k_fin'] is not None else '—'} · "
                f"БС: {employee['k_bs'] if employee['k_bs'] is not None else '—'}"
            )
    st.write(
        "Разница в ближайшую выплату, ₽",
        money(result["nearest_scenario"]["amount"] - result["nearest_now"]["amount"]),
    )
    st.caption(
        "Итого включает только суммы со статусом «рассчитано». "
        "Незаполненные и приостановленные суммы исключены; части финала уже входят в итог."
    )
    rows = [
        {key: money(value) if key.endswith("₽") else value for key, value in row.items()}
        for row in result["rows"]
    ]
    st.dataframe(rows, hide_index=True, width="stretch")
    if result["prices"]:
        st.write("Цена дня")
        st.caption(
            "Прибавка за один день раньше выбранного факта; "
            "без заданного факта — относительно срока. "
            "У промежуточной вехи на потолке и в зоне просрочки ≥100 дней — 0. "
            "Для ключевой вехи действует К срока этапа."
        )
        st.dataframe(
            [
                {"Веха": r["name"], "Опорный факт": r["date"], "Цена дня, ₽": money(r["amount"])}
                for r in result["prices"]
            ],
            hide_index=True,
            width="stretch",
        )
    if result["scenario"]["problems"]:
        st.write("Ограничения сценария")
        st.dataframe(
            [
                {"Код": p["code"], "Веха": p.get("milestone"), "Описание": p["message"]}
                for p in result["scenario"]["problems"]
            ],
            hide_index=True,
            width="stretch",
        )
