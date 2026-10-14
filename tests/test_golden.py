"""Полный расчёт сравнивается с независимым эталоном по ТЗ."""

import pytest
from conftest import ROOT, read_json

from sov.engine.calculate import calculate_case
from tools.compare_golden import compare

GOLDEN = sorted((ROOT / "tests/golden/spec").glob("*.json"))


@pytest.mark.m1
@pytest.mark.parametrize("path", GOLDEN, ids=[p.stem for p in GOLDEN])
def test_golden(path):
    case = read_json(ROOT / "tests/cases" / path.name)
    differences = compare(read_json(path), calculate_case(case))
    assert not differences, "\n".join(differences)
