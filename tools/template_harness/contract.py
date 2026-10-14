"""Перевод пересчитанного шаблона в формат результата приложения (docs/DATA_FORMATS.md, раздел 2).

Для варианта spec здесь же применяются правила ТЗ, которые формулами шаблона не выразить:
  S1  статус «ждёт предыдущую веху»            (СОВ сл. 25; ТЗ 6.7)
  S2  статус «после ухода» для вех после ухода (СОВ сл. 47; сумму обнуляет правка P7)
  S3  статус «требует решения» при опережении этапа > 100 дней (СОВ сл. 13; ТЗ 6.4)
  O1  финал или остаток < 0: к выплате max(0, финал + проценты), иначе проценты не платятся (СОВ сл. 11)
  O2  5 этап, КУПК: 100% сразу; РП и РПС при расходной части — 50% + 50% через 2 квартала × К соблюдения (СОВ сл. 28)
  O3  уход КУПК: К фин по меньшему из NPV на дату ухода и после завершения этапа (СОВ сл. 47)

Каждая формула здесь сверяется с тем, что посчитал шаблон (assert), поэтому расхождение правил и шаблона
не может пройти незаметно.
"""
import datetime as dt

KUPK = ('КУПК', 'КУПК (РП/ЗРП)')
SUSPENDED = 'Выплаты приостановлены'
RECALC = 'Принять решение о перерасчете выплаченной части при падении NPV'
EPS = 0.01  # рубля: допуск сверки формул с шаблоном


def D(s):
    return dt.date.fromisoformat(s) if s else None


def num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def k_bs(case, cat):
    bs = case.get('bs') or {}
    if bs.get('enabled') and cat in ('КУПК', 'РУПК'):
        return 0.9 + 0.1 * min(1.0, max(0.0, (bs['share_transferred'] - 0.4) * 2))
    return 1.0


def k_fin(npv_start, npv_cur, cat):
    lo, hi = (0.5, 1.5) if cat == 'КУПК (РП/ЗРП)' else (0.75, 1.25)
    if npv_start < 500_000_000:
        diff = npv_cur - npv_start
        k = 1 + 0.05 * diff / 100_000_000 if abs(diff) > 50_000_000 else 1.0
    else:
        k = npv_cur / npv_start
    return min(hi, max(lo, k))


def kpi_factor(e):
    if e['category'] != 'МП':
        return 1.0
    vals = [v for v in (e.get('kpi') or {}).values() if v is not None]
    return (sum(vals) / len(vals) - 0.5) * 2


def build(raw, case, variant):
    spec = variant == 'spec'
    out = {'schema': 'sov-result/1', 'id': case['id'], 'variant': variant, 'title': case['title'],
           'overrides_applied': [], 'blocking_problems': [], 'employees': []}
    stage = case['stage']
    plan_of = {m['name']: m['plan'] for m in case['milestones']}
    for re_, e in zip(raw['employees'], case['employees']):
        cat = e['category']
        kupk = cat in KUPK
        leave = e.get('leave_date')
        ov = lambda code, what: out['overrides_applied'].append({'code': code, 'tab_no': e['tab_no'], 'what': what})  # noqa: E731

        # ---------- вехи ----------
        ms = []
        for rm in re_['milestones']:
            amt, fact = rm['amount'], rm['fact']
            if fact is None:
                st, a = 'не закрыта', None
            elif isinstance(amt, str):
                st, a = ('не участвует' if 'не участвует' in amt.lower() else 'нет данных'), None
            elif amt is None:
                st, a = 'нет данных', None
            else:
                st, a = 'рассчитано', amt
            plan_eff = rm['individual_plan'] or rm['plan']
            ms.append({'name': rm['name'], 'plan': rm['plan'], 'individual_plan': rm['individual_plan'], 'fact': fact,
                       'deviation_days': (D(plan_eff) - D(fact)).days if fact else None,
                       'weight': num(rm['weight']), 'amount': a,
                       'pay_date': rm['pay_date'] if fact and st != 'не участвует' else None,
                       'calc_status': st})
        sum_adv = sum(m['amount'] for m in ms if m['amount'] is not None)
        if spec:
            for m in ms:
                if m['fact'] is None:
                    continue
                if leave and D(m['fact']) > D(leave):
                    assert m['amount'] in (0, None), (case['id'], m)        # обнулено правкой P7
                    m['amount'], m['calc_status'] = 0.0, 'после ухода'
                elif any(o['fact'] is None and D(plan_of[o['name']]) < D(plan_of[m['name']]) for o in ms):
                    m['calc_status'] = 'ждёт предыдущую веху'

        # ---------- финальный бонус / остаток при уходе ----------
        rf = re_['final']
        f_fact = rf['fact']
        dev_f = (D(rf['individual_plan'] or rf['plan']) - D(f_fact)).days if f_fact else None
        pb, kb, kp = re_['pb'], k_bs(case, cat), kpi_factor(e)
        kf = re_['k_fin'] if kupk else 1.0
        if kupk:
            assert abs(kf - k_fin(e['npv_start'], e['npv_current'], cat)) < 1e-9 or not spec, (case['id'], kf)
        final = {'plan': rf['plan'], 'individual_plan': rf['individual_plan'], 'fact': f_fact, 'deviation_days': dev_f,
                 'kind': 'остаток при уходе' if leave else 'финальный бонус', 'k_stage': None,
                 'amount': None, 'raw_amount': None, 'pay_date': rf['pay_date'] if f_fact else None,
                 'calc_status': 'не закрыта'}
        worked = (D(leave) - D(e['start'])).days + 1 if leave else None
        kf_rem = None                  # К фин остатка при уходе, если отличается от К фин авансов (O3)
        leave_block = ({'date': leave, 'worked_days': worked, 'k_stage': None, 'k_fin': None, 'remainder': None}
                       if leave else None)
        payouts = []
        rows = []
        for r in re_['interest']['rows']:
            if num(r['advance']) is None:
                continue
            parts = ([('50%', r['days_1'], r['interest_1']), ('25% (1)', r['days_2'], r['interest_2']),
                      ('25% (2)', r['days_3'], r['interest_3'])] if kupk else [('100%', r['days_1'], r['interest_1'])])
            rows.append({'milestone': r['milestone'], 'advance': r['advance'], 'pay_date': r['pay_date'],
                         'deferred': num(r['deferred']), 'rate': r['rate'],
                         # пока ключевая веха не закрыта, дат выплаты частей нет — и процентов тоже
                         'parts': [{'part': p, 'days': num(dd), 'interest': num(i) or 0.0} for p, dd, i in parts] if f_fact else []})

        def part_interest(label):
            return sum(p['interest'] for r in rows for p in r['parts'] if p['part'] == label)

        if f_fact:
            if leave:
                ks = 1 + (min(0, dev_f) if spec else dev_f) / 100
                calc_raw = lambda kf_: pb * kb / re_['duration_days'] * worked * kf_ * ks * kp - sum_adv  # noqa: E731
            else:
                ks = 1 + dev_f / 100
                calc_raw = lambda kf_: pb * kb * kf_ * ks * kp - sum_adv  # noqa: E731
            raw_final = calc_raw(kf)
            fa = num(rf['amount'])
            assert fa is not None and abs(max(0.0, raw_final) - fa) < EPS, (case['id'], variant, raw_final, fa)
            final.update(k_stage=ks, amount=fa, raw_amount=raw_final, calc_status='рассчитано')

            # выплаты финальной части
            rp = re_['payouts']
            if not kupk:
                payouts = [{'part': '100%', 'event_date': rp[0]['event_date'], 'pay_date': rp[0]['pay_date'],
                            'bonus': fa, 'interest': part_interest('100%'), 'amount': num(rp[0]['amount']),
                            'calc_status': 'рассчитано', 'npv_checkpoint': None}]
            else:
                labels, shares = ('50%', '25% (1)', '25% (2)'), (0.5, 0.25, 0.25)
                for k in range(3):
                    payouts.append({'part': labels[k], 'event_date': rp[k]['event_date'], 'pay_date': rp[k]['pay_date'],
                                    'bonus': fa * shares[k], 'interest': part_interest(labels[k]), 'amount': None,
                                    'calc_status': 'нет данных', 'npv_checkpoint': rp[k]['npv_checkpoint']})
                a0, a1, a2 = (rp[k]['amount'] for k in range(3))
                if num(a0) is not None:
                    payouts[0].update(amount=a0, calc_status='рассчитано')
                if num(a1) is not None:
                    payouts[1].update(amount=a1, calc_status='рассчитано')
                elif a1 == SUSPENDED:
                    payouts[1]['calc_status'] = 'приостановлено'
                if num(a2) is not None:
                    if payouts[1]['calc_status'] == 'приостановлено':
                        # NPV вернулся через полгода: шаблон выплачивает обе части 25% одной суммой
                        p1 = payouts[1]
                        p1.update(amount=p1['bonus'] + p1['interest'], calc_status='рассчитано',
                                  pay_date=rp[2]['pay_date'], delayed=True)
                        payouts[2].update(amount=a2 - p1['amount'], calc_status='рассчитано')
                    else:
                        payouts[2].update(amount=a2, calc_status='рассчитано')
                elif a2 in (SUSPENDED, RECALC):
                    payouts[2]['calc_status'] = 'приостановлено'
                    if a2 == RECALC:
                        payouts[2]['note'] = 'NPV ниже финального и через квартал, и через полгода: решение о перерасчёте выплаченной части'
            for p in payouts:
                if p['calc_status'] == 'рассчитано':
                    assert abs(p['amount'] - p['bonus'] - p['interest']) < EPS, (case['id'], variant, p)

            if spec:
                # O3: уход КУПК — К фин по меньшему NPV
                if leave and kupk and (e.get('npv_at_leave') is not None or e.get('npv_stage_end') is not None):
                    npvs = [v for v in (e.get('npv_at_leave'), e.get('npv_stage_end')) if v is not None]
                    kf_leave = k_fin(e['npv_start'], min(npvs), cat)
                    if abs(kf_leave - kf) > 1e-12:
                        raw_final = calc_raw(kf_leave)
                        fa = max(0.0, raw_final)
                        final.update(amount=fa, raw_amount=raw_final)
                        for p, sh in zip(payouts, (0.5, 0.25, 0.25)):
                            p['bonus'] = fa * sh
                            if p['calc_status'] == 'рассчитано':
                                p['amount'] = p['bonus'] + p['interest']
                        ov('O3', 'К фин остатка = %.6f по NPV %.0f (меньший из NPV на дату ухода и после этапа); шаблон %.6f'
                           % (kf_leave, min(npvs), kf))
                    kf_rem = kf_leave
                # O2: 5 этап, КУПК
                if stage == 5 and kupk:
                    exp = case.get('stage5_expense') or {}
                    fp = payouts[0]
                    if e.get('role') in ('РП', 'РПС') and exp.get('remaining'):
                        k_exp = min(1.0, exp['plan_budget'] / exp['fact_budget'])
                        for r in rows:
                            p50, _, p25b = r['parts']
                            r['parts'] = [p50, {'part': '50% (2)', 'days': p25b['days'], 'interest': 2 * p25b['interest']}]
                        third = re_['payouts'][2]
                        payouts = [
                            dict(fp, bonus=fa * 0.5, interest=part_interest('50%'), npv_checkpoint=None),
                            {'part': '50% (2)', 'event_date': third['event_date'], 'pay_date': third['pay_date'],
                             'bonus': fa * 0.5 * k_exp, 'interest': part_interest('50% (2)'), 'npv_checkpoint': None,
                             'k_expense': k_exp}]
                        ov('O2', '5 этап, %s с расходной частью: 50%% + 50%% через 2 квартала × К соблюдения %.4f' % (e['role'], k_exp))
                    else:
                        for r in rows:
                            p50 = r['parts'][0]
                            r['parts'] = [{'part': '100%', 'days': p50['days'], 'interest': 2 * p50['interest']}]
                        payouts = [dict(fp, part='100%', bonus=fa, interest=part_interest('100%'), npv_checkpoint=None)]
                        ov('O2', '5 этап, КУПК (роль: %s): финал 100%% сразу, проценты за весь срок одной частью' % e.get('role'))
                    for p in payouts:
                        p['amount'], p['calc_status'] = p['bonus'] + p['interest'], 'рассчитано'
                # O1: отрицательный финал или остаток
                if raw_final < 0:
                    net = raw_final + sum(p['interest'] for p in payouts)
                    if len(payouts) == 1 or net <= 0:
                        for p in payouts:
                            p.update(bonus=0.0, amount=0.0, calc_status='не выплачивается')
                        if net > 0:
                            payouts[0].update(amount=net, calc_status='рассчитано')
                    else:
                        for p in payouts:
                            p.update(amount=None, calc_status='ручной расчёт')
                    ov('O1', '%s до обнуления %.2f, с процентами %.2f -> %s' % (
                        final['kind'], raw_final, net, 'к выплате %.2f' % net if net > 0 else 'проценты не платятся'))
                # S3: опережение этапа больше 100 дней
                if dev_f > 100:
                    out['blocking_problems'].append({'code': 'STAGE_EARLY_OVER_100', 'tab_no': e['tab_no']})
                    final['calc_status'] = 'требует решения'
                    for p in payouts:
                        p['calc_status'] = 'требует решения'
                # S1 для ключевой вехи
                elif any(m['fact'] is None for m in ms):
                    final['calc_status'] = 'ждёт предыдущую веху'
            if leave:
                leave_block = {'date': leave, 'worked_days': worked, 'k_stage': ks,
                               'k_fin': (kf_rem if kf_rem is not None else kf) if kupk else None,
                               'remainder': final['amount']}

        interest_total = sum(p['interest'] for r in rows for p in r['parts'])
        payable = (sum(m['amount'] for m in ms if m['calc_status'] == 'рассчитано' and m['amount'] is not None)
                   + sum(p['amount'] for p in payouts if p['calc_status'] == 'рассчитано' and p['amount'] is not None))
        out['employees'].append({
            'tab_no': e['tab_no'], 'fio': e['fio'], 'category': cat, 'role': e.get('role'),
            'start': e['start'], 'leave_date': leave, 'duration_days': re_['duration_days'], 'pb': pb,
            'advance_share': 0.33 if stage == 5 else 0.67, 'advance_pool': re_['advance_pool'], 'final_pool': re_['final_pool'],
            'k_fin': kf if kupk else None, 'k_bs': kb,
            'milestones': ms, 'final': final, 'leave': leave_block,
            'interest': {'rows': rows, 'total': interest_total}, 'payouts': payouts,
            'total_payable': payable})
    return out
