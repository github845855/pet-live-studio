#!/bin/bash
# macOS 双击启动入口（Finder 中双击 run.command 即可）。
cd "$(dirname "$0")" || exit 1
if command -v python3 >/dev/null 2>&1; then
  PYTHON=python3
elif command -v python >/dev/null 2>&1; then
  PYTHON=python
else
  echo "未找到 Python 3。请先安装 Python 3.10 或更新版本：https://www.python.org/downloads/"
  read -n 1 -s -r -p "按任意键关闭窗口…"
  exit 1
fi
"$PYTHON" start.py "$@"
status=$?
if [ "$status" -ne 0 ]; then
  echo
  echo "启动失败。请把这个窗口里上面的信息发给开发者，或检查 Python 安装。"
  read -n 1 -s -r -p "按任意键关闭窗口…"
fi
exit "$status"
