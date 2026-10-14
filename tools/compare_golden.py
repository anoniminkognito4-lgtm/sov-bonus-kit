"""Сверка результатов движка с эталонами tests/golden/spec.

Результат движка по кейсу — JSON в формате sov-result/1 (docs/DATA_FORMATS.md, раздел 2), файл <ID>.json.
Сравниваются только поля, которые есть в эталоне; лишние поля в результате допустимы.

  python3 tools/compare_golden.py build/results            # все эталоны
  python3 tools/compare_golden.py build/results --case T05 # один кейс
  python3 tools/compare_golden.py build/results --golden tests/golden/template   # сравнить с текущей практикой

Из pytest: from tools.compare_golden import compare  ->  список расхождений (пустой = совпало).
"""
import argparse
import glob
import json
import os
import sys

MONEY_TOL = 0.01      # рубля; приёмка по ТЗ — 1 ₽, но промежуточные значения не округляются, так что ждём точности
COEF_TOL = 1e-9
SKIP = {'title', 'variant', 'schema', 'overrides_applied', 'patches_applied', 'note', 'what'}
COEF = {'k_fin', 'k_bs', 'weight', 'k_stage', 'rate', 'advance_share', 'k_expense'}


def _cmp(path, g, r, out):
    key = path.rsplit('.', 1)[-1].split('[')[0]
    if key in SKIP:
        return
    if isinstance(g, dict):
        if not isinstance(r, dict):
            out.append('%s: ожидался объект, получено %r' % (path, r))
            return
        for k, v in g.items():
            if k in SKIP:
                continue
            if k not in r:
                if v is not None:          # null в эталоне = поле можно не выдавать
                    out.append('%s.%s: нет поля в результате' % (path, k))
                continue
            _cmp('%s.%s' % (path, k), v, r[k], out)
    elif isinstance(g, list):
        if not isinstance(r, list) or len(r) != len(g):
            out.append('%s: длина %s, ожидалось %d' % (path, len(r) if isinstance(r, list) else type(r).__name__, len(g)))
            return
        if key == 'blocking_problems':
            gs = sorted((p['code'], p.get('tab_no')) for p in g)
            rs = sorted((p['code'], p.get('tab_no')) for p in r)
            if gs != rs:
                out.append('%s: %s, ожидалось %s' % (path, rs, gs))
            return
        for i, (a, b) in enumerate(zip(g, r)):
            _cmp('%s[%d]' % (path, i), a, b, out)
    elif isinstance(g, bool) or g is None or isinstance(g, str):
        if g != r:
            out.append('%s: %r, ожидалось %r' % (path, r, g))
    elif isinstance(g, (int, float)):
        if not isinstance(r, (int, float)) or isinstance(r, bool):
            out.append('%s: %r, ожидалось %r' % (path, r, g))
            return
        tol = COEF_TOL if key in COEF else MONEY_TOL
        if abs(g - r) > tol:
            out.append('%s: %.6f, ожидалось %.6f (разница %.6f)' % (path, r, g, r - g))
    else:
        out.append('%s: неизвестный тип эталона %r' % (path, type(g)))


def compare(golden, result):
    """Возвращает список расхождений result с golden (пустой список = совпало)."""
    out = []
    _cmp(golden.get('id', '?'), golden, result, out)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('results', help='папка с результатами движка <ID>.json')
    ap.add_argument('--golden', default=os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'tests', 'golden', 'spec'))
    ap.add_argument('--case')
    a = ap.parse_args()
    bad = 0
    files = sorted(glob.glob(os.path.join(a.golden, '*.json')))
    for gp in files:
        cid = os.path.splitext(os.path.basename(gp))[0]
        if a.case and cid != a.case:
            continue
        rp = os.path.join(a.results, cid + '.json')
        if not os.path.exists(rp):
            print('%-5s НЕТ РЕЗУЛЬТАТА' % cid)
            bad += 1
            continue
        with open(gp, encoding='utf-8') as f:
            g = json.load(f)
        with open(rp, encoding='utf-8') as f:
            r = json.load(f)
        diffs = compare(g, r)
        print('%-5s %s' % (cid, 'OK' if not diffs else 'РАСХОЖДЕНИЙ: %d' % len(diffs)))
        for d in diffs[:30]:
            print('      ' + d)
        bad += bool(diffs)
    print('\nИтого: %d из %d кейсов с расхождениями' % (bad, len(files) if not a.case else 1))
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
