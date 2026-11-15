"""Build a clean, shareable copy of Pet Live Studio.

The result is ``dist/pet-live-studio/`` plus a zip next to it. Runtime state
(``runtime/``) is never copied -- it holds login credentials, music and
backgrounds -- and the 13 legacy webp animations are dropped except the idle
frame, because the webm action library replaces them.

Usage:
    python tools/make_dist.py                # build folder + zip
    python tools/make_dist.py --all-webp     # also keep the legacy webp set
"""

import argparse
import shutil
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
NAME = "pet-live-studio"

INCLUDE_FILES = [
    "app.py",
    "start.py",
    "requirements.txt",
    "run.bat",
    "run.sh",
    "run.command",
    "Dockerfile",
    "docker-compose.yml",
    ".dockerignore",
    ".env.example",
    "LICENSE",
    "README.md",
]
INCLUDE_DIRS = ["core", "adapters", "vendor", "web", "webm", "tools"]

SKIP_DIRS = {"__pycache__", ".venv", "runtime", "dist", "tests", ".git", ".pytest_cache"}
SKIP_SUFFIXES = {".pyc", ".pyo"}

QUICKSTART = """# Pet Live Studio · 快速开始

一个在本机跑起来、用 OBS 当浏览器源的互动直播宠物：119 段动作、作息、
状态养成、弹幕指令、点歌，都能离线工作。

## 你需要

- **Python 3.10 或更新**（Windows 安装时勾选 *pip* 和 *Add python.exe to PATH*）
- 可选：一个 OpenAI 兼容的大模型服务（Ollama / llama.cpp / 云端 API），用来让宠物说话
- 可选：OpenAI 兼容的 TTS 服务；没有就用浏览器自带语音，或者干脆不发声

## 一键启动

| 系统 | 操作 |
|---|---|
| Windows | 双击 `run.bat` |
| macOS | 双击 `run.command`（首次若没反应，在终端跑 `chmod +x run.command`） |
| Linux / macOS 终端 | `./run.sh` |

首次运行会自动创建 `.venv`、安装依赖（约 1–3 分钟），然后打开控制台：

- 舞台（给 OBS 用）：http://127.0.0.1:8775/
- 控制台：http://127.0.0.1:8775/control/

端口被占用时：`python start.py --port 8780`。

## 接进 OBS

1. 添加「浏览器」源，地址填 `http://127.0.0.1:8775/`
2. 宽高设 1920×1080
3. **勾选「控制音频」**，否则听不到宠物说话和背景音乐

## 没有大模型能不能用？

能。动作、作息、喂食/陪玩/摸头、点歌、睡觉，全部在本地，不需要模型。
只有「说话」需要模型：在控制台「模型 API」里填地址即可。

## 常用设置

- 控制台里改的设置存在 `runtime/settings.json`
- 想用环境变量：复制 `.env.example` 为 `.env` 再改
- 想完全重置：删掉 `runtime/` 目录（设置、记忆、音乐、背景都会清空）

## 目录说明

```
webm/            119 段动作片段（VP9-alpha，10 秒/段）
web/actions/     动作索引 catalog.json（权重、别名、事件档位）
core/            弹幕、模型、TTS、宠物状态、点歌队列
runtime/         运行时数据，首次启动自动生成
```

## 素材授权

**公开分发或商用前请先读 `ASSETS-NOTICE.md`。**
"""

ASSETS_NOTICE = """# 素材与授权说明

- **代码**：MIT，见 `LICENSE`。
- **动作片段 `webm/` 与角色形象**：来源未确认。

这批素材移植自第三方桌面宠物项目 `dsh-pet-main`。该项目目录中**没有附带
LICENSE 文件**，角色形象（蓝发女仆）的著作权归属也无法从仓库判断。

因此：

| 用途 | 建议 |
|---|---|
| 自己本地玩、内部测试 | 没有问题 |
| 公开分发、商用、公开直播 | 先确认授权，或替换成自己的素材 |

**替换素材**：把新片段（约 10 秒、640×360、VP9-alpha 的 webm）放进 `webm/`，
再运行：

```bash
python tools/build_action_catalog.py
```

索引会重新生成。动作名即文件名；要参与随机动作，在
`tools/build_action_catalog.py` 的分类列表里加一行。

B 站弹幕依赖 blivedm（MIT），见 `vendor/NOTICE.txt`。
抖音接入依赖外部 DyHub，本项目不打包其代码。
"""


def keep(path: Path, all_webp: bool) -> bool:
    if any(part in SKIP_DIRS for part in path.parts):
        return False
    if path.suffix.lower() in SKIP_SUFFIXES:
        return False
    rel = path.relative_to(ROOT).as_posix()
    if rel.startswith("web/assets/animations/") and path.suffix.lower() == ".webp":
        return all_webp or path.name == "idle.webp"
    return True


def copy_project(target: Path, all_webp: bool) -> int:
    copied = 0
    for name in INCLUDE_FILES:
        source = ROOT / name
        if not source.is_file():
            print(f"  skip missing: {name}")
            continue
        (target / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target / name)
        copied += 1
    for name in INCLUDE_DIRS:
        source = ROOT / name
        if not source.is_dir():
            print(f"  skip missing dir: {name}")
            continue
        for item in source.rglob("*"):
            if not item.is_file() or not keep(item, all_webp):
                continue
            destination = target / item.relative_to(ROOT)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, destination)
            copied += 1
    return copied


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all-webp", action="store_true", help="include the legacy webp animations")
    parser.add_argument("--no-zip", action="store_true", help="only build the folder")
    args = parser.parse_args()

    target = DIST / NAME
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)

    copied = copy_project(target, args.all_webp)

    (target / "runtime").mkdir(exist_ok=True)
    (target / "runtime" / ".gitkeep").write_text("", encoding="utf-8")
    (target / "QUICKSTART.md").write_text(QUICKSTART, encoding="utf-8")
    (target / "ASSETS-NOTICE.md").write_text(ASSETS_NOTICE, encoding="utf-8")

    size = sum(item.stat().st_size for item in target.rglob("*") if item.is_file())
    print(f"folder: {target} ({copied} files, {size / 1024 / 1024:.1f} MB)")

    if args.no_zip:
        return 0
    stamp = time.strftime("%Y%m%d")
    archive = DIST / f"{NAME}-{stamp}.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for item in sorted(target.rglob("*")):
            if item.is_file():
                bundle.write(item, Path(NAME) / item.relative_to(target))
    print(f"zip:    {archive} ({archive.stat().st_size / 1024 / 1024:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
