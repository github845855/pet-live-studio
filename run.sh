#!/usr/bin/env sh
# macOS / Linux launcher. The first run creates .venv and installs dependencies.
set -eu
cd "$(dirname "$0")"

if command -v python3 >/dev/null 2>&1; then
  PYTHON=python3
elif command -v python >/dev/null 2>&1; then
  PYTHON=python
else
  echo "未找到 Python 3。请先安装 Python 3.10 或更新版本：https://www.python.org/downloads/"
  exit 1
fi

exec "$PYTHON" start.py "$@"
