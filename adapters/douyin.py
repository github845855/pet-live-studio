"""Douyin live events through a local DyHub bridge.

Douyin does not expose a stable public web danmaku API.  DyHub (MIT) is a
small local service that connects to a Douyin room and re-broadcasts normalized
events over WebSocket/SSE/Webhook.  Pet Live Studio only talks to the local
DyHub WebSocket, so no signatures or browser automation live in this project.
"""

import asyncio
import json
import logging
import time
import uuid
from urllib.parse import urlencode, urlsplit, urlunsplit

import aiohttp

from core.net import is_private_host

log = logging.getLogger(__name__)
EVENT_TYPES = "chat,gift,member,follow,roomStatus"
KIND_MAP = {
    "chat": "danmaku",
    "gift": "gift",
    "member": "enter",
    "follow": "follow",
}


def validate_bridge_url(value):
    value = (value or "").strip()
    if not value:
        value = "http://127.0.0.1:8757"
    if "://" not in value:
        value = "http://" + value
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https", "ws", "wss"} or not parsed.hostname:
        raise ValueError("DyHub 地址必须是 http(s):// 或 ws(s)://")
    if not is_private_host(parsed.hostname, "DOUYIN_ALLOW_HTTP_HOSTS"):
        raise ValueError("DyHub 地址必须是本机或局域网私有地址")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("DyHub 地址不能包含账号、查询参数或片段")
    return value.rstrip("/")


def bridge_endpoints(value, room_id):
    parsed = urlsplit(validate_bridge_url(value))
    secure = parsed.scheme in {"https", "wss"}
    http_scheme = "https" if secure else "http"
    ws_scheme = "wss" if secure else "ws"
    path = parsed.path.rstrip("/")
    ws_path = path if path.endswith("/ws") else (f"{path}/ws" if path else "/ws")
    http_base = urlunsplit((http_scheme, parsed.netloc, "", "", ""))
    query = urlencode({"roomId": room_id, "types": EVENT_TYPES})
    ws_url = urlunsplit((ws_scheme, parsed.netloc, ws_path, query, ""))
    return http_base, ws_url


def _timestamp(value):
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        number = 0
    if number > 10_000_000_000:
        number /= 1000
    return number or time.time()


def map_event(payload, room_id=""):
    """Convert one DyHub event into the unified event shape, or None."""
    if not isinstance(payload, dict):
        return None
    kind = KIND_MAP.get(payload.get("type"))
    if kind is None:
        return None
    user = payload.get("user") if isinstance(payload.get("user"), dict) else {}
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    name = str(user.get("nickname") or user.get("name") or "观众")[:40]
    user_id = str(user.get("id") or "")[:64]

    if kind == "danmaku":
        text = str(data.get("content") or "").strip()[:500]
        if not text:
            return None
        details = {}
    elif kind == "gift":
        gift = str(data.get("giftName") or "礼物")[:80]
        try:
            count = int(data.get("repeatCount") or data.get("comboCount")
                        or data.get("groupCount") or 1)
        except (TypeError, ValueError):
            count = 1
        count = max(1, count)
        text = f"送出 {gift} × {count}"
        details = {
            "gift_name": gift,
            "count": count,
            "diamond_count": data.get("diamondCount") or 0,
            "group_id": str(data.get("groupId") or "")[:80],
        }
    elif kind == "enter":
        text = "来到直播间"
        details = {"member_count": data.get("memberCount") or 0}
    else:  # follow
        text = "关注了直播间"
        details = {"action": data.get("action") or ""}

    return {
        "id": str(payload.get("id") or uuid.uuid4().hex)[:100],
        "platform": "douyin",
        "kind": kind,
        "user": {"id": user_id, "name": name},
        "text": text,
        "data": {"room_id": str(room_id or payload.get("roomId") or ""), **details},
        "timestamp": _timestamp(payload.get("ts") or payload.get("receivedAt")),
    }


def room_event(kind, room_id, text):
    return {
        "id": uuid.uuid4().hex,
        "platform": "douyin",
        "kind": kind,
        "user": {"id": "", "name": "直播间"},
        "text": text,
        "data": {"room_id": str(room_id)},
        "timestamp": time.time(),
    }


async def run_douyin(room_id, bridge_url, publish, status_callback=None):
    """Connect to a running DyHub instance and forward events until it closes."""
    http_base, ws_url = bridge_endpoints(bridge_url, room_id)
    timeout = aiohttp.ClientTimeout(total=None, connect=10, sock_read=None)
    async with aiohttp.ClientSession(timeout=timeout, trust_env=False) as session:
        # Ask DyHub to join the room if its management API is available.  If
        # the room is already connected this is harmless; if DyHub is behind a
        # different version we still try the WebSocket below.
        try:
            async with session.post(
                    f"{http_base}/api/rooms/connect",
                    json={"roomId": str(room_id)},
                    timeout=aiohttp.ClientTimeout(total=10, connect=5),
            ) as response:
                if response.status >= 400:
                    log.info("DyHub room connect returned HTTP %s; using existing room", response.status)
        except (aiohttp.ClientError, asyncio.TimeoutError):
            log.info("DyHub management API unavailable; using existing room connection")

        if status_callback:
            status_callback("connecting", "正在连接 DyHub 抖音弹幕")
        live_state = None
        try:
            ws_context = session.ws_connect(ws_url, heartbeat=25, max_msg_size=2 * 1024 * 1024)
            ws = await ws_context.__aenter__()
        except aiohttp.ClientConnectorError as exc:
            raise ConnectionError("DyHub 未启动或地址不可达（先运行 docker compose up -d）") from exc
        except aiohttp.WSServerHandshakeError as exc:
            raise ConnectionError("DyHub WebSocket 握手失败") from exc
        try:
            if status_callback:
                status_callback("connected", "抖音弹幕连接正常")
            async for message in ws:
                if message.type == aiohttp.WSMsgType.TEXT:
                    try:
                        payload = json.loads(message.data)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(payload, dict):
                        continue
                    if payload.get("type") == "__hello":
                        continue
                    if payload.get("type") == "roomStatus":
                        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
                        state = str(data.get("status") or "")
                        if state == "live":
                            if live_state is not True:
                                await publish(room_event("live", room_id, "抖音直播开始啦"))
                            live_state = True
                            if status_callback:
                                status_callback("connected", "抖音直播中")
                        elif state in {"closed", "error", "stopped"}:
                            if live_state is not False:
                                await publish(room_event("offline", room_id, "抖音直播结束啦"))
                            live_state = False
                            if status_callback:
                                status_callback("reconnecting", f"抖音房间状态：{state}")
                        continue
                    event = map_event(payload, room_id)
                    if event:
                        await publish(event)
                elif message.type == aiohttp.WSMsgType.ERROR:
                    break
        finally:
            await ws_context.__aexit__(None, None, None)
