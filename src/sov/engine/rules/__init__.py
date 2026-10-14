"""Адаптер JSON юнит-кейсов к чистым функциям правил."""

from sov.engine.rules.dates import milestone_weights, pay_date
from sov.engine.rules.financial import bs_multiplier, k_fin, milestone_factor, stage_factor
from sov.engine.rules.load import mp_load, project_load, rp_load


def evaluate_rule(name: str, inputs: dict) -> dict:
    if name == "weights":
        return milestone_weights(**inputs)
    if name == "normalization.project":
        return project_load(inputs)
    if name == "normalization.rp":
        return rp_load(**inputs)
    if name == "mp_load":
        return {"expected_per_project": mp_load(**inputs)}
    functions = {
        "k_fin": k_fin,
        "bs_multiplier": bs_multiplier,
        "pay_date": pay_date,
        "milestone_factor": milestone_factor,
        "k_stage": stage_factor,
    }
    return {"expected": functions[name](**inputs)}
