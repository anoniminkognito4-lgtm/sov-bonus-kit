"""Распознавание должностей по справочнику (D-098).

Чистые функции. Название из калькулятора или 1С приводится к названию справочника:
пробелы и переносы схлопываются, «и.о.» снимается, опечатки исправляются по таблице
`position_fix`, хвост с названием проекта отрезается. Не распознано — вопрос, не угадываем.
"""

import re

KPI_TEMPORARY = "вр. KPI (Вехи)"
ACTING = re.compile(r"^(и\.\s*о\.|исполняющ\w*\s+обязанност\w*)\s*", re.IGNORECASE)


def clean(text):
    """Пробелы, неразрывные пробелы и переносы строк → один пробел."""
    return re.sub(r"\s+", " ", (text or "").replace(" ", " ")).strip()


def key(text):
    return clean(text).lower().replace("ё", "е")


def _fix(text, fixes):
    for wrong, right in sorted(fixes, key=lambda pair: -len(pair[0])):
        pattern = rf"(?<![\w]){re.escape(key(wrong))}(?![\w])"
        text = re.sub(pattern, key(right), text)
    return text


class PositionBook:
    """Справочник должностей с правилами приведения названий."""

    def __init__(self, refs, fixes=(), projects=()):
        self.refs = list(refs)
        self.fixes = [(f["wrong"], f["right"]) for f in fixes if f["wrong"]]
        self.projects = {key(p) for p in projects if p}

    def __bool__(self):
        return bool(self.refs)

    def recognize(self, raw, block=None):
        """{'ref': строка справочника или None, 'steps': [...], 'raw': ...}."""
        steps = []
        text = key(raw)
        if text != (raw or "").strip().lower().replace("ё", "е"):
            steps.append("пробелы и переносы")
        acting = ACTING.sub("", text)
        if acting != text:
            steps.append("снято «и.о.»")
            text = acting
        fixed = _fix(text, self.fixes)
        if fixed != text:
            steps.append("исправлена опечатка")
            text = fixed
        refs = [r for r in self.refs if block is None or r["block"] == block]
        exact = [r for r in refs if key(r["position"]) == text]
        if exact:
            return {"raw": raw, "ref": exact[0], "steps": steps}
        # Хвост с названием проекта: «Руководитель строительства Twist».
        best = None
        for ref in refs:
            name = key(ref["position"])
            if text.startswith(name + " ") and (best is None or len(name) > len(best[0])):
                best = (name, ref)
        if best and text[len(best[0]) :].strip() in self.projects:
            return {"raw": raw, "ref": best[1], "steps": [*steps, "отрезан проект"]}
        return {"raw": raw, "ref": None, "steps": steps}


def resolve(assignment, position, ref):
    """Категория и вид СОВ назначения: из 1С, пустое — из справочника (D-098).

    Вид СОВ «вр. KPI (Вехи)» — категория МП (через KPI).
    """
    sov_type = assignment.get("sov_type") or (ref or {}).get("sov_type") or None
    category = assignment.get("category") or (ref or {}).get("category") or None
    if sov_type == KPI_TEMPORARY:
        category = "МП"
    return {
        **assignment,
        "position": (ref or {}).get("position") or position,
        "sov_type": sov_type,
        "category": category,
    }
