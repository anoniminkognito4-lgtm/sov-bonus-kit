"""Экраны: справочник должностей, флажок ЛЦ/СКБ и «Добавить / убрать с проекта»."""

from datetime import date

import streamlit as st

from sov.services.team import CATEGORIES, END_REASONS, SOV_TYPES

NEW = "— новый сотрудник —"


def _date(value):
    return date.fromisoformat(value) if value else None


def render_positions(service, registry):
    st.subheader("Справочник должностей")
    st.caption(
        "По справочнику загрузка находит строку таблицы (сл. 18), а пустые категория и вид СОВ "
        "назначения заполняются. Перед сравнением: пробелы и переносы схлопываются, «и.о.» "
        "снимается, опечатки исправляются по таблице ниже, хвост с названием проекта "
        "отрезается. Не распознано — вопрос, система не угадывает."
    )
    questions = service.position_questions()
    if questions:
        st.warning(f"Не распознано должностей: {len(questions)}. Добавьте их или исправление.")
        st.dataframe(
            [
                {"Блок": q["block"], "Как написано": q["position"], "Сотрудники": q["staff"]}
                for q in questions
            ],
            hide_index=True,
            use_container_width=True,
        )
    else:
        st.success("Все должности блоков ДРП, БПСК и БС распознаны.")
    rows = [
        {k: r[k] for k in ("block", "position", "category", "sov_type", "load_row", "comment")}
        for r in sorted(registry.rows("position_ref"), key=lambda r: (r["block"], r["position"]))
    ]
    edited = st.data_editor(
        rows,
        num_rows="dynamic",
        hide_index=True,
        use_container_width=True,
        column_config={
            "block": "Подразделение",
            "position": "Должность",
            "category": st.column_config.SelectboxColumn("Категория", options=list(CATEGORIES)),
            "sov_type": st.column_config.SelectboxColumn("Вид СОВ", options=list(SOV_TYPES)),
            "load_row": "Загрузка (строка сл. 18 или правило)",
            "comment": "Источник и комментарий",
        },
        key="positions_editor",
    )
    basis = st.text_input("Основание правки", key="positions_basis")
    if st.button("Сохранить справочник", key="positions_save"):
        try:
            report = service.save_positions(edited, basis)
            st.success(f"Сохранено записей: {report['changed']}. Откат — на экране «Загрузка».")
        except (ValueError, PermissionError) as error:
            st.error(str(error))

    st.markdown("#### Исправления в названиях")
    fixes = [
        {"wrong": r["wrong"], "right": r["right"]}
        for r in sorted(registry.rows("position_fix"), key=lambda r: r["wrong"])
    ]
    edited_fixes = st.data_editor(
        fixes,
        num_rows="dynamic",
        hide_index=True,
        use_container_width=True,
        column_config={"wrong": "Как написано", "right": "Как правильно"},
        key="fixes_editor",
    )
    fixes_basis = st.text_input("Основание правки", key="fixes_basis")
    if st.button("Сохранить исправления", key="fixes_save"):
        try:
            report = service.save_position_fixes(edited_fixes, fixes_basis)
            st.success(f"Сохранено записей: {report['changed']}.")
        except (ValueError, PermissionError) as error:
            st.error(str(error))


def render_lc_skb(service, project, as_of):
    code, queue = project["project_code"], project["queue"]
    state, row = service.lc_skb(code, queue, as_of)
    st.markdown("#### ЛЦ или объект СКБ")
    st.caption(
        "Если отмечено, загрузка БПСК по этой очереди берётся из столбца «ЛЦ, объекты СКБ» "
        "таблицы должностей. Отметка ставится и снимается с даты, через журнал."
    )
    if row:
        st.write(
            f"Сейчас: {'да' if state else 'нет'} — с {_date(row['date_from']):%d.%m.%Y} "
            f"({row['basis']})."
        )
    with st.form(f"lc_skb_{code}_{queue}"):
        flag = st.checkbox("ЛЦ или объект СКБ", value=state)
        since = st.date_input("С даты", value=date.fromisoformat(as_of), format="DD.MM.YYYY")
        basis = st.text_input("Основание")
        if st.form_submit_button("Записать"):
            try:
                service.set_lc_skb(code, queue, flag, since.isoformat(), basis)
                st.success("Записано в журнал. Пересчитайте квартал.")
            except (ValueError, PermissionError) as error:
                st.error(str(error))


def render_team_forms(service, registry, project):
    code, queue = project["project_code"], project["queue"]
    stages = sorted(
        s["stage"]
        for s in registry.rows("stage")
        if s["project_code"] == code and s["queue"] == queue
    )
    employees = {e["person_id"]: e for e in registry.rows("employee")}
    left, right = st.columns(2)
    with left.expander("➕ Добавить в команду"):
        if not stages:
            st.info("У очереди нет этапов в реестре.")
        else:
            choice = st.selectbox(
                "Сотрудник",
                [NEW, *sorted(employees)],
                format_func=lambda t: t if t == NEW else f"{employees[t]['fio']} · {t}",
                key=f"team_tab_{code}_{queue}",
            )
            new = {}
            if choice == NEW:
                c1, c2 = st.columns(2)
                person_id = None  # номер сотрудника присвоит система (D-101)
                new["fio"] = c1.text_input("ФИО", key=f"team_fio_{code}_{queue}")
                new["department"] = c2.text_input("Подразделение", key=f"team_dep_{code}_{queue}")
                new["hire_date"] = None
                department = new["department"]
                current = ""
            else:
                person_id = choice
                department = employees[choice]["department"]
                current = employees[choice]["position"] or ""
            position = st.text_input(
                "Должность", value=current, key=f"team_pos_{code}_{queue}_{choice}"
            )
            new["position"] = position
            hint = service.team_defaults(department, position) if position else {}
            if position and hint.get("block") and not hint.get("recognized"):
                st.warning("Должность не распознана справочником: укажите категорию и вид СОВ.")
            c1, c2, c3 = st.columns(3)
            stage = c1.selectbox("Этап", stages, key=f"team_stage_{code}_{queue}")
            since = c2.date_input(
                "Дата прихода на проект", format="DD.MM.YYYY", key=f"team_from_{code}_{queue}"
            )
            role = c3.text_input("Роль", value="другая", key=f"team_role_{code}_{queue}")
            options = ["по справочнику", *CATEGORIES]
            category = c1.selectbox("Категория", options, key=f"team_cat_{code}_{queue}")
            sov_type = c2.selectbox(
                "Вид СОВ", ["по справочнику", *SOV_TYPES], key=f"team_sov_{code}_{queue}"
            )
            load = c3.number_input(
                "Загрузка, % (если не по правилу)",
                min_value=0.0,
                max_value=100.0,
                value=0.0,
                key=f"team_load_{code}_{queue}",
            )
            if hint.get("category"):
                st.caption(f"По справочнику: {hint['category']}, {hint['sov_type']}.")
            basis = st.text_input("Основание", key=f"team_basis_{code}_{queue}")
            if st.button("Добавить", key=f"team_add_{code}_{queue}", type="primary"):
                try:
                    report = service.add_to_team(
                        {
                            "person_id": person_id,
                            "new_employee": new if choice == NEW else None,
                            "project_code": code,
                            "queue": queue,
                            "stage": stage,
                            "position": position,
                            "date_from": since.isoformat(),
                            "category": None if category == options[0] else category,
                            "sov_type": None if sov_type == "по справочнику" else sov_type,
                            "role": role,
                            "load": load / 100 if load else None,
                            "basis": basis,
                        }
                    )
                    st.success(
                        f"Добавлено, записей: {report['changed']}. "
                        "Отменить — «Откатить загрузку» на экране «Загрузка»."
                    )
                except (ValueError, PermissionError) as error:
                    st.error(str(error))

    active = [
        a
        for a in registry.rows("assignment")
        if a["project_code"] == code and a["queue"] == queue and not a["end_reason"]
    ]
    with right.expander("➖ Убрать с проекта"):
        if not active:
            st.info("В команде нет действующих назначений.")
            return
        names = {
            a[
                "id"
            ]: f"{employees.get(a['person_id'], {}).get('fio', a['person_id'])} · этап {a['stage']}"
            for a in active
        }
        identifier = st.selectbox(
            "Назначение", list(names), format_func=names.get, key=f"team_rm_{code}_{queue}"
        )
        c1, c2 = st.columns(2)
        until = c1.date_input(
            "Последний день на проекте", format="DD.MM.YYYY", key=f"team_to_{code}_{queue}"
        )
        reason = c2.selectbox("Причина", END_REASONS, key=f"team_reason_{code}_{queue}")
        basis = st.text_input("Основание", key=f"team_rm_basis_{code}_{queue}")
        st.caption("Назначение не удаляется: закрывается датой, история и расчёт сохраняются.")
        if st.button("Убрать", key=f"team_remove_{code}_{queue}"):
            try:
                service.remove_from_team(identifier, until.isoformat(), reason, basis)
                st.success("Записано в журнал. Пересчитайте квартал.")
            except (ValueError, PermissionError) as error:
                st.error(str(error))
