"""Приёмка не выдаёт результат macOS за проверку Windows."""

import json

from tools.acceptance import main


def test_windows_guard_records_actual_platform_and_never_claims_success_on_mac(
    monkeypatch, tmp_path
):
    monkeypatch.setattr("tools.acceptance.platform.system", lambda: "Darwin")
    assert main(["--require-windows", "--output", str(tmp_path)]) == 1
    report = json.loads(next(tmp_path.rglob("report.json")).read_text(encoding="utf-8"))
    assert report["system"] == "Darwin" and report["status"] == "failed"
    assert report["windows_verified"] is False
    assert not report["checks"]
