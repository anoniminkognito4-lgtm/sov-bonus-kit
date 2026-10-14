"""Контракт чистого расчёта. Реализация — этап M1."""

from sov.engine.params import SovParams


def calculate_case(case: dict, params: SovParams | None = None) -> dict:
    """Вернуть sov-result/1 и problems без доступа к файлам, БД и сети."""
    raise NotImplementedError("M1: движок расчёта ещё не реализован")
