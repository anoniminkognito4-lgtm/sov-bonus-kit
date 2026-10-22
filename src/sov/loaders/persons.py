"""Сотрудник по ФИО: одна функция сопоставления для всех загрузчиков (D-101).

Табельного номера нет. Человек — внутренний person_id (P0001…), его создаёт система.
ФИО сравнивается без краевых и повторных пробелов (в том числе неразрывных), ё = е,
без учёта регистра. Подразделение и должность — подсказка. Где нельзя решить
однозначно, возвращается вопрос, а не догадка; ответы — решения person_merge и
person_split в справочнике person_alias (журнал и откат как у загрузки).
"""

import re
from dataclasses import dataclass, field

QUESTIONS = {
    "PERSON_TRANSFER_OR_NAMESAKE": (
        "То же ФИО, но другое подразделение или должность: тот же человек перевёлся "
        "или однофамилец?"
    ),
    "PERSON_SIMILAR": "Похожее ФИО: новый человек или тот же?",
    "PERSON_AMBIGUOUS": "Несколько сотрудников с таким ФИО: подразделение не помогает выбрать",
}


def norm(text):
    """Пробелы по краям, двойные и неразрывные пробелы, ё → е, без учёта регистра."""
    text = str(text or "").replace(" ", " ").replace("ё", "е").replace("Ё", "Е")
    return re.sub(r"\s+", " ", text).strip().lower()


def clean(text):
    return re.sub(r"\s+", " ", str(text or "").replace(" ", " ")).strip()


def _rows(table):
    return list(table.values()) if isinstance(table, dict) else list(table or ())


@dataclass
class Match:
    """status: found — нашёлся; new — такого человека нет; question — нужен ответ."""

    status: str
    person_id: str | None = None
    code: str | None = None
    candidates: list = field(default_factory=list)

    @property
    def message(self):
        return QUESTIONS.get(self.code, "")


class People:
    """Индекс сотрудников и решений person_alias. `state` — {таблица: строки}."""

    def __init__(self, state):
        self.employees = _rows(state["employee"])
        self.aliases = {}
        for row in sorted(
            _rows(state.get("person_alias", ())), key=lambda r: r.get("created_at") or ""
        ):
            self.aliases[(row["fio"], row["department"] or "")] = row["person_id"]

    def match(self, fio, department=None, position=None):
        key, dept, post = norm(fio), norm(department), norm(position)
        if not key:
            raise ValueError("Не указано ФИО")
        for alias in ((key, dept), (key, "")):
            if alias in self.aliases:
                return Match("found", self.aliases[alias])
        same = [e for e in self.employees if norm(e["fio"]) == key]
        if len(same) > 1:
            same = [e for e in same if not dept or norm(e["department"]) == dept] or same
            if len(same) > 1 and post:
                same = [e for e in same if norm(e["position"]) == post] or same
            if len(same) > 1:
                return Match("question", code="PERSON_AMBIGUOUS", candidates=same)
        if len(same) == 1:
            person = same[0]
            moved = (
                dept and norm(person["department"]) and norm(person["department"]) != dept
            ) or (post and norm(person["position"]) and norm(person["position"]) != post)
            if moved:
                return Match("question", code="PERSON_TRANSFER_OR_NAMESAKE", candidates=[person])
            return Match("found", person["person_id"])
        similar = [e for e in self.employees if _similar(key, dept, e)]
        if similar:
            return Match("question", code="PERSON_SIMILAR", candidates=similar)
        return Match("new")


def _similar(key, dept, employee):
    """Та же фамилия и имя при другом отчестве; другая фамилия при тех же имени,
    отчестве и подразделении (смена фамилии)."""
    mine, theirs = key.split(" "), norm(employee["fio"]).split(" ")
    if len(mine) < 2 or len(theirs) < 2:
        return False
    if mine[:2] == theirs[:2] and mine[2:] != theirs[2:]:
        return True
    return (
        len(mine) >= 3
        and mine[1:] == theirs[1:]
        and mine[0] != theirs[0]
        and bool(dept)
        and norm(employee["department"]) == dept
    )


def next_person_id(state, reserved=()):
    numbers = [
        int(match.group(1))
        for value in [*(e["person_id"] for e in _rows(state["employee"])), *reserved]
        if (match := re.fullmatch(r"P(\d+)", value or ""))
    ]
    return f"P{max(numbers, default=0) + 1:04d}"


def question(match, fio, department=None, position=None):
    """Вопрос для предпросмотра загрузки и экрана «Люди»."""
    return {
        "code": match.code,
        "blocking": True,
        "message": match.message,
        "fio": clean(fio),
        "department": clean(department) or None,
        "position": clean(position) or None,
        "candidates": [
            {k: c.get(k) for k in ("person_id", "fio", "department", "position")}
            for c in match.candidates
        ],
    }
