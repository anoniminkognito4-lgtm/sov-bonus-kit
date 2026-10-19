"""Ввод одного письма РФП со всеми участниками перераспределения."""

from datetime import date

import streamlit as st

from sov.adapters.registry_case import RegistryAdapter
from sov.engine.rules.kpi import quarter_end


def render_redistribution(service, quarter):
    with st.expander("Групповое перераспределение ±30%"):
        assignments = service.registry.rows("assignment")
        people = {r["tab_no"]: r for r in service.registry.rows("employee")}
        scopes = sorted(
            {
                (a["project_code"], a["queue"], a["stage"])
                for a in assignments
                if a["category"] == "РУПК"
            }
        )
        if not scopes:
            st.info("Сначала загрузите назначения РУПК.")
            return
        scope = st.selectbox(
            "Проект, очередь, этап для перераспределения",
            scopes,
            format_func=lambda s: f"{s[0]} · очередь {s[1]} · этап {s[2]}",
            key="rd_scope",
        )
        team = [
            a
            for a in assignments
            if (a["project_code"], a["queue"], a["stage"]) == scope and a["category"] == "РУПК"
        ]
        if len({a["tab_no"] for a in team}) < 2:
            st.info("В этом проекте только один РУПК: перераспределение запрещено.")
            return
        milestones = [
            m
            for m in service.registry.rows("milestone")
            if (m["project_code"], m["queue"], m["stage"]) == scope and not m["is_key"]
        ]
        if not milestones:
            return
        names = st.multiselect(
            "Вехи письма",
            [m["name"] for m in milestones],
            default=[milestones[0]["name"]],
            placeholder="Выберите вехи",
            key="rd_ms",
        )
        if not names:
            return
        context = (
            RegistryAdapter(service.registry)
            .build_case(team[0]["id"])
            .get("redistribution_context")
        )
        current = (
            {(r["assignment_id"], r["milestone"]): r["pct"] for r in context["rows"]}
            if context
            else {}
        )
        leader = st.selectbox(
            "РФП, указанный в письме",
            list(people),
            format_func=lambda k: f"{people[k]['fio']} · {k}",
            key="rd_leader",
        )
        edited = st.data_editor(
            [
                {
                    "ID": a["id"],
                    "Сотрудник": f"{a['tab_no']} · {people[a['tab_no']]['fio']}",
                    "ФП": people[a["tab_no"]]["department"],
                    "Веха": name,
                    "Изменение, %": current.get((a["id"], name), 0) * 100,
                }
                for a in team
                for name in names
            ],
            disabled=["ID", "Сотрудник", "ФП", "Веха"],
            hide_index=True,
            column_config={
                "Изменение, %": st.column_config.NumberColumn(
                    min_value=-30.0, max_value=30.0, step=1.0
                )
            },
            key=f"rd_editor_{scope}_{tuple(names)}_{service.registry.revision()}",
        )
        approved = st.date_input(
            "Дата согласования РФП", value=date.fromisoformat(quarter_end(quarter)), key="rd_date"
        )
        basis = st.text_input("Основание группового решения", key="rd_basis")
        file_ref = st.text_input("Письмо согласования / ссылка", key="rd_file")
        st.caption(
            "Проверяется денежный баланс по каждой вехе и финалу внутри ФП/проекта. "
            "Финал использует средний процент по выполненным в период работы вехам. "
            "Начисленные авансы этим решением не переписываются."
        )
        if st.button("Проверить и сохранить групповое решение", key="rd_save"):
            try:
                rows = [
                    {
                        "assignment_id": row["ID"],
                        "milestone": row["Веха"],
                        "pct": row["Изменение, %"] / 100,
                    }
                    for row in edited
                    if next(a["tab_no"] for a in team if a["id"] == row["ID"]) != leader
                    or row["Изменение, %"]
                ]
                service.save_redistribution(rows, leader, approved.isoformat(), basis, file_ref)
                st.success("Групповое решение сохранено. Пересчитайте квартал.")
            except (ValueError, PermissionError) as error:
                st.error(str(error))
