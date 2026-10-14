"""Собирает из кейсов и эталонов таблицы для документации:
  docs/WORKED_EXAMPLE.md   — разбор кейса T13 по шагам;
  docs/TEMPLATE_VS_SPEC.md — чем текущий шаблон отличается от ТЗ, по кейсам;
  docs/TEST_PLAN.md        — таблица кейсов между метками <!-- cases:start --> и <!-- cases:end -->.
Запуск из корня комплекта: python3 tools/make_docs_tables.py
"""
import datetime as dt
import glob
import json
import os
import re

KIT = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
DOCS = os.path.join(KIT, 'docs')


def load(p):
    with open(p, encoding='utf-8') as f:
        return json.load(f)


def rub(x):
    return '—' if x is None else ('%s' % format(x, ',.2f')).replace(',', ' ').replace('.', ',')


def num(x, n=4):
    return '—' if x is None else ('%.*f' % (n, x)).rstrip('0').rstrip('.').replace('.', ',')


def ru(d):
    return '—' if not d else dt.date.fromisoformat(d).strftime('%d.%m.%Y')


cases = {c['id']: c for c in (load(p) for p in sorted(glob.glob(os.path.join(KIT, 'tests', 'cases', '*.json'))))}
spec = {os.path.basename(p)[:-5]: load(p) for p in glob.glob(os.path.join(KIT, 'tests', 'golden', 'spec', '*.json'))}
tmpl = {os.path.basename(p)[:-5]: load(p) for p in glob.glob(os.path.join(KIT, 'tests', 'golden', 'template', '*.json'))}


def order(cid):
    m = re.match(r'T(\d+)(.*)', cid)
    return (int(m.group(1)), m.group(2))


# ---------- TEST_PLAN: таблица кейсов ----------
rows = ['| ID | Что проверяем | Ожидание | Эталон |', '| --- | --- | --- | --- |']
for cid in sorted(cases, key=order):
    c = cases[cid]
    rows.append('| %s | %s | %s | %s |' % (cid, c['title'], c['check'].replace('|', '/'),
                                           'шаблон + правки' if c.get('harness', True) else 'только приложение (A08)'))
tp = os.path.join(DOCS, 'TEST_PLAN.md')
s = open(tp, encoding='utf-8').read()
s = re.sub(r'<!-- cases:start -->.*<!-- cases:end -->', '<!-- cases:start -->\n' + '\n'.join(rows) + '\n<!-- cases:end -->',
           s, flags=re.S)
open(tp, 'w', encoding='utf-8').write(s)

# ---------- TEMPLATE_VS_SPEC ----------
out = ['# Текущий шаблон и ТЗ: разница по кейсам',
       '',
       'Собрано `tools/make_docs_tables.py` из `tests/golden/template` (шаблон как есть) и `tests/golden/spec` (по ТЗ). '
       '«Итого к выплате» — авансы и части финала со статусом «рассчитано», с процентами. Причины разницы — правки и правила из `docs/TEMPLATE_MAP.md`.',
       '',
       'Почти во всех кейсах есть разница в десятки и сотни рублей: это проценты, потому что по ТЗ выплаты 10.08.2025 и 10.05.2026 '
       '(воскресенья) переносятся на пятницу (P5) и индексация отложенной части становится на 2 дня длиннее.',
       '',
       '| Кейс | Сотрудник | Категория | Шаблон, ₽ | ТЗ, ₽ | Разница, ₽ | Что меняется |',
       '| --- | --- | --- | ---: | ---: | ---: | --- |']
for cid in sorted(spec, key=order):
    g, t = spec[cid], tmpl.get(cid)
    if not t:
        continue
    for es, et in zip(g['employees'], t['employees']):
        why = []
        for ms_, mt in zip(es['milestones'], et['milestones']):
            if (ms_['amount'] or 0) != (mt['amount'] or 0) and abs((ms_['amount'] or 0) - (mt['amount'] or 0)) > 0.005:
                why.append('аванс «%s»: %s → %s' % (ms_['name'].replace('Мотивационная веха', 'веха'), rub(mt['amount']), rub(ms_['amount'])))
            if ms_['calc_status'] != mt['calc_status']:
                why.append('«%s»: статус «%s»' % (ms_['name'].replace('Мотивационная веха', 'веха'), ms_['calc_status']))
        fs, ft = es['final'], et['final']
        if fs['amount'] is not None and ft['amount'] is not None and abs(fs['amount'] - ft['amount']) > 0.005:
            why.append('%s: %s → %s' % (fs['kind'], rub(ft['amount']), rub(fs['amount'])))
        if fs['calc_status'] != ft['calc_status']:
            why.append('финал: статус «%s»' % fs['calc_status'])
        if es['k_fin'] is not None and et['k_fin'] is not None and abs(es['k_fin'] - et['k_fin']) > 1e-9:
            why.append('К фин %s → %s' % (num(et['k_fin']), num(es['k_fin'])))
        ps = {p['part']: p for p in es['payouts']}
        pt = {p['part']: p for p in et['payouts']}
        if set(ps) != set(pt):
            why.append('части выплаты: %s → %s' % (' / '.join(pt) or '—', ' / '.join(ps) or '—'))
        else:
            for k in ps:
                if ps[k]['calc_status'] != pt[k]['calc_status']:
                    why.append('часть %s: «%s» → «%s»' % (k, pt[k]['calc_status'], ps[k]['calc_status']))
        for o in g['overrides_applied']:
            if o['tab_no'] == es['tab_no']:
                why.append(o['code'])
        diff = es['total_payable'] - et['total_payable']
        di = es['interest']['total'] - et['interest']['total']
        if cases[cid]['stage'] == 5 and abs(di) > 0.01 * max(1.0, et['interest']['total']):
            why.append('проценты %s → %s: на 5 этапе отложено аванс ÷ 33%% × 67%% (P6)' % (rub(et['interest']['total']), rub(es['interest']['total'])))
        if not why and abs(diff) > 0.005:
            why.append('проценты (даты выплаты, P5)')
        out.append('| %s | %s | %s | %s | %s | %s | %s |' % (cid, es['fio'], es['category'], rub(et['total_payable']),
                                                          rub(es['total_payable']), rub(diff), '; '.join(why) or 'нет'))
out += ['', 'Кейс T11b считается только приложением (нет в шаблоне). Разбор одного кейса по шагам — `docs/WORKED_EXAMPLE.md`.', '']
open(os.path.join(DOCS, 'TEMPLATE_VS_SPEC.md'), 'w', encoding='utf-8').write('\n'.join(out))

# ---------- WORKED_EXAMPLE: T13 ----------
cid = 'T13'
c, g = cases[cid], spec[cid]
e, ce = g['employees'][0], c['employees'][0]
p = ce['periods'][0]
L = ['# Разбор кейса T13 по шагам',
     '',
     'КУПК, 4 этап, шесть промежуточных вех в срок, этап закрыт на 20 дней раньше плана. Тот же расчёт в ячейках калькулятора — '
     '`reference/examples/T13_spec.xlsx` (по ТЗ) и `T13_template.xlsx` (шаблон как есть). Все числа — из `tests/golden/spec/T13.json`; '
     'формулы — `docs/CALC_RULES.md`.',
     '',
     '## Исходные данные',
     '',
     '| Что | Значение |',
     '| --- | --- |',
     '| Сотрудник | %s, %s, табельный %s |' % (e['fio'], e['category'], e['tab_no']),
     '| Начало работы на этапе | %s |' % ru(ce['start']),
     '| Плановая дата ключевой вехи | %s |' % ru(c['final']['plan']),
     '| Факт ключевой вехи | %s |' % ru(c['final']['fact']),
     '| БГБ, загрузка | %s ₽ в год, %s%% |' % (rub(p['bgb']), num(p['load'] * 100)),
     '| NPV стартовый и текущий | %s и %s ₽ |' % (rub(ce['npv_start']), rub(ce['npv_current'])),
     '| NPV через квартал и через полгода | %s и %s ₽ |' % (rub(ce['npv_checkpoints']['q1']), rub(ce['npv_checkpoints']['q2'])),
     '| Ключевая ставка ЦБ | %s%% |' % num(c['key_rate'] * 100),
     '',
     '## 1. Длительность и ПБ',
     '',
     '- Длительность = %s − %s + 1 = **%d дней**.' % (ru(c['final']['plan']), ru(ce['start']), e['duration_days']),
     '- ПБ = %s ÷ 365 × %d × %s = **%s ₽**.' % (rub(p['bgb']), e['duration_days'], num(p['load']), rub(e['pb'])),
     '- Пул авансов = ПБ × 0,67 = %s ₽; пул финала = %s ₽.' % (rub(e['advance_pool']), rub(e['final_pool'])),
     '- К фин = %s ÷ %s = %s (в пределах 0,75–1,25). Множитель БС = 1 (не БС).' % (rub(ce['npv_current']), rub(ce['npv_start']), num(e['k_fin'])),
     '',
     '## 2. Авансы за промежуточные вехи',
     '',
     'Все шесть вех участвуют (план каждой позже старта + 60 дней), вес каждой 1/6. Все закрыты в срок: отклонение 0, К срока = 1.',
     '',
     'Аванс = ПБ × 0,67 × 1/6 × К срока × К фин = %s × 1/6 = **%s ₽**.' % (rub(e['advance_pool']), rub(e['milestones'][0]['amount'])),
     '',
     '| Веха | План | Факт | Отклонение | Вес | Аванс, ₽ | Дата выплаты |',
     '| --- | --- | --- | ---: | ---: | ---: | --- |']
for m in e['milestones']:
    L.append('| %s | %s | %s | %d | %s | %s | %s |' % (m['name'], ru(m['plan']), ru(m['fact']), m['deviation_days'], num(m['weight']),
                                                    rub(m['amount']), ru(m['pay_date'])))
adv = sum(m['amount'] for m in e['milestones'])
f = e['final']
L += ['',
      'Дата выплаты — 10 число второго месяца после квартала факта. Вехи 1 и 4: 10.08.2025 и 10.05.2026 — воскресенья, поэтому 08.08.2025 и 08.05.2026 (в шаблоне без правки P5 — 10-е).',
      '',
      '## 3. Финальный бонус',
      '',
      '- Отклонение ключевой вехи = %s − %s = %d дней опережения; К срока этапа = 1 + 20 ÷ 100 = %s.' % (
          ru(f['plan']), ru(f['fact']), f['deviation_days'], num(f['k_stage'])),
      '- Финал = ПБ × К срока этапа × К фин − авансы = %s × %s × %s − %s = **%s ₽**.' % (
          rub(e['pb']), num(f['k_stage']), num(e['k_fin']), rub(adv), rub(f['amount'])),
      '',
      '## 4. Отложенная часть и проценты',
      '',
      'Каждый аванс откладывает аванс ÷ 0,67 × 0,33 = %s ₽. Проценты считаются отдельно на каждую из трёх частей финала: '
      'отложено × ставка ÷ 365 × дни от выплаты аванса до выплаты части × доля части (0,5 / 0,25 / 0,25).' % rub(e['interest']['rows'][0]['deferred']),
      '',
      '| Веха | Выплата аванса | Дни до 50% | Проценты 50%, ₽ | Дни до 25% (1) | Проценты, ₽ | Дни до 25% (2) | Проценты, ₽ |',
      '| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |']
for r in e['interest']['rows']:
    a, b, c3 = r['parts']
    L.append('| %s | %s | %d | %s | %d | %s | %d | %s |' % (r['milestone'], ru(r['pay_date']), a['days'], rub(a['interest']),
                                                          b['days'], rub(b['interest']), c3['days'], rub(c3['interest'])))
L += ['',
      'Пример для вехи 1: %s × 0,16 ÷ 365 × 459 × 0,5 = %s ₽.' % (rub(e['interest']['rows'][0]['deferred']), rub(e['interest']['rows'][0]['parts'][0]['interest'])),
      '',
      '## 5. Выплата финала',
      '',
      '| Часть | Событие | Дата выплаты | Условие | Бонус, ₽ | Проценты, ₽ | К выплате, ₽ |',
      '| --- | --- | --- | --- | ---: | ---: | ---: |']
cond = {'50%': 'нет', '25% (1)': 'NPV через квартал ≥ финального', '25% (2)': 'NPV через полгода ≥ финального'}
for pp in e['payouts']:
    L.append('| %s | %s | %s | %s | %s | %s | %s |' % (pp['part'], ru(pp['event_date']), ru(pp['pay_date']), cond[pp['part']],
                                                      rub(pp['bonus']), rub(pp['interest']), rub(pp['amount'])))
t13t = tmpl[cid]['employees'][0]
L += ['',
      'NPV через квартал и через полгода равен финальному (5 млрд), поэтому по ТЗ обе части 25% платятся. Шаблон без правки P3 требует «строго выше» и '
      'приостанавливает их: в `T13_template.xlsx` вместо сумм стоят «Выплаты приостановлены» и «Принять решение о перерасчете…».',
      '',
      '## 6. Итого по этапу',
      '',
      '- Авансы: %s ₽.' % rub(adv),
      '- Финал с процентами: %s ₽.' % rub(sum(pp['amount'] for pp in e['payouts'])),
      '- Всего к выплате по ТЗ: **%s ₽**; по шаблону как есть: %s ₽.' % (rub(e['total_payable']), rub(t13t['total_payable'])),
      '']
open(os.path.join(DOCS, 'WORKED_EXAMPLE.md'), 'w', encoding='utf-8').write('\n'.join(L))
print('ok')
