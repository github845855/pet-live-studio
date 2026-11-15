"""点歌队列：观众发「点歌 歌名」，按顺序把本地曲库里的歌排进播放队列。

只做排队与去重，不碰播放；播完一首由舞台回报 ``track_ended``，服务端再从
队列里取下一首。队列空时恢复单曲循环。
"""

import re

CLEAN = re.compile(r"[\s\u3000，。！？!?~～、,.]+")
REQUEST = re.compile(r"^(?:点歌|来一首|放一首|想听)\s*(?P<query>.+)$")
MAX_QUERY = 40


def normalize(text):
    return CLEAN.sub("", str(text or "")).lower()


def parse_request(text):
    """「点歌 晴天」→ '晴天'；不是点歌就返回 None。"""
    match = REQUEST.match(str(text or "").strip())
    if not match:
        return None
    query = match.group("query").strip()[:MAX_QUERY]
    return query or None


def find_track(library, query):
    target = normalize(query)
    if not target:
        return None
    fallback = None
    for track in library.tracks():
        title = normalize(track.get("title"))
        by = normalize(track.get("by") or "")
        if target in (title, f"{by}{title}") and title:
            return track
        if title and (target in title or title in target):
            fallback = fallback or track
    return fallback


class SongQueue:
    LIMIT = 20

    def __init__(self):
        self.items = []

    def __len__(self):
        return len(self.items)

    def ids(self):
        return {item["id"] for item in self.items}

    def add(self, track, user=""):
        """返回 (ok, reason)。已经在队列里或满了就拒绝。"""
        if any(item["id"] == track["id"] for item in self.items):
            return False, "duplicate"
        if len(self.items) >= self.LIMIT:
            return False, "full"
        self.items.append({
            "id": track["id"],
            "title": track.get("title") or "",
            "by": track.get("by") or "",
            "user": (user or "")[:40],
        })
        return True, ""

    def pop(self):
        return self.items.pop(0) if self.items else None

    def clear(self):
        self.items.clear()

    def snapshot(self):
        return [dict(item) for item in self.items]
