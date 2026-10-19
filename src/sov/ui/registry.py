"""Экраны «Реестр» и «Калькулятор»: смотрим реестр, выбираем этапы, получаем калькулятор."""

from datetime import date

import streamlit as st

from sov.exports.calculator import calculator_workbook

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

OVERVIEW_COLUMNS = {
    "fio": "ФИО",
    "tab_no": "Таб. №",
    "project": "Проект",
    "queue": "Очередь",
    "stage": "Этап",
    "category": "Категория",
    "status": "Статус",
    "load": "Загрузка",
    "overdue_count": "Просрочено вех",
    "max_overdue_days": "Макс. просрочка, дн",
    "individual_count": "Инд. сроков",
    "next_milestone": "Ближайшая веха",
    "next_date": "Её срок",
    "stage_end": "Окончание этапа (план)",
    "closed": "Вех закрыто",
    "total": "Вех всего",
    "late_closed": "Закрыто с просрочкой",
    "unknown_status": "Вех с неизвестным статусом",
    "date_from": "Начало на этапе",
    "leave_date": "Уход",
    "end_reason": "Причина ухода",
    "role": "Роль",
}
MILESTONE_COLUMNS = {
    "name": "Веха",
    "plan": "План",
    "individual_plan": "Инд. срок",
    "fact": "Факт (закрытие в 1С)",
    "deviation_days": "Откл., дн (− просрочка)",
    "state": "Состояние",
}
STATE_ICONS = {
    "закрыта": "✅ закрыта",
    "закрыта с просрочкой": "🟠 закрыта с просрочкой",
    "просрочена": "🔴 просрочена",
    "в работе": "⏳ в работе",
    "не участвует": "▫️ не участвует",
    "статус 1С неизвестен": "❓ статус 1С неизвестен",
}


def rub(value):
    return "—" if value is None else f"{value:,.0f} ₽".replace(",", " ")


def _date(value):
    return date.fromisoformat(value) if value else None


def _overview_rows(rows):
    result = []
    for row in rows:
        values = {label: row[key] for key, label in OVERVIEW_COLUMNS.items()}
        for key in ("Начало на этапе", "Уход", "Окончание этапа (план)", "Её срок"):
            values[key] = _date(values[key])
        values["Причина ухода"] = values["Причина ухода"] or ""
        values["Ближайшая веха"] = values["Ближайшая веха"] or ""
        result.append(values)
    return result


def open_calculator(option):
    st.session_state["calc_selection"] = [option]
    st.session_state["page"] = "Калькулятор"


def render_registry(view, as_of, goto_employee):
    st.subheader("Реестр: кто, где, сколько и в какие сроки")
    st.caption(
        "Одна строка — сотрудник на этапе проекта. Просрочки и состояние вех — на дату среза. "
        "Выберите строку ниже, чтобы увидеть вехи и открыть калькулятор этапа."
    )
    rows = view.overview(as_of)
    if not rows:
        st.info("Реестр пуст. Загрузите данные на экране «Загрузка».")
        return
    f1, f2, f3, f4 = st.columns([2, 2, 1.3, 1.3])
    search = f1.text_input("Поиск по ФИО или таб. №", key="reg_search").strip().lower()
    projects = sorted({r["project"] for r in rows})
    chosen_projects = f2.multiselect(
        "Проекты", projects, key="reg_projects", placeholder="Все проекты"
    )
    status = f3.selectbox("Статус", ["Работает", "Все", "Ушёл", "Этап завершён"], key="reg_status")
    category = f4.selectbox(
        "Категория",
        ["Все", *sorted({r["category"] for r in rows if r["category"]})],
        key="reg_category",
    )
    g1, g2, _ = st.columns([1.3, 1.3, 3])
    only_overdue = g1.checkbox("Только с просрочками", key="reg_overdue")
    only_individual = g2.checkbox("Только с инд. сроками", key="reg_individual")
    filtered = [
        r
        for r in rows
        if (not search or search in (r["fio"] or "").lower() or search in r["tab_no"].lower())
        and (not chosen_projects or r["project"] in chosen_projects)
        and (status == "Все" or r["status"] == status.lower())
        and (category == "Все" or r["category"] == category)
        and (not only_overdue or r["overdue_count"])
        and (not only_individual or r["individual_count"])
    ]
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Назначений", len(filtered))
    m2.metric("Сотрудников", len({r["tab_no"] for r in filtered}))
    m3.metric("С просроченными вехами", sum(1 for r in filtered if r["overdue_count"]))
    m4.metric("С инд. сроками", sum(1 for r in filtered if r["individual_count"]))
    st.dataframe(
        _overview_rows(filtered),
        hide_index=True,
        use_container_width=True,
        column_config={
            "Загрузка": st.column_config.NumberColumn(format="percent"),
            "Начало на этапе": st.column_config.DateColumn(format="DD.MM.YYYY"),
            "Уход": st.column_config.DateColumn(format="DD.MM.YYYY"),
            "Окончание этапа (план)": st.column_config.DateColumn(format="DD.MM.YYYY"),
            "Её срок": st.column_config.DateColumn(format="DD.MM.YYYY"),
        },
        key="reg_table",
    )
    if not filtered:
        return
    st.markdown("#### Вехи сотрудника на этапе")
    by_id = {r["assignment_id"]: r for r in filtered}
    identifier = st.selectbox(
        "Назначение",
        list(by_id),
        format_func=lambda key: (
            f"{by_id[key]['fio']} · {by_id[key]['project']} · очередь {by_id[key]['queue']} "
            f"· этап {by_id[key]['stage']}"
        ),
        key="reg_assignment",
    )
    row = by_id[identifier]
    assignment = next(a for a in view.rows("assignment") if a["id"] == identifier)
    milestones = view.milestones_for(assignment, as_of)
    st.dataframe(
        [
            {
                **{label: m[key] for key, label in MILESTONE_COLUMNS.items()},
                "Веха": ("🔑 " if m["is_key"] else "") + m["name"],
                "План": _date(m["plan"]),
                "Инд. срок": _date(m["individual_plan"]),
                "Факт (закрытие в 1С)": _date(m["fact"]),
                "Состояние": STATE_ICONS.get(m["state"], m["state"]),
            }
            for m in milestones
        ],
        hide_index=True,
        use_container_width=True,
        column_config={
            key: st.column_config.DateColumn(format="DD.MM.YYYY")
            for key in ("План", "Инд. срок", "Факт (закрытие в 1С)")
        },
        key="reg_milestones",
    )
    b1, b2, _ = st.columns([1.4, 1.4, 3])
    option = (row["project_code"], row["queue"], row["stage"])
    b1.button(
        "Калькулятор этого этапа",
        on_click=open_calculator,
        args=(option,),
        key="reg_open_calc",
        type="primary",
    )
    b2.button(
        "Карточка сотрудника", on_click=goto_employee, args=(row["tab_no"],), key="reg_open_card"
    )


def render_calculator(service, view, quarter, as_of, closed=None):
    st.subheader("Калькулятор по проекту и этапу")
    st.caption(
        f"Выберите один или несколько этапов — расчёт строится по реестру на квартал {quarter}. "
        "Это тот же лист «калькулятор», только собранный из реестра: ничего не нужно вести руками."
    )
    options = view.stage_options()
    if not options:
        st.info("Реестр пуст. Загрузите данные на экране «Загрузка».")
        return
    if "calc_selection" in st.session_state:
        st.session_state["calc_selection"] = [
            o for o in st.session_state["calc_selection"] if o in options
        ]
    selection = st.multiselect(
        "Проекты и этапы",
        options,
        format_func=view.stage_label,
        key="calc_selection",
        placeholder="Выберите проект и этап",
    )
    active_only = st.checkbox(
        "Только работающие на конец квартала", value=False, key="calc_active_only"
    )
    if not selection:
        return
    identifiers = view.assignments_for(selection, as_of, active_only)
    if not identifiers:
        st.warning("По выбранным этапам нет назначений с такими условиями.")
        return
    context = (quarter, service.registry.revision(), tuple(identifiers))
    cached = st.session_state.get("calc_result")
    if not cached or cached[0] != context:
        try:
            calculation = service.calculate(quarter, identifiers)
        except (ValueError, PermissionError) as error:
            st.error(str(error))
            return
        st.session_state["calc_result"] = (context, calculation)
    calculation = st.session_state["calc_result"][1]
    results = calculation.results
    labels = {o: view.stage_label(o) for o in selection}
    st.download_button(
        "Скачать калькулятор · Excel",
        calculator_workbook(results, selection, labels, quarter, preliminary=closed is None),
        file_name=f"Калькулятор_{quarter}.xlsx",
        mime=XLSX,
        on_click=service.export_event,
        args=("calculator", ",".join(f"{c}/{q}/{s}" for c, q, s in selection)),
        key="calc_download",
        type="primary",
    )
    for option in selection:
        values = [
            v
            for v in results.values()
            if (v["case"]["project_code"], v["case"]["queue"], v["case"]["stage"]) == option
        ]
        values.sort(key=lambda v: v["result"]["employees"][0]["fio"] or "")
        st.markdown(f"### {labels[option]}")
        team_total = sum(v["result"]["employees"][0]["total_payable"] or 0 for v in values)
        c1, c2, c3 = st.columns(3)
        c1.metric("Сотрудников в расчёте", len(values))
        c2.metric("ПБ команды", rub(sum(v["result"]["employees"][0]["pb"] or 0 for v in values)))
        c3.metric("Рассчитано к выплате", rub(team_total))
        for value in values:
            employee = value["result"]["employees"][0]
            header = (
                f"{employee['fio']} · {employee['category']}"
                f" · ПБ {rub(employee['pb'])} · к выплате {rub(employee['total_payable'])}"
            )
            with st.expander(header):
                k1, k2, k3, k4 = st.columns(4)
                k1.metric("Начало на этапе", employee["start"])
                k2.metric("Длительность, дн", employee["duration_days"])
                k3.metric("К фин", employee["k_fin"] if employee["k_fin"] is not None else "—")
                k4.metric("Множитель БС", employee["k_bs"] if employee["k_bs"] is not None else "—")
                person = value["case"]["employees"][0]
                loads = ", ".join(
                    f"{p['from']}…{p['to']}: {p['load']:.0%}" for p in person.get("periods", [])
                )
                st.caption(f"Загрузка: {loads or '—'} · источник: {person.get('load_source', '—')}")
                st.dataframe(
                    [
                        {
                            "Веха": m["name"],
                            "План": _date(m.get("plan")),
                            "Инд. срок": _date(m.get("individual_plan")),
                            "Факт": _date(m.get("fact")),
                            "Откл., дн": m.get("deviation_days"),
                            "Вес": m.get("weight"),
                            "Сумма, ₽": m.get("amount"),
                            "Дата выплаты": _date(m.get("pay_date")),
                            "Статус": m["calc_status"],
                        }
                        for m in employee["milestones"]
                    ]
                    + [
                        {
                            "Веха": f"🔑 {employee['final']['kind']}",
                            "План": _date(employee["final"].get("plan")),
                            "Инд. срок": _date(employee["final"].get("individual_plan")),
                            "Факт": _date(employee["final"].get("fact")),
                            "Откл., дн": employee["final"].get("deviation_days"),
                            "Вес": None,
                            "Сумма, ₽": employee["final"].get("amount"),
                            "Дата выплаты": _date(employee["final"].get("pay_date")),
                            "Статус": employee["final"]["calc_status"],
                        }
                    ]
                    + [
                        {
                            "Веха": f"Финал: часть {p['part']} (с процентами)",
                            "План": None,
                            "Инд. срок": None,
                            "Факт": _date(p.get("event_date")),
                            "Откл., дн": None,
                            "Вес": None,
                            "Сумма, ₽": p.get("amount"),
                            "Дата выплаты": _date(p.get("pay_date")),
                            "Статус": p["calc_status"],
                        }
                        for p in employee["payouts"]
                    ],
                    hide_index=True,
                    use_container_width=True,
                    column_config={
                        "Вес": st.column_config.NumberColumn(format="percent"),
                        "Сумма, ₽": st.column_config.NumberColumn(format="%.2f"),
                        **{
                            key: st.column_config.DateColumn(format="DD.MM.YYYY")
                            for key in ("План", "Инд. срок", "Факт", "Дата выплаты")
                        },
                    },
                )
