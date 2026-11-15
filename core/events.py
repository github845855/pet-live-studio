import math
import time
import uuid


EVENT_KINDS = frozenset({"danmaku", "gift", "enter", "follow", "live", "offline"})


def normalize_event(raw):
    if not isinstance(raw, dict):
        raise ValueError("event must be an object")
    kind = raw.get("kind")
    if kind not in EVENT_KINDS:
        raise ValueError("unsupported event kind")
    user = raw.get("user", {})
    data = raw.get("data", {})
    user = {} if user is None else user
    data = {} if data is None else data
    if not isinstance(user, dict) or not isinstance(data, dict):
        raise ValueError("user and data must be objects")
    text = raw.get("text", "")
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    try:
        timestamp = float(raw.get("timestamp") or time.time())
    except (TypeError, ValueError) as exc:
        raise ValueError("timestamp must be numeric") from exc
    if not math.isfinite(timestamp):
        raise ValueError("timestamp must be finite")
    return {
        "id": str(raw.get("id") or uuid.uuid4().hex)[:100],
        "platform": str(raw.get("platform") or "local")[:32],
        "kind": kind,
        "user": {
            "id": str(user.get("id") or "")[:64],
            "name": str(user.get("name") or "观众")[:40],
        },
        "text": text[:500],
        "data": data,
        "timestamp": timestamp,
    }
