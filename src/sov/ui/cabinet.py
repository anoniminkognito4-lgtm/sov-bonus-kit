"""Личный кабинет и чтение руководителя: только представления кабинетного сервиса."""

import streamlit as st

from sov.services.cabinet import EMPLOYEE
from sov.ui.scenario import money, render_scenario

STATUS_COMMENTS = {
    "ожидает окончания ИС": "Выплата ждёт успешного окончания ИС и подтверждённой даты выплаты.",
    "сгорело": "Бонус не выплачивается по условиям ИС или передачи проекта.",
    "не участвует": "Веха не участвует в расчёте по дате начала назначения.",
    "ждёт предыдущую веху": "Бонус будет выплачен после выполнения предшествующих вех.",
    "нет данных": "Для расчёта не хватает источников; сумма не подменяется нулём.",
    "не закрыта": "Нет подтверждённого закрытия в 1С.",
    "после ухода": "Веха закрыта после ухода с проекта.",
    "приостановлено": "Контрольный NPV ниже NPV завершения этапа.",
}


def render_cabinet(service, selected_quarter, as_of):
    own = service.actor.role == EMPLOYEE
    st.subheader("Личный кабинет" if own else "Проекты руководителя")
    st.caption("Демонстрационная учётная запись. Показаны только доступные назначения.")
    assignments = service.assignments()
    if not assignments:
        st.info(
            "Для выбранной учётной записи нет доступных назначений. "
            "Загрузите условные образцы через Группу СОВ."
        )
        return
    options = {
        r["id"]: f"{r['project_code']} · этап {r['stage']} · {r['tab_no']}" for r in assignments
    }
    identifier = st.selectbox(
        "Моё назначение" if own else "Назначение команды",
        list(options),
        format_func=options.get,
        key="cabinet_assignment",
    )
    view_key = (service.actor, identifier)
    if st.session_state.get("cabinet_view") != view_key:
        service.view(identifier)
        st.session_state["cabinet_view"] = view_key
    card = service.card(identifier, selected_quarter, as_of=as_of)
    employee = card["result"]["employees"][0]
    person = card["case"]["employees"][0]
    st.write(f"**{employee['fio']} · {employee['tab_no']}**")
    st.caption(f"{card['case']['project']} · {employee['category']} · начало {employee['start']}")
    st.info("Расчёт предварительный. Начисления подтверждаются закрытым снимком квартала.")
    columns = st.columns(3)
    for column, label, key in zip(
        columns, ["ПБ, ₽", "Пул авансов, ₽", "Пул финала, ₽"], ["pb", "advance_pool", "final_pool"]
    ):
        column.metric(label, money(employee[key]))
    npvs = person.get("npv_by_milestone", {})
    rows = [
        {
            "Веха": m["name"],
            "План": m["plan"],
            "Индивидуальный срок": m["individual_plan"],
            "Факт": m["fact"],
            "Отклонение, дней": m["deviation_days"],
            "Вес": m["weight"],
            "Сумма, ₽": money(m["amount"]),
            "Статус": m["calc_status"],
            "Выплата": m["pay_date"],
            "NPV": npvs.get(m["name"], person.get("npv_current")),
            "Комментарий": STATUS_COMMENTS.get(
                m["calc_status"],
                "Расчёт по СОВ с NPV квартала вехи; "
                f"текущий К фин: {employee['k_fin'] if employee['k_fin'] is not None else '—'}.",
            ),
        }
        for m in employee["milestones"]
    ]
    st.dataframe(rows, hide_index=True, width="stretch")
    st.write("Финальный бонус / остаток", money(employee["final"]["amount"]), "₽")
    st.dataframe(
        [
            {
                "Часть": p["part"],
                "Бонус, ₽": money(p["bonus"]),
                "Проценты, ₽": money(p["interest"]),
                "Выплата": p["pay_date"],
                "Контрольный NPV": p.get("npv_checkpoint"),
                "Статус": p["calc_status"],
                "Условие": "NPV не ниже финального"
                if index > 0 and len(employee["payouts"]) == 3
                else "По правилам этапа",
            }
            for index, p in enumerate(employee["payouts"])
        ],
        hide_index=True,
        width="stretch",
    )
    paid, accrued, forecast = st.columns(3)
    paid.metric("Подтверждено выплачено, ₽", money(card["totals"]["paid"]))
    accrued.metric("Начислено в закрытых кварталах, ₽", money(card["totals"]["accrued"]))
    forecast.metric(
        "Прогноз до конца этапа, ₽", money(card["forecast"]["employees"][0]["total_payable"])
    )
    st.caption(
        "Прогноз: оставшиеся вехи в срок, текущий NPV неизменен. Расчёт предварительный. "
        "Прочерк в выплаченном означает отсутствие подтверждения оплаты; "
        "дата выплаты сама по себе её не подтверждает."
    )
    if card["forecast"]["blocking_problems"]:
        st.warning("Прогноз неполный: есть недостающие данные или условия ручного расчёта.")
    if card["result"]["problems"]:
        with st.expander("Условия и ограничения расчёта"):
            st.dataframe(
                [{"Код": p["code"], "Пояснение": p["message"]} for p in card["result"]["problems"]],
                hide_index=True,
                width="stretch",
            )
    with st.expander("Начисления по закрытым снимкам"):
        st.dataframe(
            [
                {
                    "Квартал": r["quarter"],
                    "Вид": r["kind"],
                    "Сумма, ₽": money(r["amount"]),
                    "Выплата": r["pay_date"],
                    "Статус": r["calc_status"],
                    "Редакция": r["edition"],
                }
                for r in card["accruals"]
            ],
            hide_index=True,
            width="stretch",
        )
    data = service.statement(identifier, selected_quarter, as_of=as_of)
    st.download_button(
        "Скачать выписку · Excel",
        data,
        file_name=f"Выписка_{employee['tab_no']}_{selected_quarter}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        on_click=service.export_event,
        args=("cabinet_statement", identifier),
    )
    if own:
        render_scenario(service, identifier, card["case"], selected_quarter)
