"""Архив для скачивания: всё для запуска, без баз, окружения и внутренней методологии."""

import stat
from zipfile import ZipFile

from conftest import ROOT

from tools.package_release import package


def test_release_archive_contents(tmp_path):
    archive = tmp_path / "sov.zip"
    package(archive)
    with ZipFile(archive) as source:
        names = set(source.namelist())
        modes = {i.filename: i.external_attr >> 16 for i in source.infolist()}
    prefix = "sov-bonus-kit/"
    for required in (
        "run.sh",
        "run.bat",
        "run.command",
        "demo.command",
        "demo.bat",
        "uv.lock",
        "README.md",
    ):
        assert prefix + required in names
    assert prefix + "app/main.py" in names and prefix + "samples/milestones/ms_seed.xlsx" in names
    assert not any(n.startswith(prefix + "docs/methodology") for n in names)
    assert not any(n.endswith((".sqlite", ".db", ".xlsm")) or "/.venv/" in n for n in names)
    for script in ("run.sh", "run.command", "demo.command"):
        assert modes[prefix + script] & stat.S_IXUSR, script  # запускается двойным щелчком
    assert (ROOT / "run.bat").read_bytes().count(b"\r\n") > 10  # CRLF для Windows
