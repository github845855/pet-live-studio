"""Build the web animation set from the extracted source frames.

The source material in ``anim/`` is 640x360 lossless WebP frames.  The web
build used to be a 480x270 lossy re-encode, which is why the pet looked soft
once the stage scaled it up.  This script re-encodes the same frames at a
higher resolution with a mild sharpen pass, and writes a manifest the stage
can use for exact action durations.

Usage (after ``pip install pillow``)::

    python tools/build_animations.py                 # all actions
    python tools/build_animations.py --only idle     # one action
    python tools/build_animations.py --force         # ignore freshness check

The script is intentionally dependency-light: only Pillow is required, and it
is *not* needed to run the live app.  Re-run it only when you change the
source frames or want a different resolution/quality.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from pathlib import Path

try:
    from PIL import Image, ImageFilter
except ImportError:  # pragma: no cover - friendly message
    sys.exit("Pillow is required to rebuild animations: python -m pip install pillow")


ROOT = Path(__file__).resolve().parent.parent
ANIM_ROOT = ROOT / "anim"
OUTPUT_DIR = ROOT / "web" / "assets" / "animations"
MANIFEST = OUTPUT_DIR / "manifest.json"

# action id -> source folder (relative to anim/)
SOURCES = {
    "idle": "idle/待机呼吸休闲",
    "welcome_wave": "click/点击回应-元气挥手",
    "happy_jump": "click/点击回应-开心跃动",
    "shy": "click/点击回应-害羞惊讶",
    "grumpy": "click/点击回应-傲娇生气",
    "laugh": "click/点击回应-挠痒咯咯笑",
    "thinking": "random/工作状态-思考冒泡",
    "busy": "random/工作状态-忙碌点按",
    "celebrate": "random/工作状态-雀跃庆祝",
    "bored": "random/碎碎念-发呆碎碎念",
    "surprised": "random/被吓一跳",
    "gift": "random/拆礼物",
    "sleepy": "random/打瞌睡被惊醒",
}

FRAME_MS = 42  # 24 fps, matching the original source material


def log(message: str) -> None:
    print(message, flush=True)


def frame_files(folder: Path) -> list[Path]:
    files = sorted(folder.glob("f_*.webp"))
    if not files:
        raise SystemExit(f"No frames found in {folder}")
    return files


def build_one(
    action: str,
    folder: Path,
    width: int,
    quality: int,
    sharpen: int,
    force: bool,
) -> dict:
    files = frame_files(folder)
    target = OUTPUT_DIR / f"{action}.webp"
    newest_source = max(path.stat().st_mtime for path in files)
    if not force and target.exists() and target.stat().st_mtime >= newest_source:
        with Image.open(target) as probe:
            already, already_height = probe.size
        if already >= width:
            log(f"  skip {action}: {target.name} is already {already}px wide")
            return {"file": target.name, "width": already, "height": already_height,
                    "frames": len(files), "duration": round(len(files) * FRAME_MS / 1000, 2)}

    started = time.time()
    images = []
    for path in files:
        with Image.open(path) as source:
            frame = source.convert("RGBA")
        if frame.size[0] != width:
            height = round(width * frame.size[1] / frame.size[0])
            frame = frame.resize((width, height), Image.Resampling.LANCZOS)
        else:
            height = frame.size[1]
        if sharpen:
            alpha = frame.getchannel("A")
            frame = frame.convert("RGB").filter(
                ImageFilter.UnsharpMask(radius=1.2, percent=sharpen, threshold=2)
            )
            frame.putalpha(alpha)
        images.append(frame)

    buffer = tempfile.SpooledTemporaryFile(max_size=32 * 1024 * 1024)
    images[0].save(
        buffer,
        format="WEBP",
        save_all=True,
        append_images=images[1:],
        duration=FRAME_MS,
        loop=0,
        quality=quality,
        method=6,
        alpha_quality=90,
    )
    buffer.seek(0)
    temporary = target.with_suffix(".webp.tmp")
    with temporary.open("wb") as handle:
        shutil.copyfileobj(buffer, handle)
    buffer.close()
    temporary.replace(target)

    size_mb = target.stat().st_size / 1024 / 1024
    elapsed = time.time() - started
    log(f"  {action}: {width}x{height}, {len(files)} frames, "
        f"{size_mb:.1f} MB, {elapsed:.1f}s")
    with Image.open(target) as probe:
        return {
            "file": target.name,
            "width": probe.size[0],
            "height": probe.size[1],
            "frames": len(files),
            "duration": round(len(files) * FRAME_MS / 1000, 2),
            "bytes": target.stat().st_size,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Rebuild high-resolution pet animations")
    parser.add_argument("--width", type=int, default=960, help="output width (default 960)")
    parser.add_argument("--quality", type=int, default=80, help="WebP quality (default 80)")
    parser.add_argument("--sharpen", type=int, default=45,
                        help="unsharp amount, 0 disables (default 45)")
    parser.add_argument("--only", action="append", choices=sorted(SOURCES),
                        help="build only the given action(s)")
    parser.add_argument("--force", action="store_true", help="rebuild even if up to date")
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    actions = args.only or list(SOURCES)
    log(f"Building {len(actions)} animation(s) at {args.width}px wide -> {OUTPUT_DIR}")

    manifest = {
        "version": 2,
        "width": args.width,
        "frame_ms": FRAME_MS,
        "generated_at": int(time.time()),
        "actions": {},
    }
    existing = {}
    if MANIFEST.exists():
        try:
            existing = json.loads(MANIFEST.read_text(encoding="utf-8")).get("actions", {})
        except (OSError, json.JSONDecodeError):
            existing = {}

    for action in actions:
        folder = ANIM_ROOT / SOURCES[action]
        if not folder.is_dir():
            raise SystemExit(f"Missing source folder for {action}: {folder}")
        log(f"- {action}")
        manifest["actions"][action] = build_one(
            action, folder, args.width, args.quality, args.sharpen, args.force
        )

    # Keep entries for actions that were not rebuilt in this run.
    for action, info in existing.items():
        manifest["actions"].setdefault(action, info)
    manifest["actions"] = dict(sorted(manifest["actions"].items()))

    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    total = sum(
        (OUTPUT_DIR / info["file"]).stat().st_size
        for info in manifest["actions"].values()
        if (OUTPUT_DIR / info["file"]).exists()
    )
    log(f"Manifest written: {MANIFEST}")
    log(f"Total animation size: {total / 1024 / 1024:.1f} MB")


if __name__ == "__main__":
    main()
