"""Параметры правил СОВ; наполнение таблицы редакций — этап M1."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SovParams:
    """Редакция методологии, используемая при расчёте."""

    edition: str = "SOV-v2-2026Q3"
