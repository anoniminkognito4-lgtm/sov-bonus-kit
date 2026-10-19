#!/bin/sh
set -eu
sov_app_dir=$(CDPATH= cd "$(dirname "$0")" && pwd)
cd "$sov_app_dir"
if [ ! -x .venv/bin/python ]; then
  echo 'Окружение не найдено. Сначала выполните: sh setup.sh' >&2
  exit 1
fi
export PYTHONUTF8=1
export UV_OFFLINE=1
exec .venv/bin/python -m streamlit run app/main.py "$@"
