"""Образцы файлов-источников для загрузчиков реестра (samples/), собранные из условных кейсов tests/cases.

Каждый кейс становится отдельным условным проектом TP-<ID>. Если у сотрудников одного кейса разный текущий NPV
или разные контрольные NPV (в реальном реестре NPV один на проект), кейс делится на проекты TP-<ID>-1, TP-<ID>-2.
samples/case_map.csv говорит, какой сотрудник какого кейса в каком проекте, чтобы собрать результат расчёта
по реестру обратно в формат кейса и сравнить с эталоном (tools/compare_golden.py).

Форматы колонок — docs/DATA_FORMATS.md, раздел 3. Все значения условные.
Запуск из корня комплекта: python3 tools/make_samples.py
"""
import csv
import datetime as dt
import glob
import json
import os

import openpyxl

KIT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
OUT = os.path.join(KIT, 'samples')
os.makedirs(OUT, exist_ok=True)
STAGE_WORD = {1: 'Первый этап', 2: 'Второй этап', 3: 'Третий этап', 4: 'Четвертый этап', 5: 'Пятый этап'}
MS_HEADER = ['Статус', 'Наименование', 'Шаблон', 'Ключевая', 'Вид', 'Проект', 'Очередь/корпус/строение', 'Этап',
             'Заказчик', 'Исполнитель', 'Плановый срок', 'Критерий закрытия', 'Фактический срок',
             # колонок ниже в листе шаблона нет; нужны для сопоставления по кодам (ТЗ, раздел 4)
             'Код проекта', 'Код вехи', 'Дата закрытия в 1С']
# Условная история ключевой ставки. НЕ реальные ставки ЦБ: постоянные 16%, как в большинстве кейсов.
KEY_RATES = [('2024-01-01', 0.16)]


def D(s):
    return dt.date.fromisoformat(s)


def ru(s):
    return D(s).strftime('%d.%m.%Y') if s else ''


def quarter(s):
    d = D(s)
    return '%d-Q%d' % (d.year, (d.month - 1) // 3 + 1)


def q_add(qs, n):
    y, q = int(qs[:4]), int(qs[-1])
    k = y * 4 + (q - 1) + n
    return '%d-Q%d' % (k // 4, k % 4 + 1)


def q_range(a, b):
    out, q = [], a
    while q <= b:
        out.append(q)
        q = q_add(q, 1)
    return out


def write_csv(name, header, rows):
    with open(os.path.join(OUT, name), 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.writer(f, delimiter=';')
        w.writerow(header)
        w.writerows(rows)


def npv_key(e):
    return (e.get('npv_current'), json.dumps(e.get('npv_checkpoints'), sort_keys=True))


cases = [json.load(open(p, encoding='utf-8')) for p in sorted(glob.glob(os.path.join(KIT, 'tests', 'cases', '*.json')))]
R = {k: [] for k in ('emp', 'bgb', 'proj', 'stage', 'asg', 'ind', 'npv', 'snpv', 'bs', 'kpi', 'map', 'ms', 'exp')}
seen_emp = set()
for c in cases:
    kupk = [e for e in c['employees'] if e.get('npv_current') is not None]
    split = len({npv_key(e) for e in kupk}) > 1
    groups = [[e] for e in c['employees']] if split else [c['employees']]
    for gi, emps in enumerate(groups, 1):
        code = 'TP-%s' % c['id'] + ('-%d' % gi if split else '')
        name = '%s %s' % (c['project'], code[3:])
        bs = c.get('bs') or {}
        R['proj'].append([code, name, '1', 'БС ОУС' if bs.get('enabled') else 'БПП ОУП', 'Нет', '', ''])
        start = c.get('new_stage_start') or min(e['start'] for e in c['employees'])
        R['stage'].append([code, '1', c['stage'], start, c['final']['name'], 'Нет'])
        if c.get('stage5_expense'):
            x = c['stage5_expense']
            R['exp'].append([code, '1', c['stage'], 'Да' if x['remaining'] else 'Нет', x['plan_budget'], x['fact_budget']])
        for i, m in enumerate(c['milestones'] + [dict(c['final'], key=True)]):
            closed = m.get('closed_1c', m.get('fact'))
            R['ms'].append(['Выполнена' if closed else 'Назначена', m['name'], m['name'], 'Да' if m.get('key') else 'Нет',
                            'Основная', name, '1', STAGE_WORD[c['stage']], '', '', ru(m['plan']), '',
                            ru(m.get('fact')), code, '%s-M%02d' % (code, i + 1), ru(closed)])
        f_q = quarter(c['final']['fact'] or c['final']['plan'])
        all_q = q_range(quarter(start), q_add(f_q, 2))
        for e in emps:
            R['map'].append([c['id'], e['tab_no'], code])
            if e['tab_no'] not in seen_emp:
                seen_emp.add(e['tab_no'])
                R['emp'].append([e['tab_no'], e['fio'], e.get('position', ''), 'Условное подразделение',
                                 '2020-01-01', e.get('hr_dismissal_date') or '', '', ''])
                for k, p in enumerate(e['periods']):   # последний период БГБ открыт, как в кадровой истории
                    R['bgb'].append([e['tab_no'], p['bgb'], p['from'], p['to'] if k < len(e['periods']) - 1 else ''])
            for p in e['periods']:
                R['asg'].append([e['tab_no'], code, '1', c['stage'],
                                 'Вехи от KPI' if e['category'] == 'МП' else ('Вехи от NPV' if e.get('npv_start') else 'Вехи'),
                                 e['category'], e.get('role', ''), e.get('position', ''), p['from'],
                                 e.get('leave_date') or p['to'], 'перевод/увольнение' if e.get('leave_date') else '',
                                 p['load'], 'Да' if c.get('continues_from_prev_stage') else 'Нет'])
            for mname, d in (e.get('individual_dates') or {}).items():
                R['ind'].append([e['tab_no'], code, c['stage'], mname, d, 'условное основание'])
            if e.get('npv_start') is not None:
                R['snpv'].append([e['tab_no'], code, c['stage'], e['npv_start'], e['start'], 'старт этапа (условно)'])
            for mname, v in (e.get('kpi') or {}).items():
                m = next(x for x in c['milestones'] if x['name'] == mname)
                if v is not None and m.get('fact'):
                    R['kpi'].append([e['tab_no'], quarter(m['fact']), v])
        lead = emps[0]
        if lead.get('npv_current') is not None:
            cp = lead.get('npv_checkpoints') or {}
            for q in all_q:
                if q < f_q:
                    v = lead['npv_current']
                elif q == f_q:
                    v = cp.get('final', lead['npv_current'])
                elif q == q_add(f_q, 1):
                    v = cp.get('q1', lead['npv_current'])
                else:
                    v = cp.get('q2', lead['npv_current'])
                R['npv'].append([code, q, 'ФМ %s (условная)' % q, v, v, ''])
            for e in emps:   # NPV на дату ухода = NPV квартала ухода, после этапа = NPV квартала завершения
                assert e.get('npv_at_leave') in (None, e['npv_current']), (c['id'], 'npv_at_leave != npv_current')
                assert e.get('npv_stage_end') in (None, cp.get('final')), (c['id'], 'npv_stage_end != checkpoints.final')
        if bs.get('enabled'):
            for q in all_q:
                R['bs'].append([code, q, bs['share_transferred']])

write_csv('hr_employees.csv', ['tab_no', 'fio', 'position', 'department', 'hire_date', 'dismissal_date',
                               'probation_end', 'probation_result'], R['emp'])
write_csv('hr_bgb_history.csv', ['tab_no', 'bgb', 'date_from', 'date_to'], R['bgb'])
write_csv('projects.csv', ['project_code', 'name', 'queue', 'blocks', 'is_exception', 'sale_date', 'handover_to_completion_date'], R['proj'])
write_csv('stages.csv', ['project_code', 'queue', 'stage', 'start_date', 'key_milestone', 'is_completion'], R['stage'])
write_csv('assignments.csv', ['tab_no', 'project_code', 'queue', 'stage', 'sov_type', 'category', 'role', 'position_on_project',
                              'date_from', 'date_to', 'end_reason', 'load', 'from_prev_stage'], R['asg'])
write_csv('individual_dates.csv', ['tab_no', 'project_code', 'stage', 'milestone', 'individual_plan', 'basis'], R['ind'])
write_csv('npv_quarterly.csv', ['project_code', 'quarter', 'fm_version', 'npv_plan_paur', 'npv_forecast_paur', 'fub_approved_date'], R['npv'])
write_csv('start_npv.csv', ['tab_no', 'project_code', 'stage', 'npv_start', 'fixed_date', 'basis'], R['snpv'])
write_csv('bs_transfer.csv', ['project_code', 'quarter', 'share_transferred'], R['bs'])
write_csv('kpi_mp.csv', ['tab_no', 'quarter', 'kpi'], R['kpi'])
write_csv('stage5_expense.csv', ['project_code', 'queue', 'stage', 'expense_remaining', 'plan_budget', 'fact_budget'], R['exp'])
write_csv('key_rates.csv', ['date_from', 'rate'], KEY_RATES)
write_csv('holidays.csv', ['date', 'kind'], [])
write_csv('redistribution.csv', ['tab_no', 'project_code', 'stage', 'milestone', 'pct', 'decided_by', 'approved_date', 'basis'], [])
write_csv('rp_project_sets.csv', ['tab_no', 'rank', 'project_code', 'queue', 'class', 'area_k_m2', 'stage', 'extra_objects',
                                  'is_queue', 'is_logistics', 'approved_date', 'approved_by'],
          [['T9001', 1, 'TP-RP-A', '1', 'Элит', 150, 1, 0, 'Нет', 'Нет', '2026-01-15', 'ВП БПП (условно)'],
           ['T9001', 1, 'TP-RP-B', '1', 'Премиум', 70, 4, 0, 'Нет', 'Нет', '2026-01-15', 'ВП БПП (условно)'],
           ['T9002', 2, 'TP-RP-C', '1', 'Комфорт', 90, 4, 1, 'Нет', 'Нет', '2026-01-20', 'ДП (условно)']])
write_csv('case_map.csv', ['case_id', 'tab_no', 'project_code'], R['map'])

wb = openpyxl.Workbook()
ws = wb.active
ws.title = 'Лист для загрузки вех'
ws.append(MS_HEADER)
for r in R['ms']:
    ws.append(r)
wb.save(os.path.join(OUT, 'milestones_1c.xlsx'))

# Образец файла к начислению (ТЗ 9.5) по эталонам двух кейсов
rows = []
for cid in ('T05', 'T14'):
    g = json.load(open(os.path.join(KIT, 'tests', 'golden', 'spec', cid + '.json'), encoding='utf-8'))
    for e in g['employees']:
        for m in e['milestones']:
            if m['calc_status'] == 'рассчитано' and m['amount']:
                rows.append([e['tab_no'], e['fio'], 'Условное подразделение', 'Тестовый проект %s, очередь 1' % cid, 4,
                             'аванс за веху', m['name'], quarter(m['fact']), ru(m['pay_date']), '%.2f' % m['amount']])
        for p in e['payouts']:
            if p['calc_status'] == 'рассчитано' and p['amount']:
                kind = 'финальный бонус' if p['part'] == '100%' else 'финал %s' % p['part']
                for k, v in ((kind, p['bonus']), ('проценты на отложенную часть (%s)' % p['part'], p['interest'])):
                    rows.append([e['tab_no'], e['fio'], 'Условное подразделение', 'Тестовый проект %s, очередь 1' % cid, 4,
                                 k, 'Ключевая веха этапа', quarter(p['event_date']), ru(p['pay_date']), '%.2f' % v])
rows = [r[:9] + [r[9].replace('.', ',')] + ['SNAP-%s-r1 / СОВ v2 (условно)' % r[7]] for r in rows]
write_csv('payroll_export_sample.csv', ['Табельный номер', 'ФИО', 'Подразделение', 'Проект, очередь', 'Этап', 'Вид начисления',
                                        'Веха', 'Квартал начисления', 'Дата выплаты', 'Сумма, ₽', 'ID расчёта'], rows)
print('samples:', sorted(os.listdir(OUT)))
