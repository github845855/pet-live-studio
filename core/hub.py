import asyncio
import json
import logging
import time

from aiohttp import WSMsgType

log = logging.getLogger(__name__)


class EventHub:
    def __init__(self):
        self.peers = set()
        self.history = []
        self.started_at = time.time()
        self.command_handler = None
        self.settings_packet = None
        self.stage_clients = {}
        self.audio_owner_id = ""
        self.latest_monologue_reply = None

    async def publish(self, event):
        self.history.append(event)
        del self.history[:-40]
        if event.get("kind") in {"danmaku", "enter", "gift", "follow"}:
            self.latest_monologue_reply = None
        await self.publish_packet({"type": "event", "event": event})

    async def publish_packet(self, value):
        if value.get("type") == "reply" and value.get("kind") == "monologue":
            self.latest_monologue_reply = dict(value)
        packet = json.dumps(value, ensure_ascii=False)
        stale = []
        for peer in tuple(self.peers):
            try:
                await asyncio.wait_for(peer.send_str(packet), timeout=2)
            except (asyncio.TimeoutError, ConnectionError, RuntimeError):
                stale.append(peer)
        for peer in stale:
            self.peers.discard(peer)
            await peer.close()
            await self.unregister_stage_client(peer)

    def is_audio_owner(self, peer, client_id=None):
        registered_id = self.stage_clients.get(peer)
        return bool(
            registered_id
            and registered_id == self.audio_owner_id
            and (client_id is None or client_id == registered_id)
        )

    def acknowledge_monologue(self, event_id):
        if (self.latest_monologue_reply
                and self.latest_monologue_reply.get("event_id") == event_id):
            self.latest_monologue_reply = None

    async def _publish_speech_owner(self):
        await self.publish_packet({
            "type": "speech_owner",
            "client_id": self.audio_owner_id,
        })
        if not self.audio_owner_id or not self.latest_monologue_reply:
            return
        owner = next(
            (peer for peer, client_id in self.stage_clients.items()
             if client_id == self.audio_owner_id),
            None,
        )
        if owner and owner in self.peers:
            try:
                await asyncio.wait_for(
                    owner.send_json(self.latest_monologue_reply),
                    timeout=2,
                )
            except (asyncio.TimeoutError, ConnectionError, RuntimeError):
                log.warning("Could not resume idle speech on the new stage owner")

    async def register_stage_client(self, peer, client_id, eligible):
        self.stage_clients.pop(peer, None)
        if eligible and client_id:
            self.stage_clients[peer] = client_id
        if self.audio_owner_id not in self.stage_clients.values():
            self.audio_owner_id = next(iter(self.stage_clients.values()), "")
        await self._publish_speech_owner()

    async def unregister_stage_client(self, peer):
        self.stage_clients.pop(peer, None)
        if self.audio_owner_id not in self.stage_clients.values():
            self.audio_owner_id = next(iter(self.stage_clients.values()), "")
            await self._publish_speech_owner()

    async def websocket(self, request):
        from aiohttp import web

        ws = web.WebSocketResponse(heartbeat=25, max_msg_size=128 * 1024)
        await ws.prepare(request)
        self.peers.add(ws)
        await ws.send_json({"type": "hello", "version": 1})
        await ws.send_json({"type": "history", "items": self.history})
        if self.settings_packet:
            await ws.send_json(self.settings_packet)
        try:
            async for message in ws:
                if message.type == WSMsgType.TEXT:
                    try:
                        value = json.loads(message.data)
                    except json.JSONDecodeError:
                        await ws.send_json({"type": "command_error", "message": "Invalid JSON"})
                        continue
                    if self.command_handler:
                        await self.command_handler(ws, value)
                elif message.type == WSMsgType.ERROR:
                    log.debug("websocket closed with error")
        finally:
            self.peers.discard(ws)
            await self.unregister_stage_client(ws)
        return ws
