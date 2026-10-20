# Форматы данных

Все примеры и образцы — условные данные. Даты везде ISO `ГГГГ-ММ-ДД`, кроме файлов, которые повторяют формат 1С (там `ДД.ММ.ГГГГ`). Доли — числом (0,67 → `0.67`), суммы — рубли без округления.

## 1. Кейс: вход движка в тестовом режиме

`tests/cases/<ID>.json`. Один кейс = один проект, одна очередь, один этап и 1–2 сотрудника (столько блоков в шаблоне). Генерируется `tools/make_cases.py`.

| Поле | Тип | Что |
| --- | --- | --- |
| `id`, `title`, `check` | строка | код, название, что проверяем (человеческим языком, с ожидаемыми числами) |
| `harness` | bool | `true` — эталон считается в шаблоне; `false` — кейс только для приложения (ожидания в `tests/autocontrol`) |
| `project` | строка | название условного проекта |
| `stage` | 1–5 | этап |
| `continues_from_prev_stage` | bool | переход из предыдущего этапа (F1) |
| `new_stage_start` | дата или null | дата начала нового этапа (F2) |
| `bs` | объект | `enabled` — проект БС; `share_transferred` — доля переданных к перевыставлению затрат |
| `milestones[]` | список | промежуточные вехи по порядку плана: `name`, `building`, `plan`, `fact` (null — не закрыта), необязательно `closed_1c` (по умолчанию = `fact`; `null` при наличии факта — «не закрыта в 1С») |
| `final` | объект | ключевая веха: `name`, `plan`, `fact` |
| `key_rate` | число | ставка ЦБ по умолчанию для всех вех |
| `key_rates` | объект | `{веха: ставка}` — средневзвешенная ставка по конкретной вехе (как вводит Группа СОВ в шаблоне) |
| `stage5_expense` | объект | 5 этап, РП и РПС: `remaining` (осталась расходная часть), `plan_budget`, `fact_budget` |
| `employees[]` | список | сотрудники, поля ниже |

Сотрудник:

| Поле | Тип | Что |
| --- | --- | --- |
| `tab_no` | строка | условный табельный номер `T0001`… — ключ сотрудника |
| `fio` | строка | условное ФИО «Тестов …» |
| `category` | строка | `КУПК (РП/ЗРП)`, `КУПК`, `РУПК`, `МП` |
| `role` | строка | `РП`, `ЗРП`, `РПС`, `другая` |
| `position` | строка | должность |
| `start` | дата | дата начала работы на этапе |
| `periods[]` | список | `from`, `to`, `bgb`, `load` — отрезки с постоянными БГБ и загрузкой, до планового окончания этапа |
| `npv_start`, `npv_current` | число | КУПК: стартовый NPV; текущий NPV (один на все кварталы до завершения этапа) |
| `npv_checkpoints` | объект | КУПК: `final` (NPV при завершении этапа), `q1` (через квартал), `q2` (через полгода) |
| `individual_dates` | объект | `{веха или "final": дата}` — индивидуальные сроки |
| `kpi` | объект | МП: `{веха: KPI за квартал вехи}`; `null` — KPI нет |
| `leave_date` | дата | дата ухода с проекта (этапа) |
| `npv_at_leave`, `npv_stage_end` | число | КУПК, уход: NPV на дату ухода и после завершения этапа (для К фин остатка) |
| `hr_dismissal_date` | дата | уволен в кадрах (для автоконтроля `DISMISSED_ASSIGNMENT_OPEN`) |

## 2. Результат: `sov-result/1`

Движок отдаёт по кейсу JSON этой структуры; эталоны `tests/golden/{spec,template}/<ID>.json` — в ней же. `tools/compare_golden.py` сравнивает только поля эталона, лишние поля можно добавлять.

```jsonc
{
  "schema": "sov-result/1", "id": "T13", "variant": "spec",
  "blocking_problems": [{"code": "STAGE_EARLY_OVER_100", "tab_no": "T0039"}],   // сравниваются как множество
  "employees": [{
    "tab_no": "T0024", "fio": "…", "category": "КУПК", "role": "другая",
    "start": "2025-04-01", "leave_date": null,
    "duration_days": 548, "pb": 3603287.67, "advance_share": 0.67,
    "advance_pool": 2414202.74, "final_pool": 1189084.93,
    "k_fin": 1.0,            // null не у КУПК; К фин авансов
    "k_bs": 1.0,
    "milestones": [{
      "name": "Мотивационная веха 1", "plan": "2025-06-30", "individual_plan": null, "fact": "2025-06-30",
      "deviation_days": 0,     // plan_eff − fact
      "weight": 0.1667,        // null, если не участвует
      "amount": 402367.12,     // null, если не считалась
      "pay_date": "2025-08-08",
      "calc_status": "рассчитано"
    }],
    "final": {
      "plan": "2026-09-30", "individual_plan": null, "fact": "2026-09-10", "deviation_days": 20,
      "kind": "финальный бонус",   // или "остаток при уходе"
      "k_stage": 1.2, "amount": 1909742.47, "raw_amount": 1909742.47,   // raw_amount — до обнуления
      "pay_date": "2026-11-10", "calc_status": "рассчитано"
    },
    "leave": null,   // или {"date", "worked_days", "k_stage", "k_fin", "remainder"}
    "interest": {
      "rows": [{
        "milestone": "Мотивационная веха 1", "advance": 402367.12, "pay_date": "2025-08-08",
        "deferred": 198180.82, "rate": 0.16,
        "parts": [{"part": "50%", "days": 459, "interest": 19937.53}, {"part": "25% (1)", …}, {"part": "25% (2)", …}]
      }],
      "total": 155026.27
    },
    "payouts": [{
      "part": "50%",           // "100%" | "50%" | "25% (1)" | "25% (2)" | "50% (2)"
      "event_date": "2026-09-10", "pay_date": "2026-11-10",
      "bonus": 954871.23, "interest": 59725.73, "amount": 1014596.96,
      "calc_status": "рассчитано",
      "npv_checkpoint": 5000000000,   // только КУПК 1–4 этапов
      "delayed": true,                // необязательно: часть 25% (1), выплаченная через полгода
      "k_expense": 0.8                // необязательно: 5 этап, РП/РПС
    }],
    "total_payable": 4478971.47
  }]
}
```

Кроме этого движок отдаёт на уровне кейса `problems: [{code, tab_no, milestone, blocking, message}]` — все найденные проблемы (`docs/CALC_RULES.md` §12.2); `blocking_problems` — их блокирующая часть в виде `{code, tab_no}`. В эталонах `problems` нет (их проверяют кейсы `tests/autocontrol`), а `blocking_problems` есть и сравнивается как множество. Поле со значением `null` в эталоне можно не выдавать.

Значения `calc_status` — `docs/CALC_RULES.md` §12.1. Вариант `template` устроен так же, но статусы S1–S3 и правила O1–O3 в нём не применяются.

## 3. Файлы-источники для реестра

Образцы — `samples/`, собраны из кейсов `tools/make_samples.py`. CSV: UTF-8 с BOM, разделитель `;`, первая строка — заголовок. Каждый кейс — условный проект `TP-<ID>` (или `TP-<ID>-1`, `-2`, если у сотрудников кейса разный NPV); `samples/case_map.csv` связывает кейс, сотрудника и проект.

| Файл | Источник (ТЗ, раздел 5) | Колонки |
| --- | --- | --- |
| `hr_employees.csv` | 1С IHR | tab_no, fio, position, department, hire_date, dismissal_date, probation_end, probation_result |
| `hr_bgb_history.csv` | 1С IHR | tab_no, bgb, date_from, date_to |
| `projects.csv` | 1С | project_code, name, queue, blocks, is_exception, sale_date, handover_to_completion_date |
| `stages.csv` | 1С | project_code, queue, stage, start_date, key_milestone, is_completion |
| `assignments.csv` | 1С УП ШР | tab_no, project_code, queue, stage, sov_type, category, role, position_on_project, date_from, date_to, end_reason, load, from_prev_stage |
| `milestones_1c.xlsx` | 1С «Мотивационные вехи» | лист «Лист для загрузки вех»: Статус, Наименование, Шаблон, Ключевая, Вид, Проект, Очередь/корпус/строение, Этап, Заказчик, Исполнитель, Плановый срок, Критерий закрытия, Фактический срок + Код проекта, Код вехи, Дата закрытия в 1С |
| `individual_dates.csv` | 1С «Мотивационные вехи» | tab_no, project_code, stage, milestone, individual_plan, basis |
| `npv_quarterly.csv` | 1С «Мотивационный NPV» | project_code, quarter, fm_version, npv_plan_paur, npv_forecast_paur, fub_approved_date |
| `start_npv.csv` | расчёт + Группа СОВ | tab_no, project_code, stage, npv_start, fixed_date, basis |
| `bs_transfer.csv` | отчёт ПТО БС | project_code, quarter, share_transferred |
| `kpi_mp.csv` | 1С, задачи KPI | tab_no, quarter, kpi |
| `stage5_expense.csv` | ФМ, Группа СОВ | project_code, queue, stage, expense_remaining, plan_budget, fact_budget |
| `key_rates.csv` | сайт ЦБ (здесь условно 16%) | date_from, rate |
| `holidays.csv` | производственный календарь | date, kind (пустой образец) |
| `redistribution.csv` | решение РФП, вносит Группа СОВ | tab_no, project_code, stage, milestone, pct, decided_by, approved_date, basis |
| `rp_project_sets.csv` | форма фиксации набора проектов РП | tab_no, rank, project_code, queue, class, area_k_m2, stage, extra_objects, is_queue, is_logistics, approved_date, approved_by |

Замечания:

- В `milestones_1c.xlsx` даты в формате 1С `ДД.ММ.ГГГГ`, этап словами («Четвертый этап»), статусы «Назначена» / «Выполнена» (точные значения статусов 1С уточнить). Ключевая веха — строка с «Ключевая = Да». Три последние колонки в листе шаблона отсутствуют: если в реальной выгрузке их нет, загрузчик сопоставляет по (Проект, Очередь, Этап, Наименование) и помечает это предупреждением.
- В `assignments.csv` строка = период с постоянной загрузкой; `date_to` у ушедшего — дата ухода. ПБ при этом считается до планового окончания этапа (`CALC_RULES` §2).
- В `npv_quarterly.csv` квартал вида `2026-Q3`. NPV на дату ухода = NPV квартала ухода; NPV после этапа = NPV квартала завершения.
- `holidays.csv` и `redistribution.csv` в образце пустые — только заголовки.

Порядок загрузки любого файла — ТЗ, раздел 5: распознать тип по заголовкам → проверить структуру → сопоставить по ключам → предпросмотр (добавится / изменится / не распознано) → «Применить» с записью в журнал → повторная загрузка того же файла ничего не меняет → откат до закрытия квартала.

## 4. Реестр: таблицы (SQLite в прототипе)

Минимум для прототипа; полный список — ТЗ, раздел 4. Все таблицы с историей хранят `valid_from`, `valid_to`, `source_file_id`, `created_at`.

| Таблица | Ключ | Основные поля |
| --- | --- | --- |
| employee | tab_no | fio, position, department, hire_date, dismissal_date, probation_end, probation_result |
| bgb_period | tab_no, date_from | bgb, date_to |
| project | project_code, queue | name, blocks, is_exception, sale_date, handover_date |
| stage | project_code, queue, stage | start_date, key_milestone_code, is_completion |
| milestone | milestone_code | project_code, queue (`""` — веха проекта без очереди), stage, name, is_key, status (значение 1С), fact_date, closed_1c_date, queue_raw, name_raw, template, kind, criterion, extra_json, first_file_hash |
| milestone_plan_version | milestone_code, version | plan_date, basis, effective_date (дата выгрузки 1С), file_hash |
| milestone_key | natural_key | milestone_code — `[проект, очередь 1С, этап, наименование]` → код вехи (MILESTONES §3.2) |
| milestone_raw_row | id | upload_id, row_no, raw_json — строка выгрузки как есть; не предметная таблица, в снимок квартала не входит, удаляется откатом |
| assignment | id | tab_no, project_code, queue, stage, sov_type, category, role, position, date_from, date_to, end_reason, from_prev_stage |
| assignment_load | assignment_id, date_from | load, date_to |
| individual_date | tab_no, milestone_code | individual_plan, basis |
| npv | project_code, quarter, fm_version | npv_plan_paur, npv_forecast_paur, fub_approved_date |
| start_npv | assignment_id | npv_start, fixed_date, basis, corrections |
| bs_share | project_code, quarter | share_transferred |
| kpi | tab_no, quarter | kpi |
| stage5_expense | project_code, queue, stage | expense_remaining, plan_budget, fact_budget |
| key_rate | date_from | rate |
| holiday | date | kind |
| redistribution | tab_no, milestone_code | pct, decided_by, approved_date, basis |
| rp_project_set | tab_no, project_code, queue | rank, class, area_k_m2, stage, extra_objects, is_queue, is_logistics, approved_date |
| sov_param | edition, name | value, valid_from |
| upload | id | file_name, file_hash, type, author, uploaded_at, applied_at, rolled_back_at |
| accrual | id | snapshot_id, tab_no, assignment_id, kind, milestone_code, part, quarter, pay_date, amount, calc_status, edition |
| snapshot | id | quarter, closed_at, closed_by, edition |
| manual_decision | id | kind, tab_no, payload_json, basis, file_ref, author, created_at |
| audit_log | id | who, when, entity, entity_id, before_json, after_json, basis |

## 5. Файл к начислению

Одна строка — одно начисление; xlsx и csv (UTF-8, `;`). Образец — `samples/payroll_export_sample.csv` (по эталонам T05 и T14). Колонки: Табельный номер, ФИО, Подразделение, Проект и очередь, Этап, Вид начисления (аванс за веху / финальный бонус / финал 50% / финал 25% (1) / финал 25% (2) / остаток при уходе / проценты на отложенную часть), Веха, Квартал начисления, Дата выплаты, Сумма, ₽ (до копеек), ID расчёта (снимок квартала и редакция правил). Проценты — отдельной строкой рядом со своей частью.

Формат рабочий: его поменяют под требования расчёта зарплаты, поэтому выгрузка строится из таблицы `accrual` отдельным модулем с описанием колонок в конфиге.

## 6. Выписка по проекту и этапу

Excel в виде листа «калькулятор» (ТЗ 9.3): шапка проекта, блок на сотрудника, таблица вех с датами, отклонением, весом, суммой, статусом и датой выплаты, ключевая веха, части 50/25/25, проценты. Только значения, без формул и макросов. Пример внешнего вида — `reference/examples/T13_spec.xlsx`.

## 7. Дополнения M6

- Источник параметров распознаётся по заголовкам `edition;name;value`.
  Для каждой редакции обязательна строка `effective_from` со значением `ГГГГ-QN`;
  остальные имена — поля `SovParams`, значения — JSON (числа и массивы).
  Пример и ограничения: [M6.md](M6.md). Снимок содержит полный набор `params`.
- Поля ИС `probation_end`, `probation_result` поступают из HR; допустимы
  `проходит`, `пройден`, `не пройден`. Дата выплаты после ИС — ручное решение.
- Продажа и передача поступают из `projects.csv`: `sale_date` — первый платёж,
  `handover_to_completion_date` — передача. Дату окончания работы и согласование
  дополнительного срока задаёт ручное решение по назначению.
- Перевод РУПК → КУПК: две строки назначений; старое с `end_reason=перевод`
  заканчивается накануне нового. Новое получает отдельный ID. В `start_npv.csv`
  для нового КУПК обязательна `fixed_date` конца месяца перевода.
- В `redistribution.csv` значение `pct` — доля (например, `0.3`), не число 30.
  Все участники письма должны сохранять фонд в рублях; проверка проводится до
  применения. `decided_by` — табельный номер согласующего РФП.
- `payouts[].slot` — порядковый номер части с нуля, сохраняемый в снимке.
  `frozen=true` отмечает ранее начисленное; сумма и даты восстанавливаются
  из исходного снимка. Дополнительные поля не меняют контракт `sov-result/1`.
- Корректировка создаёт отдельное начисление с `correction_snapshot_id` и
  `correction_decision_id`. Исходные записи и файлы остаются неизменными.
