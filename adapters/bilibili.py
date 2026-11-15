import asyncio
import logging
import sys
from http.cookies import SimpleCookie
from pathlib import Path
from yarl import URL
import aiohttp

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "vendor"))
import blivedm

log = logging.getLogger(__name__)


class Handler(blivedm.BaseHandler):
    def __init__(self, queue):
        self.queue = queue

    def handle(self, client, command):
        cmd = command.get("cmd") if isinstance(command, dict) else None
        if cmd in ("LIVE", "PREPARING"):
            self.emit("live" if cmd == "LIVE" else "offline", None,
                      "直播开始啦" if cmd == "LIVE" else "直播结束啦")
            return
        try:
            super().handle(client, command)
        except (KeyError, TypeError, ValueError, IndexError, AttributeError):
            log.warning("Ignored malformed Bilibili event")

    def emit(self, kind, message, text="", ident=None, data=None):
        item = {
            "id": str(ident or ""),
            "platform": "bilibili",
            "kind": kind,
            "user": {
                "id": str(getattr(message, "uid", "")) if message else "",
                "name": (getattr(message, "uname", "") or getattr(message, "username", "") or "观众")
                if message else "直播间",
            },
            "text": str(text),
            "data": data or {},
        }
        try:
            self.queue.put_nowait(item)
        except asyncio.QueueFull:
            log.warning("Bilibili event queue full; dropping event kind=%s", kind)

    def _on_danmaku(self, client, message):
        self.emit("danmaku", message, message.msg)

    def _on_gift(self, client, message):
        self.emit("gift", message, f"送出 {message.gift_name} × {message.num}",
                  data={"gift_name": str(message.gift_name)[:80], "count": int(message.num)})

    def _on_super_chat(self, client, message):
        self.emit("gift", message, message.message, f"sc-{message.id}",
                  data={"gift_name": "醒目留言", "price": int(message.price)})

    def _on_buy_guard(self, client, message):
        self.emit("follow", message, "开通了大航海支持")

    def _on_interact_word_v2(self, client, message):
        if message.msg_type == 1:
            self.emit("enter", message, "来到直播间")
        elif message.msg_type == 2:
            self.emit("follow", message, "关注了直播间")


class StatusClient(blivedm.BLiveClient):
    def __init__(self, room_id, session, status_callback):
        super().__init__(room_id, session=session)
        self.status_callback = status_callback

    async def _on_ws_connect(self):
        await super()._on_ws_connect()
        if self.status_callback:
            self.status_callback("connected", "B 站弹幕连接正常")

    async def _on_ws_close(self):
        await super()._on_ws_close()
        if self.status_callback:
            self.status_callback("reconnecting", "B 站连接中断，正在重连")


async def run_bilibili(room_id, sessdata, publish, status_callback=None):
    if not room_id:
        return
    try:
        room = int(room_id)
        if room <= 0:
            raise ValueError
    except ValueError as exc:
        raise ValueError("BILI_ROOM_ID must be a positive number") from exc

    jar = aiohttp.CookieJar()
    cookies = SimpleCookie()
    if sessdata:
        cookies["SESSDATA"] = sessdata
        cookies["SESSDATA"]["domain"] = ".bilibili.com"
        cookies["SESSDATA"]["path"] = "/"
        cookies["SESSDATA"]["secure"] = True
        jar.update_cookies(cookies, response_url=URL("https://www.bilibili.com/"))

    queue = asyncio.Queue(maxsize=500)
    handler = Handler(queue)
    async with aiohttp.ClientSession(cookie_jar=jar, timeout=aiohttp.ClientTimeout(total=15)) as session:
        client = StatusClient(room, session=session, status_callback=status_callback)
        client.set_handler(handler)
        client.set_reconnect_policy(lambda retry, total: min(60, 5 * (1 + retry)))
        if status_callback:
            status_callback("connecting", "正在连接 B 站")
        client.start()
        log.info("Bilibili receive-only adapter started for room %s", room)
        try:
            while True:
                event = await queue.get()
                try:
                    await publish(event)
                finally:
                    queue.task_done()
        finally:
            await client.stop_and_close()
