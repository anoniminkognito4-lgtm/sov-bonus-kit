"""Локальная панель Группы СОВ. Все данные — условные."""

import json
import os
from datetime import date
from pathlib import Path
from uuid import uuid4

import streamlit as st

from sov.adapters.registry_case import quarter_add
from sov.exports.files import payroll, problems_file, statement, summary_file
from sov.loaders.batch import SAMPLE_ORDER
from sov.registry.session import Registry
from sov.services.panel import ADMIN, Actor, PanelService

ROOT = Path(__file__).resolve().parents[1]
st.set_page_config(page_title="СОВ · Расчёт бонусов", page_icon="◈", layout="wide")
st.markdown(
    """<style>
[data-testid="stAppViewContainer"] {background: #f5f7f5;}
h1,h2,h3 {color: #173f45;}
[data-testid="stMetric"] {background:white;padding:16px;border-radius:12px;}
</style>""",
    unsafe_allow_html=True,
)
st.title("СОВ · Расчёт бонусов")
st.caption("Локальный прототип · только условные данные · вход по ролям — демонстрационный")


@st.cache_resource
def database(path):
    return Registry(path)


role = st.sidebar.selectbox("Роль", [ADMIN, "Руководитель", "Сотрудник"], key="role")
author = st.sidebar.text_input("Пользователь", "Тестовый методолог", key="author")
if role != ADMIN:
    st.info("Панель расчёта доступна Группе СОВ. Кабинеты остальных ролей будут подключены позже.")
    st.stop()
registry = database(os.environ.get("SOV_DB_PATH", str(ROOT / "build/sov.sqlite")))
service = PanelService(registry, Actor(author, role))
quarters = [f"{year}-Q{number}" for year in range(2025, 2029) for number in range(1, 5)]
selected_quarter = st.sidebar.selectbox("Квартал", quarters, index=6, key="quarter")
page = st.sidebar.radio(
    "Раздел",
    [
        "Обзор",
        "Сотрудники",
        "Проекты",
        "Загрузка",
        "Проблемы",
        "Ручные решения",
        "Расчёт и закрытие",
        "Журнал",
    ],
    key="page",
)
st.sidebar.caption("Данные хранятся локально. Расчёт работает без сети.")
if "flash" in st.session_state:
    st.success(st.session_state.pop("flash"))


def refresh(message):
    if old := st.session_state.pop("calculation", None):
        st.session_state["previous_calculation"] = old
    st.session_state["flash"] = message
    st.rerun()


def goto_employee(tab_no):
    st.session_state["page"] = "Сотрудники"
    st.session_state["focus_tab"] = tab_no


def run_calculation(ids=None):
    try:
        previous = st.session_state.get("calculation") or st.session_state.get(
            "previous_calculation"
        )
        calculation = service.calculate(selected_quarter, ids, previous=previous)
        st.session_state["calculation"] = calculation
        st.success(f"Рассчитано назначений: {len(calculation.assignment_ids)}")
        return calculation
    except (ValueError, PermissionError) as error:
        st.error(str(error))
        return None


def current_calculation():
    calculation = st.session_state.get("calculation")
    if calculation and (
        calculation.quarter != selected_quarter or calculation.revision != registry.revision()
    ):
        return None
    return calculation


def download(label, data, file_name, mime, kind, identifier):
    st.download_button(
        label,
        data,
        file_name=file_name,
        mime=mime,
        on_click=service.export_event,
        args=(kind, identifier),
    )


def display_exports(entries, results, identifier, closed=None):
    st.subheader("Выгрузки")
    if closed:
        download(
            "Файл к начислению · Excel",
            payroll(closed),
            f"Начисления_{selected_quarter}.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "payroll_xlsx",
            identifier,
        )
        download(
            "Файл к начислению · CSV",
            payroll(closed, "csv"),
            f"Начисления_{selected_quarter}.csv",
            "text/csv",
            "payroll_csv",
            identifier,
        )
    else:
        st.caption("Файл к начислению станет доступен после закрытия квартала.")
    previous = service.closed(quarter_add(selected_quarter, -1))
    previous_entries = previous["payload"].get("entries", []) if previous else []
    download(
        "Сводная с предыдущим кварталом",
        summary_file(entries, previous_entries),
        f"Сводная_{selected_quarter}.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "summary",
        identifier,
    )
    options = sorted({(v["case"]["project_code"], v["case"]["stage"]) for v in results.values()})
    if options:
        project, stage = st.selectbox(
            "Выписка по проекту и этапу",
            options,
            format_func=lambda v: f"{v[0]} · этап {v[1]}",
            key="export_project",
        )
        download(
            "Выписка · Excel",
            statement(results, project, stage, preliminary=closed is None),
            f"Выписка_{project}_{stage}.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "statement",
            identifier,
        )


LABELS = {
    "tab_no": "Табельный номер",
    "fio": "ФИО",
    "department": "Подразделение",
    "project_code": "Код проекта",
    "project": "Проект",
    "name": "Наименование",
    "queue": "Очередь",
    "stage": "Этап",
    "category": "Категория",
    "role": "Роль",
    "position": "Должность",
    "sov_type": "Вид СОВ",
    "date_from": "Дата начала",
    "date_to": "Дата окончания",
    "from_prev_stage": "Из предыдущего этапа",
    "end_reason": "Причина ухода",
    "plan": "План",
    "individual_plan": "Индивидуальный срок",
    "fact": "Факт",
    "deviation_days": "Отклонение, дней",
    "weight": "Вес",
    "amount": "Сумма, ₽",
    "raw_amount": "Финал до обнуления, ₽",
    "pay_date": "Дата выплаты",
    "calc_status": "Статус",
    "part": "Часть",
    "kind": "Вид начисления / решения",
    "k_stage": "К срока этапа",
    "bonus": "Бонус, ₽",
    "interest": "Проценты, ₽",
    "event_date": "Дата события",
    "closed_1c": "Закрытие в 1С",
    "closed_1c_date": "Закрытие в 1С",
    "npv_checkpoint": "Контрольный NPV",
    "delayed": "Выплата перенесена",
    "k_expense": "К расходов",
    "milestone": "Веха",
    "milestone_code": "Код вехи",
    "status": "Статус",
    "fact_date": "Факт",
    "is_key": "Ключевая веха",
    "start_date": "Начало этапа",
    "is_completion": "Дострой",
    "key_milestone_name": "Ключевая веха",
    "quarter": "Квартал",
    "fm_version": "Версия ФМ",
    "npv_plan_paur": "NPV с плановыми ПАУР",
    "npv_forecast_paur": "NPV с прогнозными ПАУР",
    "fub_approved_date": "Утверждение ФЮБ",
    "edition": "Редакция СОВ",
    "bgb": "БГБ, ₽",
    "from": "С",
    "to": "По",
    "load": "Загрузка, %",
    "basis": "Основание",
    "file_ref": "Файл",
    "author": "Автор",
    "id": "ID",
    "snapshot_id": "ID расчёта",
    "assignment_id": "ID назначения",
    "code": "Код проблемы",
    "blocking": "Блокирует",
    "message": "Описание",
    "line": "Строка",
    "row": "Данные строки",
    "who": "Автор",
    "when": "Дата",
    "entity": "Объект",
    "entity_id": "ID объекта",
    "before_json": "Было",
    "after_json": "Стало",
    "added": "Добавится",
    "changed": "Изменится",
    "unchanged": "Без изменений",
    "unmatched": "Не сопоставлено",
    "errors": "Ошибки",
}
HIDDEN = {"row_key", "valid_from", "valid_to", "source_file_id", "created_at", "payload_json"}


TYPE_LABELS = {
    "employee": "Сотрудники",
    "bgb_period": "История БГБ",
    "project": "Проекты",
    "stage": "Этапы",
    "assignment": "Назначения",
    "assignment_load": "Загрузка",
    "milestone": "Вехи",
    "milestone_plan_version": "Плановые сроки",
    "individual_date": "Индивидуальные сроки",
    "npv": "NPV по кварталам",
    "start_npv": "Стартовый NPV",
    "bs_share": "Перевыставление БС",
    "kpi": "KPI МП",
    "stage5_expense": "Расходная часть",
    "key_rate": "Ключевая ставка",
    "holiday": "Календарь",
    "redistribution": "Перераспределение",
    "rp_project_set": "Нормирование РП",
    "early_stage": "Решение по опережению",
    "outside_session": "Выплата вне сессии",
    "snapshot": "Закрытие квартала",
    "accrual": "Начисления",
    "upload": "Загрузка",
    "manual_decision": "Ручное решение",
    "view": "Просмотр",
    "export": "Выгрузка",
}


def translated(row):
    values = {}
    for key, value in row.items():
        if key in HIDDEN:
            continue
        if key in ("entity", "kind", "Источник", "Таблица") and isinstance(value, str):
            value = TYPE_LABELS.get(value, value)
        if key == "load" and isinstance(value, (int, float)):
            value = value * 100
        values[LABELS.get(key, key)] = value
    return values


def table(rows, **kwargs):
    if isinstance(rows, (list, tuple)):
        rows = [translated(row) if isinstance(row, dict) else row for row in rows]
    st.dataframe(rows, **kwargs)


employees = registry.rows("employee")
assignments = registry.rows("assignment")
projects = registry.rows("project")
calculation = current_calculation()
closed = service.closed(selected_quarter)

if page == "Обзор":
    st.subheader(f"Квартал {selected_quarter}")
    a, b, c, d = st.columns(4)
    a.metric("Сотрудники", len(employees))
    b.metric("Проекты и очереди", len(projects))
    c.metric("Назначения", len(assignments))
    d.metric("Квартал", "Закрыт" if closed else "Открыт")
    if not assignments:
        st.info(
            "Начните с раздела «Загрузка»: загрузите условные образцы или свои условные выгрузки."
        )
    elif calculation:
        status_totals = {}
        for row in calculation.entries:
            status_totals[row["calc_status"]] = (
                status_totals.get(row["calc_status"], 0) + row["amount"]
            )
        table([{"Статус": k, "Сумма, ₽": v} for k, v in status_totals.items()], hide_index=True)
        st.warning(
            f"Блокирующих проблем: {len(calculation.blocking)}"
        ) if calculation.blocking else st.success("Блокирующих проблем нет")
    else:
        st.info(
            "Рассчитайте квартал в разделе «Расчёт и закрытие», чтобы увидеть суммы и проблемы."
        )
    uploads = service.uploads()
    st.subheader("Источники")
    table(
        [
            {
                "Файл": r["file_name"],
                "Источник": r["type"],
                "Загружен": r["applied_at"],
                "Автор": r["author"],
                "Откат": bool(r["rolled_back_at"]),
            }
            for r in uploads
        ],
        hide_index=True,
    )

elif page == "Загрузка":
    st.subheader("Загрузка и предпросмотр")
    st.caption("Только условные данные. Файлы с макросами не поддерживаются.")
    if st.button("Загрузить все условные образцы", key="load_demo"):
        try:
            reports = service.demo(ROOT / "samples")
            unmatched = sum(r["summary"]["unmatched"] for r in reports)
            refresh(
                f"Образцы обработаны. Несопоставленных строк: {unmatched} (видны в предпросмотре)."
            )
        except ValueError as error:
            st.error(str(error))
    source_name = st.selectbox("Условный образец", SAMPLE_ORDER, key="sample_file")
    uploaded = st.file_uploader("Или выберите условный файл", type=["csv", "xlsx"])
    if st.button("Предпросмотр", key="preview_upload"):
        try:
            source = ROOT / "samples" / source_name
            if uploaded is not None:
                folder = ROOT / "build/imports" / str(uuid4())
                folder.mkdir(parents=True, exist_ok=True)
                source = folder / Path(uploaded.name).name
                source.write_bytes(uploaded.getvalue())
            st.session_state["upload_preview"] = service.preview(source)
        except (ValueError, OSError) as error:
            st.error(str(error))
    preview = st.session_state.get("upload_preview")
    if preview:
        st.write(
            f"**Файл:** {preview.path.name} · **Тип:** "
            f"{TYPE_LABELS.get(preview.kind, preview.kind)}"
        )
        table([preview.summary], hide_index=True)
        if preview.changes:
            table(
                [
                    {
                        "Таблица": r["table"],
                        "Ключ": r["key"],
                        "Действие": "Добавится" if r["before"] is None else "Изменится",
                        "Было": json.dumps(translated(r["before"] or {}), ensure_ascii=False),
                        "Станет": json.dumps(translated(r["data"]), ensure_ascii=False),
                    }
                    for r in preview.changes
                ],
                hide_index=True,
            )
        for label, rows in (
            ("Не сопоставлено", preview.unmatched),
            ("Ошибки", preview.errors),
            ("Предупреждения", preview.warnings),
        ):
            if rows:
                st.write(label)
                table(list(rows), hide_index=True)
        if st.button("Применить", key="apply_upload", disabled=bool(preview.errors)):
            try:
                report = service.apply(preview)
                st.session_state.pop("upload_preview", None)
                refresh(f"Загрузка применена. Изменено записей: {report['changed']}.")
            except (ValueError, PermissionError) as error:
                st.error(str(error))
    active_uploads = [r for r in service.uploads() if not r["rolled_back_at"]]
    if active_uploads:
        choices = {r["id"]: f"{r['file_name']} · {r['applied_at']}" for r in active_uploads}
        identifier = st.selectbox(
            "Загрузка для отката", list(choices), format_func=choices.get, key="rollback_id"
        )
        if st.button("Откатить загрузку", key="rollback_upload"):
            try:
                service.rollback(identifier)
                refresh("Загрузка откачена. История сохранена.")
            except ValueError as error:
                st.error(str(error))

elif page == "Сотрудники":
    st.subheader("Сотрудники")
    if not employees:
        st.info("Сначала загрузите справочник сотрудников.")
    else:
        f1, f2, f3 = st.columns(3)
        block = f1.selectbox(
            "Блок", ["Все", *sorted({p["blocks"] for p in projects if p["blocks"]})]
        )
        category = f2.selectbox(
            "Категория", ["Все", *sorted({a["category"] for a in assignments if a["category"]})]
        )
        state = f3.selectbox("Работает / ушёл", ["Все", "Работает", "Ушёл"])
        statuses = (
            ["Все", *sorted({r["calc_status"] for r in calculation.entries})]
            if calculation
            else ["Все"]
        )
        amount_status = st.selectbox("Статус суммы", statuses, disabled=not calculation)
        project_filter = st.selectbox(
            "Проект", ["Все", *sorted({p["project_code"] for p in projects})]
        )
        matching = [
            a
            for a in assignments
            if (category == "Все" or a["category"] == category)
            and (project_filter == "Все" or a["project_code"] == project_filter)
            and (state == "Все" or bool(a["end_reason"]) == (state == "Ушёл"))
            and (
                block == "Все"
                or any(
                    p["blocks"] == block and p["project_code"] == a["project_code"]
                    for p in projects
                )
            )
        ]
        if amount_status != "Все" and calculation:
            allowed = {
                r["assignment_id"] for r in calculation.entries if r["calc_status"] == amount_status
            }
            matching = [a for a in matching if a["id"] in allowed]
        tabs = {a["tab_no"] for a in matching}
        people = {e["tab_no"]: e for e in employees if e["tab_no"] in tabs}
        table(
            [
                {"Табельный номер": e["tab_no"], "ФИО": e["fio"], "Подразделение": e["department"]}
                for e in people.values()
            ],
            hide_index=True,
        )
        if people:
            focus = st.session_state.pop("focus_tab", None)
            if focus in people:
                st.session_state["person_tab"] = focus
            tab_no = st.selectbox(
                "Карточка сотрудника",
                list(people),
                format_func=lambda key: f"{people[key]['fio']} · {key}",
                key="person_tab",
            )
            service.view("employee", tab_no)
            person_assignments = [a for a in matching if a["tab_no"] == tab_no]
            identifier = st.selectbox(
                "Назначение",
                [a["id"] for a in person_assignments],
                format_func=lambda key: next(
                    f"{a['project_code']} · этап {a['stage']}"
                    for a in person_assignments
                    if a["id"] == key
                ),
                key="person_assignment",
            )
            try:
                value = service.calculate(selected_quarter, [identifier]).results[identifier]
                employee = value["result"]["employees"][0]
                st.caption(
                    "Расчёт предварительный. Суммы к начислению фиксируются при закрытии квартала."
                )
                m1, m2, m3 = st.columns(3)
                m1.metric(
                    "ПБ, ₽",
                    f"{employee['pb']:,.2f}" if employee["pb"] is not None else "Нет данных",
                )
                m2.metric("К фин", employee["k_fin"] if employee["k_fin"] is not None else "—")
                m3.metric("Множитель БС", employee["k_bs"] if employee["k_bs"] is not None else "—")
                table(employee["milestones"], hide_index=True)
                st.write("Финал")
                table([employee["final"]], hide_index=True)
                table(employee["payouts"], hide_index=True)
                st.write("Проценты, ₽", employee["interest"]["total"])
                with st.expander("История БГБ и загрузки"):
                    table(value["case"]["employees"][0]["periods"], hide_index=True)
                with st.expander("История начислений и решений"):
                    table(
                        [r for r in registry.rows("accrual") if r["tab_no"] == tab_no],
                        hide_index=True,
                    )
                    table(
                        [r for r in service.decisions() if r["tab_no"] == tab_no], hide_index=True
                    )
            except ValueError as error:
                st.error(str(error))

elif page == "Проекты":
    st.subheader("Проекты и этапы")
    if projects:
        labels = {p["row_key"]: f"{p['name']} · очередь {p['queue']}" for p in projects}
        key = st.selectbox("Проект и очередь", list(labels), format_func=labels.get)
        project = next(p for p in projects if p["row_key"] == key)
        st.write("Блоки", project["blocks"])
        table(
            [r for r in registry.rows("stage") if r["project_code"] == project["project_code"]],
            hide_index=True,
        )
        st.write("Вехи")
        latest_plans = {}
        for plan in registry.rows("milestone_plan_version"):
            code = plan["milestone_code"]
            if code not in latest_plans or plan["version"] > latest_plans[code]["version"]:
                latest_plans[code] = plan
        table(
            [
                {**r, "plan": latest_plans.get(r["milestone_code"], {}).get("plan_date")}
                for r in registry.rows("milestone")
                if r["project_code"] == project["project_code"]
            ],
            hide_index=True,
        )
        st.write("NPV по кварталам")
        table(
            [r for r in registry.rows("npv") if r["project_code"] == project["project_code"]],
            hide_index=True,
        )
        st.write("Команда")
        table(
            [r for r in assignments if r["project_code"] == project["project_code"]],
            hide_index=True,
        )
    else:
        st.info("Справочник проектов пока пуст.")

elif page == "Проблемы":
    st.subheader("Проблемы расчёта")
    if st.button("Обновить проблемы", key="refresh_problems"):
        calculation = run_calculation()
    if calculation:
        mode = st.selectbox("Показать", ["Все", "Блокирующие", "Предупреждения"])
        problems = [
            p
            for p in calculation.problems
            if mode == "Все" or p["blocking"] == (mode == "Блокирующие")
        ]
        table(problems, hide_index=True)
        tabs = sorted({p["tab_no"] for p in problems if p.get("tab_no")})
        if tabs:
            tab_no = st.selectbox("Сотрудник с проблемой", tabs)
            st.button("Перейти в карточку", on_click=goto_employee, args=(tab_no,))
        download(
            "Список проблем · CSV",
            problems_file(problems),
            f"Проблемы_{selected_quarter}.csv",
            "text/csv",
            "problems",
            selected_quarter,
        )
        manual_problems = [p for p in calculation.problems if p["code"] == "MANUAL_CALC"]
        if manual_problems:
            download(
                "Сотрудники с ручным расчётом · CSV",
                problems_file(manual_problems),
                f"Ручной_расчёт_{selected_quarter}.csv",
                "text/csv",
                "manual_staff",
                selected_quarter,
            )
    else:
        st.info("Нажмите «Обновить проблемы» после загрузки данных.")

elif page == "Ручные решения":
    st.subheader("Ручные решения Группы СОВ")
    labels = {
        a["id"]: f"{a['tab_no']} · {a['project_code']} · этап {a['stage']}" for a in assignments
    }
    if not labels:
        st.info("Сначала загрузите назначения.")
    else:
        kinds = {
            "Решение по опережению > 100 дней": "early_stage",
            "Корректировка стартового NPV": "start_npv",
            "Расходная часть пятого этапа": "stage5_expense",
            "Выплата вне сессии": "outside_session",
            "Перераспределение ±30%": "redistribution",
        }
        identifier = st.selectbox(
            "Назначение для решения",
            list(labels),
            format_func=labels.get,
            key="decision_assignment",
        )
        kind_label = st.selectbox("Вид решения", list(kinds), key="decision_kind")
        kind = kinds[kind_label]
        payload = {}
        if kind == "early_stage":
            payload["approved"] = st.checkbox(
                "Решение по опережению подтверждено", key="approve_early"
            )
        elif kind == "start_npv":
            payload["value"] = st.number_input("Стартовый NPV, ₽", value=0.0, step=1_000_000.0)
        elif kind == "stage5_expense":
            payload["plan_budget"] = st.number_input(
                "Плановый бюджет, ₽", min_value=0.0, value=100_000_000.0
            )
            payload["fact_budget"] = st.number_input(
                "Фактический бюджет, ₽", min_value=0.01, value=100_000_000.0
            )
        elif kind == "outside_session":
            from sov.adapters.registry_case import RegistryAdapter

            names = [
                m["name"] for m in RegistryAdapter(registry).build_case(identifier)["milestones"]
            ]
            payload["milestone"] = st.selectbox("Веха вне сессии", names)
            payload["pay_date"] = st.date_input(
                "Дата выплаты", value=date(2026, 11, 10)
            ).isoformat()
        else:
            payload["pct"] = (
                st.number_input("Перераспределение, %", min_value=-30.0, max_value=30.0) / 100
            )
            st.info(
                "Решение сохранится для ручного расчёта. До его обработки начисление блокируется."
            )
        basis = st.text_area("Основание решения", key="decision_basis")
        file_ref = st.text_input("Ссылка / имя подтверждающего файла", key="decision_file")
        if st.button("Сохранить решение", key="save_decision"):
            try:
                service.save_decision(kind, identifier, payload, basis, file_ref)
                refresh("Решение сохранено в журнале. Пересчитайте квартал.")
            except (ValueError, KeyError) as error:
                st.error(str(error))
        table(service.decisions(), hide_index=True)

elif page == "Расчёт и закрытие":
    st.subheader(f"Расчёт и закрытие · {selected_quarter}")
    if closed:
        st.success(f"Квартал закрыт · {closed['closed_by']} · {closed['closed_at']}")
        if "entries" in closed["payload"]:
            table(
                [
                    {
                        **r,
                        "calc_status": "начислено"
                        if r["calc_status"] == "рассчитано"
                        else r["calc_status"],
                    }
                    for r in closed["payload"]["entries"]
                ],
                hide_index=True,
            )
            display_exports(
                closed["payload"]["entries"], closed["payload"]["results"], closed["id"], closed
            )
        else:
            st.info("Сохранён снимок исходных данных без начислений.")
    else:
        ids = st.multiselect(
            "Выбранные назначения (пусто — все)",
            [a["id"] for a in assignments],
            format_func=lambda key: next(
                f"{a['tab_no']} · {a['project_code']}" for a in assignments if a["id"] == key
            ),
        )
        if st.button("Рассчитать", key="calculate", disabled=not assignments):
            calculation = run_calculation(ids or None)
        if calculation:
            st.caption(
                "Расчёт предварительный. Прогнозы и блокирующие проблемы "
                "не допускаются к начислению."
            )
            table(list(calculation.entries), hide_index=True)
            if calculation.blocking:
                st.error(f"Закрытие запрещено: блокирующих проблем {len(calculation.blocking)}.")
                table(calculation.blocking, hide_index=True)
            partial = set(calculation.assignment_ids) != {a["id"] for a in assignments}
            if partial:
                st.info("Для закрытия квартала рассчитайте все назначения.")
            if st.button(
                "Закрыть квартал",
                key="close_quarter",
                disabled=bool(calculation.blocking) or partial or not calculation.entries,
            ):
                try:
                    service.close(calculation)
                    refresh("Квартал закрыт. Начисления зафиксированы.")
                except ValueError as error:
                    st.error(str(error))
            display_exports(calculation.entries, calculation.results, selected_quarter)
        else:
            st.info("Загрузите данные и нажмите «Рассчитать».")

elif page == "Журнал":
    st.subheader("Журнал действий")
    who = st.text_input("Фильтр по автору")
    tab_no = st.text_input("Табельный номер / код проекта")
    journal = [
        r
        for r in service.journal()
        if (not who or who in r["who"]) and (not tab_no or tab_no in str(r))
    ]
    table(journal, hide_index=True)
