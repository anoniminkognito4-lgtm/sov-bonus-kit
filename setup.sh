#!/bin/sh
set -eu
sov_app_dir=$(CDPATH= cd "$(dirname "$0")" && pwd)
cd "$sov_app_dir"
if command -v uv >/dev/null 2>&1; then
  sov_uv=uv
elif [ -x .tools/uv/bin/uv ]; then
  sov_uv=.tools/uv/bin/uv
else
  echo 'Установите uv по инструкции docs/WINDOWS.md, затем повторите настройку.' >&2
  exit 1
fi
export PYTHONUTF8=1
"$sov_uv" sync --locked --python 3.12
printf '%s\n' 'Установка завершена. Запуск: sh run.sh'
