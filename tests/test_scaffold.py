"""Готовность каркаса проверяется отдельно от будущего движка."""

import inspect

from conftest import ROOT, read_json, unit_cases

from sov.engine.calculate import calculate_case
from sov.engine.params import SovParams


def test_engine_contract():
    assert list(inspect.signature(calculate_case).parameters) == ["case", "params"]
    assert SovParams().edition == "SOV-v2-2026Q3"


def test_test_data_available():
    assert list(unit_cases())
    for path in (ROOT / "tests/golden/spec").glob("*.json"):
        case = read_json(ROOT / "tests/cases" / path.name)
        assert case["id"] == read_json(path)["id"]
    for control in read_json(ROOT / "tests/autocontrol/cases.json")["cases"]:
        assert (ROOT / "tests/cases" / f"{control['base']}.json").is_file()


def test_package_structure():
    for name in ("engine", "registry", "loaders", "adapters", "exports", "services"):
        assert (ROOT / "src/sov" / name / "__init__.py").is_file()
