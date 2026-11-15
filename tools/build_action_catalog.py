"""Build web/actions/catalog.json from the webm action clips.

The clips are 10 s VP9-alpha videos named after the action ("待机呼吸休闲.webm").
There is no pre-generated duration list, so we read the WebM EBML header here:
no ffmpeg, no extra dependency.

Run: python tools/build_action_catalog.py [--check]
"""

import argparse
import json
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLIP_DIR = ROOT / "webm"
CATALOG_FILE = ROOT / "web" / "actions" / "catalog.json"

# Legacy action ids the LLM still emits; each points at one concrete clip.
ALIASES = {
    "idle": "待机呼吸休闲",
    "welcome_wave": "点击回应-元气挥手",
    "happy_jump": "点击回应-开心跃动",
    "shy": "点击回应-害羞惊讶",
    "grumpy": "傲娇生气",
    "laugh": "点击回应-挠痒咯咯笑",
    "thinking": "深度思考碎碎念",
    "busy": "写代码",
    "celebrate": "工作状态-雀跃庆祝",
    "bored": "哈欠连天",
    "surprised": "被吓一跳",
    "gift": "拆礼物",
    "sleepy": "原地小憩沉眠",
}

# Weighted random pool. Order matters: a clip claimed by an earlier category is
# not repeated in a later one (weights stay meaningful).
CATEGORIES = [
    ("小动作", 28, [
        "悠闲哼歌", "超大伸懒腰", "原地敲击桌面互动", "原地重力下蹲压缩", "哈欠连天",
        "原地小憩沉眠", "女仆屈膝礼仪", "被吓一跳", "小幅度原地360度旋转展示",
        "偷吃零食被抓住", "用鲸鱼尾巴拍打地面", "打瞌睡被惊醒", "照镜子", "整体换装试色",
        "轻快记录", "写代码", "摇扇纳凉", "晨间刷牙", "半身转向踢腿", "傲娇生气",
        "吉祥物-哈气-dshwe", "吉祥物-跳抓眩晕落地起身-dshwe",
    ]),
    ("玩耍", 24, [
        "原地专心玩魔方", "原地蹲下玩玩具汽车", "鲸鱼吐泡泡特效", "原地跳跃抓碎头顶物品",
        "玩游戏气急败坏", "玩水枪", "小提琴演奏", "蓝鲸现世", "优雅女仆舞", "轻快摇摆舞",
        "可爱宅舞", "吹气球", "动物环绕", "放风筝", "拆礼物", "变鸽子", "扑克魔术",
        "抽陀螺", "吹笛子", "蝴蝶蜜蜂环绕头顶开花", "撸猫", "凭空生花", "骑木马",
        "三球抛接", "踢毽子", "下五子棋", "荡秋千",
    ]),
    ("吃什么", 18, [
        "吃白饭", "大口吃零食", "吃Token", "吃早餐", "吃午餐", "吃晚餐", "吃冰淇淋融化",
        "吃大闸蟹", "吃糖葫芦", "吃长寿面", "吃西瓜", "涮火锅", "吃饭·碗饭", "吃饭团",
        "吃饺子", "吃汤圆", "吃年糕", "吃青团", "吃腊八粥", "吃重阳糕", "吃粽子",
    ]),
    ("时节", 14, [
        "被落叶淹没", "中秋赏月吃月饼", "堆雪人", "放烟花", "收红包", "写福字", "穿针乞巧",
        "舞狮头", "讨糖南瓜灯", "插茱萸赏菊", "放河灯", "萌化小幽灵", "装点圣诞树",
        "放孔明灯",
    ]),
    ("文字", 6, ["是啊，吃什么", "深度思考碎碎念"]),
]

EVENTS = {
    "gift": ["拆礼物", "凭空生花", "放烟花"],
    "follow": ["工作状态-雀跃庆祝"],
    "enter": ["点击回应-元气挥手"],
    "whisper": ["碎碎念-擦桌碎碎念", "碎碎念-发呆碎碎念", "碎碎念-对屏碎碎念"],
    "balance": [
        "余额-钱袋满溢", "余额-金袋叮当", "余额-钱袋如常",
        "余额-数金皱眉", "余额-袋空如洗", "余额-分文不剩",
    ],
    "workStatus": [
        "工作状态-思考冒泡", "工作状态-忙碌点按", "工作状态-清点归档",
        "工作状态-原地踱步张望", "工作状态-雀跃庆祝", "工作状态-垂头叹气冒汗",
    ],
}

CLICKS = [
    "点击回应-开心跃动", "点击回应-害羞惊讶", "点击回应-挠痒咯咯笑", "点击回应-元气挥手",
]

SLEEP = {"start": ["睡觉-开始"], "loop": ["睡觉-循环"], "end": ["睡觉-起来"]}

IDLE_WEIGHT = 10


def read_id(data, offset):
    first = data[offset]
    length = 1
    mask = 0x80
    while length <= 8 and not (first & mask):
        mask >>= 1
        length += 1
    if length > 8:
        raise ValueError("bad element id")
    value = 0
    for index in range(length):
        value = (value << 8) | data[offset + index]
    return value, offset + length


def read_size(data, offset):
    first = data[offset]
    length = 1
    mask = 0x80
    while length <= 8 and not (first & mask):
        mask >>= 1
        length += 1
    if length > 8:
        raise ValueError("bad element size")
    value = first & (mask - 1)
    for index in range(1, length):
        value = (value << 8) | data[offset + index]
    return value, offset + length, value == (1 << (7 * length)) - 1


CONTAINERS = {0x18538067, 0x1549A966, 0x1654AE6B, 0xAE, 0xE0}


def parse_ebml(data, start, end, out):
    offset = start
    while offset < end:
        try:
            element, offset = read_id(data, offset)
            size, offset, unknown = read_size(data, offset)
        except (IndexError, ValueError):
            return
        next_offset = end if unknown else min(offset + size, end)
        if element in CONTAINERS:
            parse_ebml(data, offset, next_offset, out)
        elif element == 0x2AD7B1:
            out["timecode_scale"] = int.from_bytes(data[offset:next_offset], "big")
        elif element == 0x4489:
            raw = data[offset:next_offset]
            fmt = ">f" if len(raw) == 4 else ">d"
            if len(raw) in (4, 8):
                out["duration"] = struct.unpack(fmt, raw)[0]
        elif element == 0xB0:
            out["width"] = int.from_bytes(data[offset:next_offset], "big")
        elif element == 0xBA:
            out["height"] = int.from_bytes(data[offset:next_offset], "big")
        offset = next_offset


def probe(path):
    data = path.read_bytes()
    info = {}
    parse_ebml(data, 0, len(data), info)
    scale = info.get("timecode_scale", 1_000_000) or 1_000_000
    duration = round(info.get("duration", 0) * scale / 1e9, 2)
    return {
        "duration": duration or 10.0,
        "file": path.name,
        "width": info.get("width"),
        "height": info.get("height"),
    }


def build():
    if not CLIP_DIR.is_dir():
        raise SystemExit(f"clip directory not found: {CLIP_DIR}")
    clips = {}
    for path in sorted(CLIP_DIR.glob("*.webm")):
        clips[path.stem] = probe(path)

    missing = []

    def take(name):
        if name not in clips:
            missing.append(name)
        return name

    categories = []
    claimed = set()
    for category_id, weight, names in CATEGORIES:
        actions = []
        for name in names:
            if name in claimed:
                continue
            take(name)
            claimed.add(name)
            actions.append(name)
        categories.append({"id": category_id, "weight": weight, "actions": actions})

    events = {key: [take(name) for name in names] for key, names in EVENTS.items()}
    events["sleep"] = {key: [take(name) for name in names] for key, names in SLEEP.items()}

    aliases = {key: take(name) for key, name in ALIASES.items()}
    clicks = [take(name) for name in CLICKS]

    weights = {
        "idle": IDLE_WEIGHT,
        "categories": sum(item["weight"] for item in categories),
    }

    catalog = {
        "version": 1,
        "clip": {
            "width": next((item["width"] for item in clips.values() if item["width"]), 640),
            "height": next((item["height"] for item in clips.values() if item["height"]), 360),
        },
        "clips": clips,
        "aliases": aliases,
        "clicks": clicks,
        "categories": categories,
        "events": events,
        "weights": weights,
    }
    if missing:
        catalog["missing"] = sorted(set(missing))
    return catalog


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail if the catalog is stale")
    args = parser.parse_args()

    catalog = build()
    text = json.dumps(catalog, ensure_ascii=False, indent=2) + "\n"
    if args.check:
        current = CATALOG_FILE.read_text(encoding="utf-8") if CATALOG_FILE.exists() else ""
        if current != text:
            print("catalog.json is stale; run python tools/build_action_catalog.py")
            return 1
        print(f"catalog ok: {len(catalog['clips'])} clips")
        return 0

    CATALOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CATALOG_FILE.write_text(text, encoding="utf-8")
    missing = catalog.get("missing") or []
    print(f"wrote {CATALOG_FILE.relative_to(ROOT)}: {len(catalog['clips'])} clips")
    if missing:
        print("missing clips: " + ", ".join(missing))
    return 0


if __name__ == "__main__":
    sys.exit(main())
