"""Экран «Новый калькулятор» (NEW_CALC §6) и подсказки «Обзора» (§3)."""

from datetime import date
from pathlib import Path

import streamlit as st

from sov.exports.calc_file import MESSAGES as FILE_MESSAGES
from sov.services.new_calc import BLOCKS, project_block

FIRST_ROW = 16


def _date(value):
    return date.fromisoformat(value) if value else None


def open_new_calc(project, queue, stage):
    st.session_state["nc_project"] = project
    st.session_state["nc_queue"] = queue
    st.session_state["nc_stage"] = stage
    st.session_state["page"] = "Новый калькулятор"


def render_hints(service, as_of):
    hints = service.new_calc_hints(as_of)
    if not hints:
        return
    st.markdown("#### Калькуляторы: что пора сделать")
    for index, hint in enumerate(hints):
        if hint["code"] == "NEWCALC_STAGE_DUE":
            text = (
                f"{hint['project_code']} · очередь {hint['queue']} · этап {hint['stage']}: "
                f"{hint['message'].lower()}"
                + (
                    f" (ключевая веха текущего этапа {_date(hint['key_date']):%d.%m.%Y})"
                    if hint.get("key_date")
                    else ""
                )
            )
            left, right = st.columns([4, 1])
            left.info(text)
            right.button(
                "Создать",
                key=f"nc_hint_{index}",
                on_click=open_new_calc,
                args=(hint["project_code"], hint["queue"], hint["stage"]),
            )
        else:
            st.warning(
                f"Калькулятор {hint['calc']}: {hint['message'].lower()}. "
                f"Добавить: {', '.join(hint['add']) or '—'}; "
                f"убрать: {', '.join(hint['remove']) or '—'}."
            )


def render_new_calc(service, registry, root):
    st.subheader("Новый калькулятор")
    st.caption(
        "Калькулятор — это этап очереди: набор вех из хранилища и команда. "
        "Вехи отбираются по правилам макроса «Вставить вехи в новый калькулятор»."
    )
    projects = registry.rows("project")
    if not projects:
        st.info("Справочник проектов пуст. Загрузите проекты на экране «Загрузка».")
        return
    codes = sorted({p["project_code"] for p in projects})
    names = {p["project_code"]: p["name"] for p in projects}
    c1, c2, c3 = st.columns([3, 1, 1])
    if st.session_state.get("nc_project") not in codes:
        st.session_state["nc_project"] = codes[0]
    project = c1.selectbox(
        "Проект", codes, format_func=lambda c: f"{names[c]} · {c}", key="nc_project"
    )
    queues = sorted({p["queue"] for p in projects if p["project_code"] == project})
    if st.session_state.get("nc_queue") not in queues:
        st.session_state["nc_queue"] = queues[0]
    queue = c2.selectbox("Очередь", queues, key="nc_queue")
    stage = c3.number_input("Этап", min_value=1, max_value=5, step=1, key="nc_stage")
    draft = service.new_calc_draft(project, queue, int(stage))
    selection, header, store = draft["selection"], draft["header"], draft["store"]
    exists = any(p["code"] == "NEWCALC_EXISTS" for p in selection.problems)

    # 1. Шапка.
    h1, h2, h3 = st.columns(3)
    continues = h1.checkbox(
        "Переход из предыдущего этапа (F1)",
        value=header["continues_from_prev"],
        key=f"nc_f1_{project}_{queue}_{stage}",
    )
    start = h2.date_input(
        "Старт этапа (F2)",
        value=_date(header["start_date"]),
        format="DD.MM.YYYY",
        key=f"nc_f2_{project}_{queue}_{stage}",
    )
    completion = h3.checkbox("Дострой", key=f"nc_completion_{project}_{queue}_{stage}")
    project_row = next(p for p in projects if p["project_code"] == project and p["queue"] == queue)
    bs = "БС" in (project_row["blocks"] or "").split()
    st.caption(f"Отношение к БС (F3): {'да' if bs else 'нет'} — по признаку проекта.")
    if header["start_date"]:
        st.caption("Старт по умолчанию — день после плановой даты ключевой вехи предыдущего этапа.")

    for problem in selection.problems:
        (st.error if problem["blocking"] else st.warning)(
            f"{problem['code']}: {problem['message']}"
            + (f" — {problem['milestone']}" if problem.get("milestone") else "")
        )

    # 2. Вехи в виде строк калькулятора.
    st.markdown("#### Вехи")
    rows = [
        {
            "Строка": FIRST_ROW + index,
            "Этап": int(stage),
            "Наименование": m["name"],
            "Очередь / корпус": m.get("queue_raw") or "без очереди",
            "План": _date(store.plan(m["milestone_code"])),
            "Факт": _date(m["fact_date"]),
            "Статус 1С": m["status"],
            "Источник": "очередь" if m["queue"] else "проект",
        }
        for index, m in enumerate(selection.milestones)
    ]
    if selection.key:
        key = selection.key
        rows.append(
            {
                "Строка": "ключевая",
                "Этап": int(stage),
                "Наименование": "🔑 " + key["name"],
                "Очередь / корпус": key.get("queue_raw") or "",
                "План": _date(store.plan(key["milestone_code"])),
                "Факт": _date(key["fact_date"]),
                "Статус 1С": key["status"],
                "Источник": "очередь",
            }
        )
    st.dataframe(
        rows,
        hide_index=True,
        use_container_width=True,
        column_config={
            k: st.column_config.DateColumn(format="DD.MM.YYYY") for k in ("План", "Факт")
        },
    )
    if selection.excluded:
        with st.expander(f"Не вошли: {len(selection.excluded)}"):
            st.dataframe(
                [
                    {"Веха": m["name"], "Очередь": m["queue"] or "без очереди", "Причина": reason}
                    for m, reason in selection.excluded
                ],
                hide_index=True,
                use_container_width=True,
            )

    if not exists:
        # 3. Команда.
        st.markdown("#### Команда")
        team = [{"Взять": True, **person} for person in draft["team"]]
        employees = {e["tab_no"]: e for e in registry.rows("employee")}
        extra = st.multiselect(
            "Добавить вручную",
            sorted(employees),
            format_func=lambda t: f"{employees[t]['fio']} · {t}",
            key="nc_manual",
            placeholder="Выберите сотрудников",
        )
        for tab in extra:
            team.append(
                {
                    "Взять": True,
                    "tab_no": tab,
                    "fio": employees[tab]["fio"],
                    "category": "РУПК",
                    "role": "другая",
                    "position": employees[tab]["position"],
                    "sov_type": "Вехи",
                    "date_from": start.isoformat() if start else None,
                    "individual": False,
                    "source": "вручную",
                }
            )
        if not team:
            st.caption(
                f"С этапа {int(stage) - 1} переходить некому: добавьте сотрудников вручную "
                "или создайте калькулятор без команды."
            )
        edited = st.data_editor(
            team,
            hide_index=True,
            use_container_width=True,
            disabled=["tab_no", "fio", "source", "individual"],
            column_config={
                "tab_no": "Таб. №",
                "fio": "ФИО",
                "category": st.column_config.SelectboxColumn(
                    "Категория", options=["КУПК (РП/ЗРП)", "КУПК", "РУПК", "МП"]
                ),
                "role": "Роль",
                "position": "Должность",
                "sov_type": "Вид СОВ",
                "date_from": "Начало на этапе",
                "individual": "Инд. срок N−1",
                "source": "Откуда",
            },
            key="nc_team",
        )
        st.caption(
            "Загрузка не вносится: ДРП, БС и БПСК — по таблице должностей, МП — по правилу, "
            "РП и ЗРП — нормированием. Для КУПК стартовый NPV фиксируется на старт этапа."
        )
        if st.button("Создать", key="nc_create", type="primary", disabled=bool(selection.blocking)):
            try:
                chosen = [
                    {k: v for k, v in row.items() if k != "Взять"} for row in edited if row["Взять"]
                ]
                result = service.create_calculator(
                    project,
                    queue,
                    int(stage),
                    {
                        "start_date": start.isoformat() if start else None,
                        "continues_from_prev": continues,
                        "is_completion": completion,
                    },
                    chosen,
                )
                st.success(
                    f"Калькулятор создан: записей {result['changed']}. "
                    "Отменить можно на экране «Загрузка» («Откатить загрузку»)."
                )
            except (ValueError, PermissionError) as error:
                st.error(str(error))

    # 4. Файл для параллельного расчёта.
    st.markdown("#### Файл для параллельного расчёта")
    settings = service.calc_file_settings()
    with st.expander("Настройка: шаблоны по блокам, папка, имя файла"):
        templates = {
            block: st.text_input(
                f"Шаблон {block}", settings["templates"].get(block, ""), key=f"nc_tpl_{block}"
            )
            for block in BLOCKS
        }
        folder = st.text_input("Папка для новых файлов", settings["folder"], key="nc_folder")
        name = st.text_input("Шаблон имени", settings["name"], key="nc_name")
        if st.button("Сохранить настройки", key="nc_save_settings"):
            service.save_calc_file_settings(
                {"templates": templates, "folder": folder, "name": name}
            )
            st.success("Настройки сохранены.")
    block = project_block(project_row["blocks"])
    template = Path(settings["templates"].get(block) or settings["templates"]["БПП"])
    template = template if template.is_absolute() else root / template
    target = Path(settings["folder"])
    target = target if target.is_absolute() else root / target
    st.caption(f"Блок проекта: {block}. Шаблон: {template}. Исходный шаблон не меняется.")
    if st.button("Создать файл калькулятора", key="nc_file", disabled=selection.key is None):
        try:
            report = service.export_calc_file(
                project,
                queue,
                int(stage),
                template,
                target,
                settings["name"],
                {"F1": continues, **({"F2": start.isoformat()} if start else {})},
            )
            (st.success if report["status"] == "создан" else st.error)(
                f"Файл {report['status']}: {report['file']}. Вписано строк: {report['written']}."
            )
            for problem in report["problems"]:
                st.warning(f"{problem['code']}: {FILE_MESSAGES[problem['code']]}")
            if report["overflow"]:
                st.write("Не поместились:", ", ".join(report["overflow"]))
            if report["check"]:
                st.write("Самопроверка: какую дату найдёт Excel")
                st.dataframe(
                    [
                        {
                            "Строка": c["row"],
                            "Веха": c["milestone"],
                            "Дата": c["date"] or "не найдёт",
                        }
                        for c in report["check"]
                    ],
                    hide_index=True,
                    use_container_width=True,
                )
        except (ValueError, OSError, KeyError) as error:
            st.error(str(error))
