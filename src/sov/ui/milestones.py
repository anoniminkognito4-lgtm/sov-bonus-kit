"""Экраны хранилища вех: предпросмотр выгрузки 1С, дерево вех, история плана, решения."""

from datetime import date

import streamlit as st

from sov.loaders.milestones import MESSAGES, SKIP_CODES

PROJECT_COLUMNS = {
    "added": "Добавится",
    "moved": "Перенос плана",
    "closed": "Закрытие",
    "missing": "Пропала из файла",
    "blocked": "Не применится",
}


def _date(value):
    return date.fromisoformat(value) if value else None


def milestone_options():
    """Параметры загрузки вех из формы на экране «Загрузка»."""
    with st.expander("Выгрузка вех 1С: режим и дата выгрузки"):
        seed = st.checkbox(
            "Начальная версия (только для проектов, по которым вех ещё нет)", key="ms_seed"
        )
        export = st.date_input(
            "Дата выгрузки (по умолчанию — дата изменения файла)",
            value=None,
            format="DD.MM.YYYY",
            key="ms_export_date",
        )
    options = {"mode": "seed" if seed else "update"}
    if export:
        options["export_date"] = export.isoformat()
    return options


def render_milestone_preview(preview):
    stats = preview.stats
    mode = "начальная версия" if stats.get("mode") == "seed" else "обновление"
    st.write(
        f"**Выгрузка вех:** {mode} · дата выгрузки {_date(stats['export_date']):%d.%m.%Y} · "
        f"лист «{stats['sheet']}» · макет {stats['layout']}"
    )
    if len(stats.get("sheets", ())) > 1:
        st.info(
            f"Подходящих листов несколько: {', '.join(stats['sheets'])}. Взят «{stats['sheet']}»."
        )
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Проектов", stats["projects"])
    c2.metric("Очередей", stats["queues"])
    c3.metric("Этапов", stats["stages"])
    c4.metric("Вех", stats["milestones"])
    c5.metric("Не применится строк", preview.summary["blocked"])
    by_project = stats.get("by_project") or {}
    if by_project:
        st.write("Сводка по проектам")
        st.dataframe(
            [
                {"Проект": code, **{label: values[key] for key, label in PROJECT_COLUMNS.items()}}
                for code, values in sorted(by_project.items())
            ],
            hide_index=True,
            use_container_width=True,
        )
    only_key = st.checkbox("Только ключевые вехи", key="ms_only_key")
    key_codes = {
        c["data"]["milestone_code"]
        for c in preview.changes
        if c["table"] == "milestone" and c["data"].get("is_key")
    }
    problems = [
        p
        for p in preview.problems
        if not only_key or "KEY" in p["code"] or p.get("milestone_code") in key_codes
    ]
    if problems:
        st.write("Проблемы и вопросы")
        st.dataframe(
            [
                {
                    "Код": p["code"],
                    "Описание": MESSAGES[p["code"]],
                    "Не применится": "да" if p["code"] in SKIP_CODES else "",
                    "Строка": p.get("source_ref") or "",
                    "Проект": p.get("project_code") or p.get("project") or "",
                    "Веха": p.get("milestone") or "",
                    "Сдвиг плана, дней": p.get("shift_days"),
                    "Назначений": p.get("assignments"),
                    "Было → стало": (
                        f"{p['before']} → {p['after']}" if p.get("before") or p.get("after") else ""
                    ),
                    "Предложение": p.get("proposal") or "",
                }
                for p in problems
            ],
            hide_index=True,
            use_container_width=True,
        )


def _latest_versions(registry):
    result = {}
    for row in registry.rows("milestone_plan_version"):
        result.setdefault(row["milestone_code"], []).append(row)
    for rows in result.values():
        rows.sort(key=lambda r: r["version"])
    return result


def render_project_milestones(service, registry, project_code):
    """Дерево «очередь → этап → вехи» и история плановых дат (MILESTONES §5, §9)."""
    milestones = [m for m in registry.rows("milestone") if m["project_code"] == project_code]
    st.markdown("#### Вехи проекта в хранилище")
    if not milestones:
        st.info("Вех этого проекта в хранилище нет. Загрузите выгрузку вех 1С.")
        return
    versions = _latest_versions(registry)
    groups = {}
    for m in milestones:
        groups.setdefault(m["queue"], {}).setdefault(m["stage"], []).append(m)
    for queue in sorted(groups):
        label = f"Очередь {queue}" if queue else "Вехи проекта без очереди"
        with st.expander(label, expanded=bool(queue) and len(groups) <= 2):
            for stage in sorted(groups[queue]):
                rows = groups[queue][stage]
                st.write(f"**Этап {stage}**")
                rows.sort(
                    key=lambda m: (
                        m["is_key"],
                        (versions.get(m["milestone_code"]) or [{}])[-1].get("plan_date") or "",
                    )
                )
                st.dataframe(
                    [
                        {
                            "Веха": ("🔑 " if m["is_key"] else "") + m["name"],
                            "План": _date(
                                (versions.get(m["milestone_code"]) or [{}])[-1].get("plan_date")
                            ),
                            "Версий плана": len(versions.get(m["milestone_code"], [])),
                            "Факт": _date(m["fact_date"]),
                            "Статус 1С": m["status"],
                            "Код": m["milestone_code"],
                        }
                        for m in rows
                    ],
                    hide_index=True,
                    use_container_width=True,
                    column_config={
                        k: st.column_config.DateColumn(format="DD.MM.YYYY")
                        for k in ("План", "Факт")
                    },
                )
    labels = {
        m["milestone_code"]: f"{m['name']} · очередь {m['queue'] or '—'} · этап {m['stage']}"
        for m in milestones
    }
    files = {r["id"]: r["file_name"] for r in service.uploads()}
    # Сначала вехи с переносами плана — их историю смотрят чаще.
    order = sorted(labels, key=lambda c: (-len(versions.get(c, [])), labels[c]))
    code = st.selectbox("История плановой даты", order, format_func=labels.get, key="ms_history")
    st.dataframe(
        [
            {
                "Версия": v["version"],
                "Плановая дата": _date(v["plan_date"]),
                "Действует с (дата выгрузки)": _date(v["effective_date"]),
                "Основание": v["basis"],
                "Файл": files.get(v["source_file_id"], v["source_file_id"]),
            }
            for v in versions.get(code, [])
        ],
        hide_index=True,
        use_container_width=True,
        column_config={
            k: st.column_config.DateColumn(format="DD.MM.YYYY")
            for k in ("Плановая дата", "Действует с (дата выгрузки)")
        },
    )
    with st.expander("Решения по вехам"):
        render_decisions(service, labels, versions)


def render_decisions(service, labels, versions):
    kinds = {
        "status_alias": "Что значит статус 1С",
        "milestone_inactive": "Веха снята",
        "milestone_rename": "Переименование вехи",
        "fact_change_accept": "Принять изменение факта",
        "plan_move_basis": "Основание переноса плана",
    }
    kind = st.selectbox("Решение", list(kinds), format_func=kinds.get, key="ms_decision_kind")
    payload = {}
    if kind == "status_alias":
        payload["raw"] = st.text_input("Значение статуса в 1С", key="ms_raw_status")
        meanings = {
            "inactive": "веха снята",
            "assigned": "как «Назначена»",
            "done": "как «Выполнена»",
        }
        payload["status"] = st.selectbox(
            "Смысл", list(meanings), format_func=meanings.get, key="ms_status_meaning"
        )
    else:
        payload["milestone_code"] = st.selectbox(
            "Веха", sorted(labels), format_func=labels.get, key="ms_decision_code"
        )
    if kind == "milestone_rename":
        payload["new_name"] = st.text_input("Новое наименование из выгрузки", key="ms_new_name")
    if kind == "fact_change_accept":
        fact = st.date_input(
            "Новый факт (пусто — факт снят)", value=None, format="DD.MM.YYYY", key="ms_new_fact"
        )
        payload["fact"] = fact.isoformat() if fact else None
    if kind == "plan_move_basis":
        numbers = [v["version"] for v in versions.get(payload["milestone_code"], [])]
        payload["version"] = st.selectbox("Версия плана", numbers or [1], key="ms_version")
        payload["basis"] = st.text_input("Основание переноса (протокол, письмо)", key="ms_basis")
    basis = st.text_input("Основание решения", key="ms_decision_basis")
    file_ref = st.text_input("Ссылка на файл", key="ms_decision_file")
    if st.button("Сохранить решение", key="ms_save_decision"):
        try:
            if kind == "status_alias":
                service.save_status_alias(payload["raw"], payload["status"], basis, file_ref)
            else:
                service.save_milestone_decision(kind, payload, basis, file_ref)
            st.success("Решение сохранено. Оно действует на следующие загрузки и расчёты.")
        except (ValueError, PermissionError) as error:
            st.error(str(error))
