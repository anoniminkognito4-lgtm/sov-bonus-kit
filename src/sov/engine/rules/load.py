"""Нормирование РП и загрузка МП: CALC_RULES §2, толкование И-2."""

from sov.engine.params import DEFAULT_PARAMS, SovParams


def project_load(project: dict, params: SovParams = DEFAULT_PARAMS) -> dict:
    class_name = project["class"]
    bounds = dict(params.area_bounds)[class_name]
    area = project["area_k_m2"]
    size_index = (
        0
        if area < bounds[0]
        else next((i for i, upper in enumerate(bounds[1:], 1) if area <= upper), 4)
    )
    product = (
        dict(params.class_factors)[class_name]
        * params.size_factors[size_index]
        * params.stage_factors[project["stage"] - 1]
    )
    if project.get("is_logistics"):
        product *= params.logistics_factor
    value = product + params.extra_object_factor * project.get("extra_objects", 0)
    if project.get("is_queue") and project["stage"] >= 2:
        value *= params.queue_factor
    return {"expected": value, "expected_size": ("S", "M", "L", "XL", "XXL")[size_index]}


def rp_load(rank: int, projects: list[float], params: SovParams = DEFAULT_PARAMS) -> dict:
    if not projects:
        raise ValueError("Набор проектов РП пуст")
    total = sum(projects)
    threshold = params.rank_thresholds[rank - 1]
    load = (
        1 if total <= threshold else (total / threshold - 1) * params.load_over_threshold_slope + 1
    )
    low, high = params.rank_ranges[rank - 1]
    return {
        "x": total,
        "load_pct": load,
        "per_project": load / len(projects),
        "in_range": low <= total <= high,
    }


def mp_load(projects: int, params: SovParams = DEFAULT_PARAMS) -> float:
    if projects < 1:
        raise ValueError("Количество проектов должно быть положительным")
    return 1.0 if projects == 1 else params.mp_multiple_project_load
