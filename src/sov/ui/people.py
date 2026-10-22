"""Вопросы «тот же человек или другой» и справочник людей (D-101)."""

import streamlit as st

from sov.loaders.persons import QUESTIONS
from sov.loaders.upshr import MESSAGES as UPSHR_MESSAGES


def render_person_questions(service, preview, refresh):
    """Вопросы о людях из предпросмотра: ответ — решение в журнале, потом новый предпросмотр."""
    questions = [p for p in preview.problems if p["code"] in QUESTIONS]
    if not questions:
        return
    st.warning(
        f"Вопросы о людях: {len(questions)}. Строки с вопросами не загрузятся, "
        "пока вы не ответите; после ответа сделайте предпросмотр ещё раз."
    )
    seen = set()
    for index, question in enumerate(questions):
        marker = (question["fio"], question.get("department"), question.get("position"))
        if marker in seen:
            continue
        seen.add(marker)
        with st.container(border=True):
            st.write(
                f"**{question['fio']}** · {question.get('department') or '—'} · "
                f"{question.get('position') or '—'} (строка {question['line']})"
            )
            st.caption(question["message"])
            candidates = {c["person_id"]: c for c in question["candidates"]}
            chosen = st.radio(
                "Это тот же человек, что",
                list(candidates),
                format_func=lambda p: (
                    f"{candidates[p]['fio']} · {candidates[p]['department'] or '—'} · "
                    f"{candidates[p]['position'] or '—'} · {p}"
                ),
                key=f"person_q_{index}",
            )
            basis = st.text_input("Основание", key=f"person_basis_{index}")
            left, right = st.columns(2)
            if left.button("Тот же человек", key=f"person_merge_{index}"):
                try:
                    service.person_merge(question, chosen, basis)
                    refresh("Записано: тот же человек. Сделайте предпросмотр ещё раз.")
                except (ValueError, PermissionError) as error:
                    st.error(str(error))
            if right.button("Другой человек", key=f"person_split_{index}"):
                try:
                    report = service.person_split(question, basis)
                    refresh(
                        f"Записано: другой человек, номер {report['person_id']}. "
                        "Сделайте предпросмотр ещё раз."
                    )
                except (ValueError, PermissionError) as error:
                    st.error(str(error))


def render_people(registry):
    st.subheader("Люди")
    st.caption(
        "Табельного номера нет: сотрудник — это ФИО и внутренний номер, который присваивает "
        "система. Сравнение ФИО — без лишних пробелов, ё = е, без учёта регистра. "
        "Решения «тот же человек» и «другой человек» откатываются на экране «Загрузка»."
    )
    st.dataframe(
        [
            {
                "№ сотрудника": e["person_id"],
                "ФИО": e["fio"],
                "Подразделение": e["department"],
                "Должность": e["position"],
                "Уволен": e["dismissal_date"],
            }
            for e in sorted(registry.rows("employee"), key=lambda e: e["fio"] or "")
        ],
        hide_index=True,
        use_container_width=True,
    )
    aliases = registry.rows("person_alias")
    if aliases:
        st.markdown("#### Решения о людях")
        st.dataframe(
            [
                {
                    "ФИО (как в файле)": a["fio"],
                    "Подразделение": a["department"] or "любое",
                    "№ сотрудника": a["person_id"],
                    "Решение": "тот же человек" if a["kind"] == "merge" else "другой человек",
                    "Основание": a["basis"],
                }
                for a in aliases
            ],
            hide_index=True,
            use_container_width=True,
        )


def render_upshr_preview(preview):
    """Сводка и вопросы выгрузки УП ШР «Проектные команды» (D-103)."""
    stats = preview.stats
    st.caption(
        f"Строк: {stats.get('rows', 0)} · назначений: {stats.get('assignments', 0)} · "
        f"новых людей: {stats.get('new_people', 0)}. Загрузка из файла не берётся: только "
        "по правилам СОВ. «Дата окончания» — плановый срок этапа, не дата ухода."
    )
    problems = [p for p in preview.problems if p["code"] not in QUESTIONS]
    if problems:
        st.write("Проверки и вопросы")
        st.dataframe(
            [
                {
                    "Строка": p.get("line"),
                    "Вопрос": UPSHR_MESSAGES.get(p["code"], p.get("message")),
                    "Блокирует расчёт": "да" if p["blocking"] else "",
                    "ФИО": p.get("fio") or "",
                    "Было (реестр)": p.get("before") or "",
                    "В файле": p.get("after") or "",
                }
                for p in problems
            ],
            hide_index=True,
            use_container_width=True,
        )
