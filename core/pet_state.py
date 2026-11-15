"""宠物状态：饱食 / 心情 / 精力 / 亲密度。

数值按时间自然衰减（不跑后台定时器，读的时候按 updated_at 补算），
观众用弹幕「喂食 / 陪玩 / 哄睡 / 摸头」改变数值。状态持久化到
``runtime/pet_state.json``，重启后接着养。
"""

import json
import time
from pathlib import Path

STAT_KEYS = ("hunger", "mood", "energy", "bond")
DEFAULTS = {"hunger": 70.0, "mood": 70.0, "energy": 80.0, "bond": 0.0}

# 每分钟自然衰减；心情会慢慢回到 MOOD_TARGET，亲密度不衰减。
DECAY_PER_MINUTE = {"hunger": 1 / 8, "energy": 1 / 10}
MOOD_TARGET = 60.0
MOOD_PULL_PER_MINUTE = 1 / 60

# 互动带来的变化。clip 取自动作库，text 是给观众看的一句话气泡。
CARE = {
    "feed": {
        "label": "喂食",
        "clip": "吃白饭",
        "text": "谢谢投喂，我开动了！",
        "delta": {"hunger": 18, "mood": 3},
        "cooldown": 30,
    },
    "play": {
        "label": "陪玩",
        "clip": "三球抛接",
        "text": "来玩！接住啦～",
        "delta": {"mood": 12, "energy": -6, "hunger": -2},
        "cooldown": 45,
    },
    "sleep": {
        "label": "哄睡",
        "clip": "睡觉-开始",
        "text": "那我先睡一会儿……晚安。",
        "delta": {"energy": 16, "mood": 4},
        "cooldown": 120,
    },
    "pat": {
        "label": "摸头",
        "clip": "点击回应-害羞惊讶",
        "text": "唔……别摸啦。",
        "delta": {"mood": 6, "bond": 1},
        "cooldown": 15,
    },
}

LEVELS = (
    (120, "家人"),
    (60, "朋友"),
    (25, "熟悉"),
    (0, "陌生"),
)


def clamp(value, low=0.0, high=100.0):
    return max(low, min(high, float(value)))


class PetState:
    def __init__(self, path):
        self.path = Path(path)
        self.hunger = DEFAULTS["hunger"]
        self.mood = DEFAULTS["mood"]
        self.energy = DEFAULTS["energy"]
        self.bond = DEFAULTS["bond"]
        self.updated_at = time.time()
        self.care_count = 0
        self._cooldowns = {}
        self.load()

    # ------------------------------------------------------------------ 持久化

    def load(self):
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(raw, dict):
            return
        for key in STAT_KEYS:
            try:
                setattr(self, key, clamp(raw.get(key, DEFAULTS[key]), 0, 1000 if key == "bond" else 100))
            except (TypeError, ValueError):
                setattr(self, key, DEFAULTS[key])
        try:
            self.updated_at = float(raw.get("updated_at") or time.time())
        except (TypeError, ValueError):
            self.updated_at = time.time()
        try:
            self.care_count = int(raw.get("care_count") or 0)
        except (TypeError, ValueError):
            self.care_count = 0

    def save(self):
        payload = {
            "hunger": round(self.hunger, 2),
            "mood": round(self.mood, 2),
            "energy": round(self.energy, 2),
            "bond": round(self.bond, 2),
            "care_count": self.care_count,
            "updated_at": self.updated_at,
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(self.path)
        except OSError:
            pass

    # ------------------------------------------------------------------- 衰减

    def settle(self, now=None):
        """按经过的时间补算衰减；返回是否发生变化。"""
        now = time.time() if now is None else now
        elapsed = max(0.0, now - self.updated_at) / 60.0
        if elapsed <= 0:
            return False
        self.hunger = clamp(self.hunger - DECAY_PER_MINUTE["hunger"] * elapsed)
        self.energy = clamp(self.energy - DECAY_PER_MINUTE["energy"] * elapsed)
        mood_gap = MOOD_TARGET - self.mood
        self.mood = clamp(self.mood + mood_gap * min(1.0, MOOD_PULL_PER_MINUTE * elapsed))
        self.updated_at = now
        return True

    # ------------------------------------------------------------------- 互动

    def apply_care(self, command, user_key="", now=None):
        """执行一次照料。返回 {ok, ...}；冷却中返回 ok=False 和剩余秒数。"""
        spec = CARE.get(command)
        if spec is None:
            return {"ok": False, "reason": "unknown"}
        now = time.time() if now is None else now
        self.settle(now)
        key = (user_key or "", command)
        ready_at = self._cooldowns.get(key, 0)
        if now < ready_at:
            return {"ok": False, "reason": "cooldown", "wait": round(ready_at - now, 1)}
        self._cooldowns[key] = now + spec["cooldown"]
        for stat, delta in spec["delta"].items():
            current = getattr(self, stat)
            upper = 1000 if stat == "bond" else 100
            setattr(self, stat, clamp(current + delta, 0, upper))
        self.care_count += 1
        self.save()
        return {
            "ok": True,
            "label": spec["label"],
            "clip": spec["clip"],
            "text": spec["text"],
            "state": self.snapshot(now),
        }

    # ------------------------------------------------------------------- 快照

    @property
    def level(self):
        for threshold, name in LEVELS:
            if self.bond >= threshold:
                return name
        return LEVELS[-1][1]

    @property
    def status(self):
        if self.energy < 20:
            return "sleepy"
        if self.hunger < 25:
            return "hungry"
        if self.mood < 30:
            return "sad"
        if self.mood > 80:
            return "happy"
        return "normal"

    def snapshot(self, now=None):
        self.settle(now)
        return {
            "hunger": round(self.hunger, 1),
            "mood": round(self.mood, 1),
            "energy": round(self.energy, 1),
            "bond": round(self.bond, 1),
            "level": self.level,
            "status": self.status,
            "care_count": self.care_count,
            "updated_at": self.updated_at,
        }
