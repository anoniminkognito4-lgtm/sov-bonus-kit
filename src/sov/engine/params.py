"""Неизменяемая таблица параметров редакции СОВ v2, 3 квартал 2026."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SovParams:
    edition: str = "SOV-v2-2026Q3"
    year_days: int = 365
    advance_shares: tuple[float, ...] = (0.67, 0.67, 0.67, 0.67, 0.33)
    newcomer_days: int = 60
    timing_scale_days: int = 100
    milestone_cap: float = 1.5
    early_decision_days: int = 100
    financial_bounds: tuple[float, float] = (0.75, 1.25)
    rp_financial_bounds: tuple[float, float] = (0.5, 1.5)
    low_npv_threshold: float = 500_000_000
    low_npv_delta: float = 50_000_000
    low_npv_step: float = 100_000_000
    low_npv_slope: float = 0.05
    bs_base: float = 0.9
    bs_variable: float = 0.1
    bs_threshold: float = 0.4
    bs_slope: float = 2
    kpi_threshold: float = 0.5
    kpi_slope: float = 2
    kpi_floor: float = 0  # И-3
    final_shares: tuple[float, ...] = (0.5, 0.25, 0.25)
    stage5_shares: tuple[float, ...] = (0.5, 0.5)
    checkpoint_days: int = 90
    pay_day: int = 10
    pay_months_after_quarter: int = 2
    load_change_threshold: float = 0.25
    amount_change_threshold: float = 0.1
    mp_multiple_project_load: float = 0.5
    class_factors: tuple[tuple[str, float], ...] = (
        ("Элит", 1.1),
        ("Премиум", 1.0),
        ("Бизнес", 0.9),
        ("Бизнес+", 0.9),
        ("Комфорт", 0.8),
    )
    area_bounds: tuple[tuple[str, tuple[float, ...]], ...] = (
        ("Элит", (20, 50, 80, 100)),
        ("Премиум", (50, 100, 200, 300)),
        ("Бизнес", (50, 100, 200, 300)),
        ("Бизнес+", (50, 100, 200, 300)),
        ("Комфорт", (100, 200, 300, 400)),
    )
    size_factors: tuple[float, ...] = (1, 1.2, 1.3, 1.4, 1.5)
    stage_factors: tuple[float, ...] = (0.4, 0.4, 0.6, 0.6, 0.3)
    extra_object_factor: float = 0.05
    queue_factor: float = 0.85
    logistics_factor: float = 0.7
    rank_thresholds: tuple[float, ...] = (1.2, 1, 0.8)
    rank_ranges: tuple[tuple[float, float], ...] = ((1, 1.4), (0.8, 1.2), (0.6, 1))
    load_over_threshold_slope: float = 2


DEFAULT_PARAMS = SovParams()
