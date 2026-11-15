"""Short conversation memory for multi-turn replies and continuing stories.

The memory is deliberately small and local: it stores the last few viewer
exchanges plus the pet's replies, so the model can keep a topic or a little
story going without a huge prompt.  Everything is kept in
``runtime/memory.json`` and never leaves the machine.
"""

import json
import logging
import re
import time
import uuid
from pathlib import Path

log = logging.getLogger(__name__)

MAX_EXCHANGES = 80
REPLY_PUNCTUATION = re.compile(r"[\s，。！？、,.!?;；:：\"'“”‘’（）()【】\[\]…—\-]+")


class ConversationMemory:
    def __init__(self, path=None, max_exchanges=MAX_EXCHANGES):
        self.path = Path(path) if path else None
        self.max_exchanges = max(10, int(max_exchanges))
        self.exchanges = []
        self.load()

    def load(self):
        if self.path is None or not self.path.is_file():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Could not read conversation memory (%s); starting fresh",
                        type(exc).__name__)
            return
        items = raw.get("exchanges") if isinstance(raw, dict) else raw
        if not isinstance(items, list):
            return
        cleaned = [self._clean(item) for item in items]
        self.exchanges = [item for item in cleaned if item][-self.max_exchanges:]
        if self._compact_monologues():
            self.save()

    @staticmethod
    def reply_key(text):
        return REPLY_PUNCTUATION.sub("", str(text or "")).lower()

    def _compact_monologues(self):
        """Keep at most two identical monologues so old loops do not poison prompts."""
        counts = {}
        kept = []
        changed = False
        for item in reversed(self.exchanges):
            if item.get("kind") == "monologue":
                key = self.reply_key(item.get("reply"))
                if key and counts.get(key, 0) >= 2:
                    changed = True
                    continue
                if key:
                    counts[key] = counts.get(key, 0) + 1
            kept.append(item)
        if changed:
            self.exchanges = list(reversed(kept))
        return changed

    @staticmethod
    def _clean(item):
        if not isinstance(item, dict):
            return None
        reply = item.get("reply")
        if not isinstance(reply, str) or not reply.strip():
            return None
        try:
            timestamp = float(item.get("ts") or 0)
        except (TypeError, ValueError):
            timestamp = 0
        return {
            "id": str(item.get("id") or uuid.uuid4().hex)[:24],
            "viewer": str(item.get("viewer") or "")[:40],
            "kind": str(item.get("kind") or "danmaku")[:20],
            "text": str(item.get("text") or "")[:300],
            "reply": reply.strip()[:180],
            "action": str(item.get("action") or "idle")[:40],
            "ts": timestamp or time.time(),
        }

    def record(self, event, result):
        """Remember one successful exchange. Returns the stored item."""
        kind = str(event.get("kind") or "danmaku")
        user = event.get("user") or {}
        item = {
            "id": uuid.uuid4().hex[:24],
            "viewer": str(user.get("name") or "")[:40],
            "kind": kind[:20],
            "text": "" if kind == "monologue" else str(event.get("text") or "")[:300],
            "reply": str(result.get("text") or "")[:180],
            "action": str(result.get("action") or "idle")[:40],
            "ts": time.time(),
        }
        if kind == "monologue":
            key = self.reply_key(item["reply"])
            duplicates = sum(
                1 for previous in self.exchanges[-12:]
                if previous.get("kind") == "monologue"
                and self.reply_key(previous.get("reply")) == key
            )
            if key and duplicates >= 2:
                # Do not reinforce a line the model is already stuck on.
                return item
        self.exchanges.append(item)
        del self.exchanges[:-self.max_exchanges]
        self.save()
        return item

    def recent(self, limit=6, viewer=""):
        """Return the newest exchanges, preferring the current viewer's turns."""
        limit = max(0, min(int(limit or 0), 24))
        if limit <= 0 or not self.exchanges:
            return []
        items = list(self.exchanges)
        if not viewer:
            return items[-limit:]
        own = [item for item in items if item["viewer"] == viewer][-max(2, limit // 2):]
        own_ids = {item["id"] for item in own}
        remaining = limit - len(own)
        others = [item for item in items if item["id"] not in own_ids][-remaining:] if remaining > 0 else []
        merged = sorted(own + others, key=lambda item: item["ts"])
        return merged[-limit:]

    def clear(self):
        count = len(self.exchanges)
        self.exchanges = []
        self.save()
        return count

    def save(self):
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(
                    {"version": 1, "exchanges": self.exchanges},
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            temporary.replace(self.path)
        except OSError as exc:
            log.warning("Could not save conversation memory (%s)", type(exc).__name__)

    @property
    def count(self):
        return len(self.exchanges)

    def __len__(self):
        return len(self.exchanges)
