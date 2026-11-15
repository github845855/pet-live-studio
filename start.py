#!/usr/bin/env python3
"""Cross-platform launcher for Pet Live Studio.

Run this with any Python 3.10+ interpreter::

    python start.py              # Windows
    python3 start.py             # macOS / Linux
    ./run.sh                     # macOS / Linux convenience wrapper

It creates a local virtual environment on first run, installs the runtime
dependencies, starts the studio and opens the control console in the browser.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
import venv
import webbrowser
from pathlib import Path

from core.net import is_lan_bind_host

ROOT = Path(__file__).resolve().parent
VENV_DIR = ROOT / ".venv"
REQUIREMENTS = ROOT / "requirements.txt"
MARKER = VENV_DIR / ".requirements.sha256"
MIN_PYTHON = (3, 10)
DEFAULT_PORT = 8775

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass


def venv_python() -> Path:
    if os.name == "nt":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def echo(message: str = "") -> None:
    print(message, flush=True)


def read_env_file() -> dict[str, str]:
    values = {}
    path = ROOT / ".env"
    if not path.is_file():
        return values
    try:
        for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                values[key] = value
    except OSError:
        pass
    return values


def requirements_hash() -> str:
    digest = hashlib.sha256()
    try:
        digest.update(REQUIREMENTS.read_bytes())
    except OSError:
        pass
    digest.update(f"{sys.version_info.major}.{sys.version_info.minor}".encode())
    return digest.hexdigest()


def build_env(port: int, host: str) -> dict[str, str]:
    env = os.environ.copy()
    env["PORT"] = str(port)
    env["HOST"] = host
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    if os.name == "nt":
        # Some Windows setups run project-local interpreters at low integrity,
        # which cannot write to the system temp folder. Keep pip/app temp files
        # inside the project so installs and caches do not hang.
        temp = ROOT / ".venvtmp"
        temp.mkdir(parents=True, exist_ok=True)
        env["TEMP"] = str(temp)
        env["TMP"] = str(temp)
    return env


def explain_venv_failure(exc: Exception) -> None:
    echo("")
    echo("创建虚拟环境失败 / Could not create the virtual environment")
    echo(f"  {type(exc).__name__}: {exc}")
    echo("")
    echo("请先安装完整的 Python（包含 venv 和 pip）：")
    echo("  Windows   重新安装 python.org 的 Python，勾选 pip 与 Add to PATH")
    echo("  Ubuntu    sudo apt install python3 python3-venv python3-pip")
    echo("  Fedora    sudo dnf install python3 python3-pip")
    echo("  macOS     brew install python  或安装 python.org 版本")
    echo("")
    echo("如果系统 Python 不方便改动，也可以手动创建环境：")
    echo("  python -m venv .venv")
    echo("  .venv\\Scripts\\pip install -r requirements.txt   (Windows)")
    echo("  .venv/bin/pip install -r requirements.txt       (macOS/Linux)")


def ensure_venv(no_install: bool) -> Path:
    python = venv_python()
    if sys.prefix != sys.base_prefix and Path(sys.executable) == python:
        # Already running inside the project venv.
        if not no_install:
            install_requirements(python)
        return python
    if not python.exists():
        echo("首次运行：正在创建本地 Python 环境 .venv …")
        try:
            venv.EnvBuilder(with_pip=True, clear=False, upgrade_deps=False).create(VENV_DIR)
        except Exception as exc:  # noqa: BLE001 - show a helpful message
            explain_venv_failure(exc)
            raise SystemExit(1) from exc
    if not no_install:
        install_requirements(python)
    return python


def install_requirements(python: Path) -> None:
    current = requirements_hash()
    try:
        if MARKER.is_file() and MARKER.read_text(encoding="utf-8").strip() == current:
            return
    except OSError:
        pass
    probe = subprocess.run(
        [str(python), "-c", "import aiohttp, dotenv, brotli, pure_protobuf, yarl"],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if probe.returncode == 0:
        MARKER.write_text(current, encoding="utf-8")
        return
    echo("正在检查并安装依赖（首次运行需要几分钟）…")
    command = [
        str(python), "-m", "pip", "install",
        "--disable-pip-version-check", "--no-input",
        "-r", str(REQUIREMENTS),
    ]
    try:
        result = subprocess.run(command, cwd=ROOT, check=False)
    except OSError as exc:
        explain_venv_failure(exc)
        raise SystemExit(1) from exc
    if result.returncode != 0:
        echo("")
        echo(f"依赖安装失败（pip 退出码 {result.returncode}）。")
        echo("请检查网络或代理后再运行一次；也可以手动执行：")
        echo(f"  {python} -m pip install -r requirements.txt")
        raise SystemExit(1)
    MARKER.write_text(current, encoding="utf-8")


def wait_for_server(host: str, port: int, process: subprocess.Popen, timeout: float = 40) -> bool:
    probe_host = "127.0.0.1" if host in {"localhost", "::1", "0.0.0.0", "::"} else host
    url = f"http://{probe_host}:{port}/health"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(url, timeout=1) as response:
                if response.status == 200:
                    return True
        except (urllib.error.URLError, OSError, ValueError):
            pass
        time.sleep(0.35)
    return process.poll() is None


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        prog="start.py",
        description="Start the Pet Live Studio stage and control console.",
    )
    parser.add_argument("--port", type=int, help=f"HTTP/WebSocket port (default {DEFAULT_PORT})")
    parser.add_argument("--host", default=None, help="Bind host (0.0.0.0 for LAN; default 127.0.0.1)")
    parser.add_argument("--no-browser", action="store_true", help="Do not open the browser")
    parser.add_argument("--no-install", action="store_true", help="Skip the dependency check")
    parser.add_argument("--check", action="store_true", help="Only prepare the environment, do not start")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if sys.version_info < MIN_PYTHON:
        echo("需要 Python 3.10 或更新版本；当前版本："
             f"{sys.version_info.major}.{sys.version_info.minor}")
        echo("https://www.python.org/downloads/")
        return 1

    file_env = read_env_file()
    host = (args.host or os.getenv("HOST") or file_env.get("HOST") or "127.0.0.1").strip()
    if not is_lan_bind_host(host):
        echo(f"HOST 必须是 0.0.0.0、回环地址或局域网私有地址，收到：{host}")
        return 1
    raw_port = args.port or os.getenv("PORT") or file_env.get("PORT") or DEFAULT_PORT
    try:
        port = int(raw_port)
    except (TypeError, ValueError):
        echo(f"PORT 不是有效数字：{raw_port}")
        return 1
    if not 1024 <= port <= 65535:
        echo("PORT 必须在 1024 到 65535 之间")
        return 1

    python = ensure_venv(args.no_install)
    if args.check:
        echo(f"环境已就绪：{python}")
        return 0

    env = build_env(port, host)
    address = "127.0.0.1" if host in {"localhost", "::1", "0.0.0.0", "::"} else host
    stage_url = f"http://{address}:{port}/"
    control_url = f"http://{address}:{port}/control/"

    echo("")
    echo("  Pet Live Studio")
    echo(f"  舞台 / OBS 浏览器源   {stage_url}")
    echo(f"  控制台                {control_url}")
    echo("  按 Ctrl+C 停止")
    echo("")

    process = subprocess.Popen([str(python), str(ROOT / "app.py")], cwd=ROOT, env=env)
    try:
        ready = wait_for_server(host, port, process)
        if not ready:
            code = process.poll()
            if code is None:
                echo("服务启动超时。请检查端口占用或查看上面的日志。")
                process.terminate()
                return 1
            if code != 0:
                echo("")
                echo(f"服务启动失败（退出码 {code}）。")
                if code == 1:
                    echo("常见原因：端口已被占用，试试  python start.py --port 8780")
                return code
        if ready and not args.no_browser:
            webbrowser.open(control_url)
        return process.wait()
    except KeyboardInterrupt:
        echo("")
        echo("正在停止 …")
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
