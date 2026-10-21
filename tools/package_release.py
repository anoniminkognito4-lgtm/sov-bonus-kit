"""Упаковка переносимого комплекта без окружения, БД, локальных кешей и внутренней методологии.

`docs/methodology` — внутренний документ компании (README): в архив для скачивания не входит.
"""

import argparse
import hashlib
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
FOLDERS = ("src", "app", "config", "docs", "tests", "samples", "tools", "reference", ".streamlit")
FILES = (
    "README.md",
    "CLAUDE.md",
    "pyproject.toml",
    "uv.lock",
    ".python-version",
    ".gitattributes",
    "run.bat",
    "setup.bat",
    "check.bat",
    "run.sh",
    "setup.sh",
    "run.command",
    "demo.command",
    "demo.bat",
)
# Внутренние документы и локальные данные — никогда в архив.
EXCLUDED = ("docs/methodology",)


def package(output):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    inputs = [ROOT / name for name in FILES]
    for name in FOLDERS:
        inputs.extend(
            p
            for p in (ROOT / name).rglob("*")
            if p.is_file()
            and "__pycache__" not in p.parts
            and p.suffix.lower() not in (".pyc", ".sqlite", ".db", ".xlsm", ".xlsb")
            and not any(p.relative_to(ROOT).as_posix().startswith(e) for e in EXCLUDED)
        )
    with ZipFile(output, "w", ZIP_DEFLATED) as archive:
        for path in sorted(inputs):
            archive.write(path, "sov-bonus-kit/" + path.relative_to(ROOT).as_posix())
    return hashlib.sha256(output.read_bytes()).hexdigest()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "output", nargs="?", type=Path, default=ROOT / "build/sov-bonus-kit-portable.zip"
    )
    args = parser.parse_args()
    print(f"{args.output}: SHA-256 {package(args.output)}")
