"""Переносимая приёмка на условных данных с честной фиксацией платформы запуска."""

import argparse
import hashlib
import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def quarter_workflow(folder):
    from sov.exports.files import payroll, statement
    from sov.registry.session import Registry
    from sov.services.cabinet import CabinetService
    from sov.services.panel import Actor, PanelService
    from tools.make_panel_demo import make_panel_demo

    registry = Registry(folder / "Реестр СОВ.sqlite")
    try:
        panel = PanelService(registry, Actor("Условная приёмка"))
        panel.demo(ROOT / "samples")
        for source in make_panel_demo(folder / "Условные исправления"):
            panel.apply(panel.preview(source))
        assignment = next(a for a in registry.rows("assignment") if a["person_id"] == "T0039")
        panel.save_decision(
            "early_stage",
            assignment["id"],
            {"approved": True},
            "Условное согласование",
            "условное_письмо.pdf",
        )
        calculation = panel.calculate("2026-Q3")
        if calculation.blocking:
            raise AssertionError(str(calculation.blocking))
        panel.close(calculation)
        closed = panel.closed("2026-Q3")
        (folder / "Начисления СОВ.xlsx").write_bytes(payroll(closed))
        (folder / "Начисления СОВ.csv").write_bytes(payroll(closed, "csv"))
        value = next(iter(calculation.results.values()))
        (folder / "Выписка СОВ.xlsx").write_bytes(
            statement(calculation.results, value["case"]["project_code"], value["case"]["stage"])
        )
        cabinet = CabinetService(registry, Actor("Сотрудник T0002", "Сотрудник", "T0002"))
        own = cabinet.assignments()[0]["id"]
        cabinet.card(own, "2026-Q3", as_of="2026-09-30")
        before = hashlib.sha256(registry.path.read_bytes()).hexdigest()
        panel.simulate(assignment["id"], {}, as_of="2026-09-30")
        cabinet.simulate(own, {}, as_of="2026-09-30")
        if hashlib.sha256(registry.path.read_bytes()).hexdigest() != before:
            raise AssertionError("Моделирование изменило реестр")
        return {
            "quarter": "2026-Q3",
            "assignments": len(calculation.assignment_ids),
            "closed_snapshot": closed["id"],
            "scenario_database_unchanged": True,
        }
    finally:
        registry.dispose()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-windows", action="store_true")
    parser.add_argument("--output", type=Path, default=ROOT / "build/acceptance")
    args = parser.parse_args(argv)
    moment = datetime.now(timezone.utc)
    folder = args.output.resolve() / moment.strftime("%Y%m%dT%H%M%S%fZ") / "Проверка с пробелами"
    folder.mkdir(parents=True, exist_ok=False)
    report = {
        "timestamp_utc": moment.isoformat(),
        "system": platform.system(),
        "machine": platform.machine(),
        "python": sys.version,
        "lock_sha256": hashlib.sha256((ROOT / "uv.lock").read_bytes()).hexdigest(),
        "windows_verified": False,
        "status": "failed",
        "checks": [],
    }
    if args.require_windows and platform.system() != "Windows":
        report["error"] = "Приёмку Windows необходимо запускать на Windows"
    else:
        commands = [
            [sys.executable, "-m", "pytest", "-q"],
            [sys.executable, "-m", "ruff", "check", "src", "app", "tests"],
            [sys.executable, "-m", "ruff", "format", "--check", "src", "app", "tests"],
            [
                sys.executable,
                "-m",
                "sov.engine.cli",
                str(ROOT / "tests/cases"),
                str(folder / "Расчёты движка"),
            ],
            [sys.executable, str(ROOT / "tools/compare_golden.py"), str(folder / "Расчёты движка")],
            [
                sys.executable,
                "-m",
                "sov.registry.cli",
                str(ROOT / "samples"),
                str(folder / "Образцы реестра.sqlite"),
                str(folder / "Расчёты реестра"),
            ],
            [
                sys.executable,
                str(ROOT / "tools/compare_registry_golden.py"),
                str(folder / "Расчёты реестра"),
            ],
        ]
        try:
            env = {**os.environ, "PYTHONUTF8": "1", "UV_OFFLINE": "1"}
            for index, command in enumerate(commands, 1):
                print(f"[{index}/{len(commands)}] {' '.join(command[1:4])}", flush=True)
                process = subprocess.run(
                    command,
                    cwd=ROOT,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    env=env,
                    check=False,
                )
                (folder / f"check-{index}.log").write_text(
                    process.stdout + process.stderr, encoding="utf-8"
                )
                report["checks"].append({"command": command, "returncode": process.returncode})
                if process.returncode:
                    raise RuntimeError(f"Проверка {index} не прошла: check-{index}.log")
            sys.path.insert(0, str(ROOT))
            report["quarter_workflow"] = quarter_workflow(folder)
            report["status"] = "passed"
            report["windows_verified"] = platform.system() == "Windows"
        except Exception as error:
            report["error"] = f"{type(error).__name__}: {error}"
    output = folder / "report.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{report['status']}: {output}")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
