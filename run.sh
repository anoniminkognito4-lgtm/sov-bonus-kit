#!/bin/sh
# Запуск панели СОВ на Mac одной командой: sh run.sh (рабочая база) или sh run.sh demo.
# При первом запуске ставит зависимости через uv (нужен интернет один раз).
set -eu
sov_app_dir=$(CDPATH= cd "$(dirname "$0")" && pwd)
cd "$sov_app_dir"
export PYTHONUTF8=1

if [ ! -x .venv/bin/python ]; then
  sov_uv=""
  for candidate in uv "$HOME/.local/bin/uv" "$HOME/.cargo/bin/uv" /opt/homebrew/bin/uv /usr/local/bin/uv; do
    if command -v "$candidate" >/dev/null 2>&1; then sov_uv=$candidate; break; fi
  done
  if [ -z "$sov_uv" ]; then
    echo 'Не найден uv. Установите его командой:' >&2
    echo '  curl -LsSf https://astral.sh/uv/install.sh | sh' >&2
    echo 'затем закройте и снова откройте Терминал и повторите: sh run.sh' >&2
    exit 1
  fi
  echo 'Первый запуск: устанавливаю зависимости (несколько минут, нужен интернет)…'
  "$sov_uv" sync --locked --python 3.12
fi

export UV_OFFLINE=1
mode=${1:-}
if [ "$mode" = demo ]; then
  shift
  export SOV_DEMO=1
  export SOV_DB_PATH="$sov_app_dir/build/demo/sov.sqlite"
  echo 'Демо-режим: отдельная база build/demo/sov.sqlite с условными данными.'
else
  export SOV_DB_PATH="${SOV_DB_PATH:-$sov_app_dir/build/sov.sqlite}"
fi
echo 'Панель откроется в браузере: http://localhost:8501  (остановить: Ctrl+C)'
# Браузер открывает скрипт: без вопроса Streamlit о почте при первом запуске.
if command -v open >/dev/null 2>&1; then
  (sleep 4; open http://localhost:8501) >/dev/null 2>&1 &
fi
exec .venv/bin/python -m streamlit run app/main.py --server.headless true "$@"
