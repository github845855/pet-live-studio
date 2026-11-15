import asyncio
import json
import logging
import math
import mimetypes
import os
from pathlib import Path
import re
import sys
import time
import uuid
from urllib.parse import urlsplit

import aiohttp
from aiohttp import web
from dotenv import load_dotenv

from core.actions import ACTION_LABELS, CLIP_FILES, stage_actions_payload
from core.commands import match as match_command
from core.pet_state import PetState
from core.songbook import SongQueue, find_track, parse_request
from core.events import normalize_event
from core.hub import EventHub
from core.memory import ConversationMemory
from core.music import MusicLibrary, track_public
from core.llm import LocalLLM, REPLY_KINDS
from core.net import is_lan_bind_host
from core.settings import load_settings, public_settings, save_settings
from core.tts import (
    AZURE_REGION_PATTERN,
    AZURE_VOICES,
    SpeechUpstreamError,
    detect_audio_mime,
    speech_client_timeout,
    synthesize_azure_speech,
    synthesize_openai_speech,
)
from adapters.douyin import validate_bridge_url

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env", override=False)
WEB = ROOT / "web"
RUNTIME = ROOT / "runtime"
SETTINGS_FILE = Path(os.getenv("PET_LIVE_SETTINGS") or (RUNTIME / "settings.json"))
MEMORY_FILE = Path(os.getenv("PET_LIVE_MEMORY") or (RUNTIME / "memory.json"))
PET_STATE_FILE = Path(os.getenv("PET_LIVE_STATE") or (RUNTIME / "pet_state.json"))
BACKGROUND_DIR = RUNTIME / "media" / "background"
MUSIC_DIR = RUNTIME / "media" / "music"
ACTION_CLIP_DIR = ROOT / "webm"
MAX_BACKGROUND_BYTES = 8 * 1024 * 1024
MAX_MUSIC_BYTES = 40 * 1024 * 1024
# 作息从进程启动的那一刻算起：每次开播都是新的「早上」，多端显示也一致。
DAY_CYCLE_STARTED_AT = int(time.time() * 1000)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("pet-live-studio")
hub = EventHub()
llm_responder = None
pet_state = None
# 适配器只拿到 publish_event 这个函数，拿不到 app；这里留一个当前实例兜底。
CURRENT_APP = {"app": None}
LLM_RESPONDER_KEY = web.AppKey("llm_responder", dict)
PET_KEY = web.AppKey("pet_state", PetState)
SONG_KEY = web.AppKey("song_queue", SongQueue)
SETTINGS_KEY = web.AppKey("settings", dict)
BILIBILI_TASK_KEY = web.AppKey("bilibili_task", dict)
BILIBILI_STATUS_KEY = web.AppKey("bilibili_status", dict)
DOUYIN_TASK_KEY = web.AppKey("douyin_task", dict)
DOUYIN_STATUS_KEY = web.AppKey("douyin_status", dict)
MEMORY_KEY = web.AppKey("memory", ConversationMemory)
MUSIC_KEY = web.AppKey("music", MusicLibrary)
VOICE_REPORTS_KEY = web.AppKey("voice_reports", dict)
PERSIST_TASK_KEY = web.AppKey("persist_task", dict)

LAYOUT_NUMBERS = {
    "pet_x": (0.0, 100.0),
    "pet_y": (0.0, 100.0),
    "pet_scale": (0.5, 3.0),
}
HUD_POSITIONS = {"left-top", "right-top", "left-bottom", "right-bottom"}
DEFAULT_ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}
# A dropped connection or a 5xx from a local TTS service usually succeeds on a
# second try; retrying costs nothing compared to dropping the line on the stage.
SPEECH_ATTEMPTS = 2
SPEECH_RETRY_DELAY = 0.8


def allowed_hosts():
    hosts = set(DEFAULT_ALLOWED_HOSTS)
    for item in os.getenv("PET_LIVE_ALLOWED_HOSTS", "").split(","):
        host = item.strip().lower().rstrip(".")
        if host:
            hosts.add(host)
    return hosts
SETTING_NUMBERS = {
    "speech_rate": (0.5, 2.0),
    "speech_pitch": (0.5, 2.0),
    "speech_volume": (0.0, 1.0),
    "pet_scale": (0.5, 3.0),
    "pet_x": (0.0, 100.0),
    "pet_y": (0.0, 100.0),
    "background_dim": (0.0, 0.9),
    "music_volume": (0.0, 1.0),
    "music_duck": (0.0, 1.0),
    "sleep_after": (0.0, 240.0),
    "day_cycle_minutes": (0.0, 1440.0),
    "hud_opacity": (0.2, 1.0),
    "hud_scale": (0.6, 1.6),
}
SETTING_BOOLEANS = (
    "speech_enabled",
    "idle_chatter_enabled",
    "memory_enabled",
    "pet_flip",
    "pet_shadow",
    "show_brand",
    "feed_enabled",
    "music_enabled",
    "music_loop",
    "hud_enabled",
)


@web.middleware
async def asset_cache(request, handler):
    """Keep HTML/CSS/JS fresh while letting the large animations be cached."""
    response = await handler(request)
    path = request.path
    if path in {"/", "/control/"} or (
        path.startswith("/assets/") and not path.startswith("/assets/animations/")
    ):
        response.headers["Cache-Control"] = "no-cache"
    return response


@web.middleware
async def local_only(request, handler):
    try:
        hostname = urlsplit("//" + request.host).hostname
    except ValueError as exc:
        raise web.HTTPForbidden() from exc
    if not hostname or hostname not in allowed_hosts():
        raise web.HTTPForbidden(
            text="Host not allowed. Set PET_LIVE_ALLOWED_HOSTS for LAN access."
        )
    origin = request.headers.get("Origin")
    if origin and origin != f"{request.scheme}://{request.host}":
        raise web.HTTPForbidden(text="Same-origin requests only")
    return await handler(request)


async def stage(request):
    return web.FileResponse(WEB / "index.html")


async def control(request):
    return web.FileResponse(WEB / "control.html")


async def animation(request):
    name = request.match_info["name"]
    if name not in ACTION_LABELS:
        raise web.HTTPNotFound()
    return web.FileResponse(
        WEB / "assets" / "animations" / f"{name}.webp",
        headers={"Content-Type": "image/webp", "Cache-Control": "public, max-age=86400"},
    )


async def action_clip(request):
    """Serve one motion clip. Only names present in the catalog are reachable."""
    name = request.match_info["name"]
    filename = CLIP_FILES.get(name)
    if not filename:
        raise web.HTTPNotFound()
    path = ACTION_CLIP_DIR / filename
    if not path.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={
        "Content-Type": "video/webm",
        "Cache-Control": "public, max-age=86400",
    })


async def list_actions(request):
    return web.json_response(stage_actions_payload())


BACKGROUND_RE = re.compile(r"^(?:background|[a-f0-9]{12})\.(?:png|jpg|webp)$")


def background_response(name):
    if not name or not BACKGROUND_RE.fullmatch(name):
        raise web.HTTPNotFound()
    path = BACKGROUND_DIR / name
    if not path.is_file():
        raise web.HTTPNotFound()
    mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
    return web.FileResponse(path, headers={"Content-Type": mime, "Cache-Control": "no-store"})


def background_items(app):
    """Every image in the background folder, newest first, with the active one flagged."""
    active = app[SETTINGS_KEY]["background"]
    entries = []
    if BACKGROUND_DIR.is_dir():
        for path in BACKGROUND_DIR.iterdir():
            if not path.is_file() or not BACKGROUND_RE.fullmatch(path.name):
                continue
            try:
                stamp = path.stat()
            except OSError:
                continue
            entries.append((stamp.st_mtime_ns, {
                "name": path.name,
                "url": f"/api/backgrounds/{path.name}",
                "size": stamp.st_size,
                "active": path.name == active,
            }))
    entries.sort(key=lambda item: item[0], reverse=True)
    return [item for _, item in entries]


def background_payload(app):
    active = app[SETTINGS_KEY]["background"]
    return {"background": active, "active": active, "items": background_items(app)}


async def background(request):
    return background_response(request.app[SETTINGS_KEY]["background"])


async def background_item(request):
    return background_response(request.match_info["name"])


async def list_backgrounds(request):
    return web.json_response(background_payload(request.app))


async def upload_background(request):
    parts = await request.multipart()
    field = await parts.next()
    if field is None or field.name != "file":
        raise web.HTTPBadRequest(text="An image file is required")
    content = await read_upload(field, MAX_BACKGROUND_BYTES)
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        suffix = ".png"
    elif content.startswith(b"\xff\xd8\xff"):
        suffix = ".jpg"
    elif content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        suffix = ".webp"
    else:
        raise web.HTTPBadRequest(text="Use a PNG, JPEG, or WebP image")
    if await parts.next() is not None:
        raise web.HTTPBadRequest(text="Only one file may be uploaded")

    BACKGROUND_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex[:12]}{suffix}"
    (BACKGROUND_DIR / filename).write_bytes(content)
    request.app[SETTINGS_KEY]["background"] = filename
    persist_settings(request.app)
    await broadcast_stage_settings(request.app)
    return web.json_response(background_payload(request.app))


async def remove_background(request):
    """Stop using the current image; the file stays in the library."""
    request.app[SETTINGS_KEY]["background"] = ""
    persist_settings(request.app)
    await broadcast_stage_settings(request.app)
    return web.json_response(background_payload(request.app))


async def delete_background(request):
    name = request.match_info["name"]
    if not BACKGROUND_RE.fullmatch(name):
        raise web.HTTPNotFound()
    path = BACKGROUND_DIR / name
    if not path.is_file():
        raise web.HTTPNotFound()
    path.unlink(missing_ok=True)
    if request.app[SETTINGS_KEY]["background"] == name:
        request.app[SETTINGS_KEY]["background"] = ""
        persist_settings(request.app)
        await broadcast_stage_settings(request.app)
    return web.json_response(background_payload(request.app))


async def read_upload(field, limit):
    chunks = []
    size = 0
    while True:
        chunk = await field.read_chunk()
        if not chunk:
            break
        size += len(chunk)
        if size > limit:
            raise web.HTTPRequestEntityTooLarge(max_size=limit, actual_size=size)
        chunks.append(chunk)
    if not size:
        raise web.HTTPBadRequest(text="Uploaded file is empty")
    return b"".join(chunks)


def music_state(settings, library, songs=None):
    track, _ = library.get(settings["music_track"]) if settings["music_track"] else (None, None)
    queued = songs.snapshot() if songs is not None else []
    return {
        "tracks": [track_public(item) for item in library.tracks()],
        "active": track["id"] if track else "",
        "title": track["title"] if track else "",
        "enabled": settings["music_enabled"],
        "volume": settings["music_volume"],
        "duck": settings["music_duck"],
        # 队列非空时关掉单曲循环，让舞台播完能回报 track_ended 接着放下一首。
        "loop": bool(settings["music_loop"]) and not queued,
        "queue": queued,
    }


async def list_music(request):
    state = music_state(request.app[SETTINGS_KEY], request.app[MUSIC_KEY], request.app[SONG_KEY])
    return web.json_response(state)


async def upload_music(request):
    parts = await request.multipart()
    field = await parts.next()
    if field is None or field.name != "file":
        raise web.HTTPBadRequest(text="An audio file is required")
    content = await read_upload(field, MAX_MUSIC_BYTES)
    if await parts.next() is not None:
        raise web.HTTPBadRequest(text="Only one file may be uploaded")
    library = request.app[MUSIC_KEY]
    try:
        track = library.add(content, field.filename)
    except ValueError as exc:
        raise web.HTTPBadRequest(text=str(exc)) from exc
    settings = request.app[SETTINGS_KEY]
    if not settings["music_track"]:
        settings["music_track"] = track["id"]
        persist_settings(request.app)
        await broadcast_stage_settings(request.app)
    state = music_state(settings, library, request.app[SONG_KEY])
    return web.json_response({"track": track_public(track), **state})


async def scan_music(request):
    """Pick up audio files copied straight into runtime/media/music."""
    library = request.app[MUSIC_KEY]
    added = library.scan()
    settings = request.app[SETTINGS_KEY]
    if added and not settings["music_track"]:
        settings["music_track"] = added[0]["id"]
        persist_settings(request.app)
        await broadcast_stage_settings(request.app)
    return web.json_response({
        "added": [track_public(track) for track in added],
        **music_state(settings, library, request.app[SONG_KEY]),
    })


async def delete_music(request):
    track_id = request.match_info["track_id"]
    library = request.app[MUSIC_KEY]
    removed = library.remove(track_id)
    if removed is None:
        raise web.HTTPNotFound()
    settings = request.app[SETTINGS_KEY]
    if settings["music_track"] == track_id:
        remaining = library.tracks()
        settings["music_track"] = remaining[0]["id"] if remaining else ""
        persist_settings(request.app)
        await broadcast_stage_settings(request.app)
    return web.json_response(music_state(settings, library, request.app[SONG_KEY]))


async def music_file(request):
    library = request.app[MUSIC_KEY]
    track, path = library.get(request.match_info["track_id"])
    if track is None or not path.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(path, headers={
        "Content-Type": library.mime(track),
        "Cache-Control": "public, max-age=3600",
    })


async def synthesize_speech(request):
    settings = request.app[SETTINGS_KEY]
    if settings["speech_provider"] not in {"azure", "openai"}:
        raise web.HTTPBadRequest(text="Fixed speech synthesis is not enabled")
    try:
        data = await request.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise web.HTTPBadRequest(text="Speech request must be a JSON object") from exc
    if not isinstance(data, dict) or not isinstance(data.get("text"), str):
        raise web.HTTPBadRequest(text="Speech text is required")
    provider = settings["speech_provider"]
    audio = None
    failure = None
    async with aiohttp.ClientSession(
            timeout=speech_client_timeout(settings), trust_env=False) as session:
        for attempt in range(SPEECH_ATTEMPTS):
            try:
                if provider == "azure":
                    audio = await synthesize_azure_speech(session, data["text"], settings)
                else:
                    audio = await synthesize_openai_speech(session, data["text"], settings)
                failure = None
                break
            except ValueError as exc:
                raise web.HTTPBadRequest(text=str(exc)) from exc
            except SpeechUpstreamError as exc:
                failure = exc
            except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
                failure = SpeechUpstreamError(
                    f"Speech request failed ({type(exc).__name__})",
                    retryable=True,
                )
            if not failure.retryable or attempt + 1 >= SPEECH_ATTEMPTS:
                break
            log.warning(
                "%s speech synthesis failed (%s), retrying once",
                provider,
                failure,
            )
            await asyncio.sleep(SPEECH_RETRY_DELAY)
    if failure is not None:
        log.warning("%s speech synthesis failed (%s)", provider, failure)
        raise web.HTTPBadGateway(text=f"Speech synthesis failed: {failure}") from failure
    return web.Response(body=audio, content_type=detect_audio_mime(audio), headers={
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    })


async def health(request):
    responder = request.app[LLM_RESPONDER_KEY]["instance"]
    return web.json_response({
        "ok": True,
        "clients": len(hub.peers),
        "speech_owner": hub.audio_owner_id,
        "audio_stages": len(hub.stage_clients),
        "llm_enabled": responder is not None,
        "reply_queue": responder.queue_status() if responder else None,
        "idle_chatter": responder.idle_chatter_status() if responder else None,
        "bilibili": request.app[BILIBILI_STATUS_KEY],
        "douyin": request.app[DOUYIN_STATUS_KEY],
        "music_tracks": len(request.app[MUSIC_KEY].tracks()),
        "memory_exchanges": len(request.app[MEMORY_KEY]),
    })


async def get_settings(request):
    settings = request.app[SETTINGS_KEY]
    responder = request.app[LLM_RESPONDER_KEY]["instance"]
    return web.json_response({
        "settings": public_settings(settings),
        "music": music_state(settings, request.app[MUSIC_KEY], request.app[SONG_KEY]),
        "reply_queue": responder.queue_status() if responder else None,
        "bilibili": request.app[BILIBILI_STATUS_KEY],
        "douyin": request.app[DOUYIN_STATUS_KEY],
        "stage_voices": request.app[VOICE_REPORTS_KEY],
        "memory": {
            "enabled": settings["memory_enabled"],
            "turns": settings["memory_turns"],
            "exchanges": len(request.app[MEMORY_KEY]),
        },
        "clients": len(hub.peers),
        "actions": [{"id": action, "label": label} for action, label in ACTION_LABELS.items()],
    })


async def test_llm(request):
    settings = request.app[SETTINGS_KEY]
    if not settings["llm_base_url"]:
        raise web.HTTPBadRequest(text="Configure an API URL first")
    client = LocalLLM(
        settings["llm_base_url"],
        model=settings["llm_model"],
        api_key=settings["llm_api_key"],
        persona=settings["persona"],
    )
    await client.start()
    try:
        models = await client.test_connection()
    except (aiohttp.ClientError, ValueError, asyncio.TimeoutError) as exc:
        raise web.HTTPBadGateway(text=str(exc)) from exc
    finally:
        await client.close()
    return web.json_response({"ok": True, "models": models})


async def update_settings(request):
    try:
        data = await request.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise web.HTTPBadRequest(text="Settings must be a JSON object") from exc
    if not isinstance(data, dict):
        raise web.HTTPBadRequest(text="Settings must be a JSON object")

    settings = request.app[SETTINGS_KEY]
    updated = dict(settings)
    for key, limit in {
        "llm_base_url": 500,
        "llm_model": 120,
        "persona": 2000,
        "bili_room_id": 16,
        "speech_provider": 20,
        "speech_voice": 200,
        "speech_openai_voice": 200,
        "speech_base_url": 500,
        "speech_model": 120,
        "speech_region": 40,
        "feed_position": 10,
        "hud_position": 20,
        "music_track": 32,
        "douyin_room_id": 24,
        "douyin_bridge_url": 300,
    }.items():
        if key in data:
            value = data[key]
            if not isinstance(value, str) or len(value) > limit:
                raise web.HTTPBadRequest(text=f"Invalid setting: {key}")
            updated[key] = value.strip()
    if "speech_provider" in data and updated["speech_provider"] not in {"browser", "azure", "openai"}:
        raise web.HTTPBadRequest(text="Invalid setting: speech_provider")
    if "speech_region" in data and updated["speech_region"] and not AZURE_REGION_PATTERN.fullmatch(
            updated["speech_region"].lower()):
        raise web.HTTPBadRequest(text="Invalid setting: speech_region")
    if "speech_voice" in data and updated["speech_provider"] == "azure":
        if updated["speech_voice"] not in AZURE_VOICES:
            raise web.HTTPBadRequest(text="Unsupported Azure Speech voice")
    if updated["speech_provider"] == "openai":
        try:
            updated["speech_base_url"] = LocalLLM.validate_base_url(updated["speech_base_url"])
        except ValueError as exc:
            raise web.HTTPBadRequest(text=f"Invalid speech_base_url: {exc}") from exc
        if not updated["speech_model"]:
            raise web.HTTPBadRequest(text="Speech model is required for OpenAI-compatible TTS")
        if not updated["speech_openai_voice"]:
            raise web.HTTPBadRequest(text="Speech voice is required for OpenAI-compatible TTS")
    if updated["llm_base_url"]:
        try:
            LocalLLM.validate_base_url(updated["llm_base_url"])
        except ValueError as exc:
            raise web.HTTPBadRequest(text=str(exc)) from exc
    room_id = updated["bili_room_id"]
    if room_id and (not room_id.isdigit() or int(room_id) <= 0):
        raise web.HTTPBadRequest(text="Room ID must be a positive number")
    douyin_room = updated["douyin_room_id"]
    if douyin_room and (not douyin_room.isdigit() or int(douyin_room) <= 0):
        raise web.HTTPBadRequest(text="Douyin room ID must be a positive number")
    try:
        updated["douyin_bridge_url"] = validate_bridge_url(updated["douyin_bridge_url"])
    except ValueError as exc:
        raise web.HTTPBadRequest(text=str(exc)) from exc
    if updated["feed_position"] not in {"left", "right"}:
        raise web.HTTPBadRequest(text="Invalid setting: feed_position")
    if updated["hud_position"] not in HUD_POSITIONS:
        raise web.HTTPBadRequest(text="Invalid setting: hud_position")
    if updated["music_track"]:
        track, _ = request.app[MUSIC_KEY].get(updated["music_track"])
        if track is None:
            raise web.HTTPBadRequest(text="Unknown music track")
    if "background" in data:
        name = data["background"]
        if not isinstance(name, str) or (name and not BACKGROUND_RE.fullmatch(name)):
            raise web.HTTPBadRequest(text="Invalid setting: background")
        if name and not (BACKGROUND_DIR / name).is_file():
            raise web.HTTPBadRequest(text="Unknown background")
        updated["background"] = name

    for key in SETTING_BOOLEANS:
        if key in data:
            if not isinstance(data[key], bool):
                raise web.HTTPBadRequest(text=f"Invalid setting: {key}")
            updated[key] = data[key]
    for key, (minimum, maximum) in SETTING_NUMBERS.items():
        if key in data:
            try:
                value = _number(data[key])
            except ValueError as exc:
                raise web.HTTPBadRequest(text=f"Invalid setting: {key}") from exc
            if not minimum <= value <= maximum:
                raise web.HTTPBadRequest(text=f"Invalid setting: {key}")
            updated[key] = value
    if "idle_chatter_interval" in data:
        interval = data["idle_chatter_interval"]
        if isinstance(interval, bool) or not isinstance(interval, int) or not 10 <= interval <= 600:
            raise web.HTTPBadRequest(text="Invalid setting: idle_chatter_interval")
        updated["idle_chatter_interval"] = interval
    if "memory_turns" in data:
        turns = data["memory_turns"]
        if isinstance(turns, bool) or not isinstance(turns, int) or not 2 <= turns <= 12:
            raise web.HTTPBadRequest(text="Invalid setting: memory_turns")
        updated["memory_turns"] = turns
    if "api_key" in data:
        if not isinstance(data["api_key"], str) or len(data["api_key"]) > 2000:
            raise web.HTTPBadRequest(text="Invalid API key")
        api_key = data["api_key"].strip()
        if api_key:
            updated["llm_api_key"] = api_key
        elif data.get("clear_api_key") is True:
            updated["llm_api_key"] = ""
    if "sessdata" in data:
        if not isinstance(data["sessdata"], str) or len(data["sessdata"]) > 4000:
            raise web.HTTPBadRequest(text="Invalid SESSDATA")
        sessdata = data["sessdata"].strip()
        if sessdata:
            updated["bili_sessdata"] = sessdata
        elif data.get("clear_sessdata") is True:
            updated["bili_sessdata"] = ""
    if "speech_key" in data:
        if not isinstance(data["speech_key"], str) or len(data["speech_key"]) > 4000:
            raise web.HTTPBadRequest(text="Invalid Azure Speech key")
        speech_key = data["speech_key"].strip()
        if speech_key:
            updated["speech_key"] = speech_key
        elif data.get("clear_speech_key") is True:
            updated["speech_key"] = ""
    if "speech_api_key" in data:
        if not isinstance(data["speech_api_key"], str) or len(data["speech_api_key"]) > 4000:
            raise web.HTTPBadRequest(text="Invalid Speech API key")
        speech_api_key = data["speech_api_key"].strip()
        if speech_api_key:
            updated["speech_api_key"] = speech_api_key
        elif data.get("clear_speech_api_key") is True:
            updated["speech_api_key"] = ""

    llm_changed = any(
        updated[key] != settings[key]
        for key in ("llm_base_url", "llm_model", "llm_api_key", "persona")
    )
    bilibili_changed = any(
        updated[key] != settings[key]
        for key in ("bili_room_id", "bili_sessdata")
    )
    douyin_changed = any(
        updated[key] != settings[key]
        for key in ("douyin_room_id", "douyin_bridge_url")
    )
    settings.update(updated)
    persist_settings(request.app)
    responder = request.app[LLM_RESPONDER_KEY]["instance"]
    if responder:
        responder.configure_idle_chatter(
            settings["idle_chatter_enabled"],
            settings["idle_chatter_interval"],
        )
        responder.configure_memory(
            settings["memory_enabled"],
            settings["memory_turns"],
        )
    if llm_changed:
        await restart_llm(request.app)
    if bilibili_changed and (
        request.app[BILIBILI_TASK_KEY]["task"] is not None or not updated["bili_room_id"]
    ):
        await configure_bilibili(request.app, updated["bili_room_id"])
    if douyin_changed and (
        request.app[DOUYIN_TASK_KEY]["task"] is not None or not updated["douyin_room_id"]
    ):
        await configure_douyin(
            request.app,
            updated["douyin_room_id"],
            updated["douyin_bridge_url"],
        )
    await broadcast_stage_settings(request.app)
    return await get_settings(request)


def _number(value):
    if isinstance(value, bool):
        raise ValueError("not a number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("not finite")
    return number


async def bilibili_control(request):
    try:
        data = await request.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise web.HTTPBadRequest(text="Request must be a JSON object") from exc
    if not isinstance(data, dict) or data.get("action") not in {"connect", "disconnect"}:
        raise web.HTTPBadRequest(text="Action must be connect or disconnect")
    if data["action"] == "disconnect":
        await configure_bilibili(request.app, "")
        return web.json_response({"bilibili": request.app[BILIBILI_STATUS_KEY]})
    room_id = data.get("room_id", request.app[SETTINGS_KEY]["bili_room_id"])
    if not isinstance(room_id, str) or not room_id.isdigit() or int(room_id) <= 0:
        raise web.HTTPBadRequest(text="Room ID must be a positive number")
    settings = request.app[SETTINGS_KEY]
    sessdata = data.get("sessdata")
    if sessdata is not None:
        if not isinstance(sessdata, str) or len(sessdata) > 4000:
            raise web.HTTPBadRequest(text="Invalid SESSDATA")
        sessdata = sessdata.strip()
        if sessdata:
            settings["bili_sessdata"] = sessdata
        elif data.get("clear_sessdata") is True:
            settings["bili_sessdata"] = ""
    settings["bili_room_id"] = room_id
    persist_settings(request.app)
    await configure_bilibili(request.app, room_id)
    return web.json_response({"bilibili": request.app[BILIBILI_STATUS_KEY]})


async def douyin_control(request):
    try:
        data = await request.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise web.HTTPBadRequest(text="Request must be a JSON object") from exc
    if not isinstance(data, dict) or data.get("action") not in {"connect", "disconnect"}:
        raise web.HTTPBadRequest(text="Action must be connect or disconnect")
    settings = request.app[SETTINGS_KEY]
    if data["action"] == "disconnect":
        await configure_douyin(request.app, "", settings["douyin_bridge_url"])
        return web.json_response({"douyin": request.app[DOUYIN_STATUS_KEY]})
    room_id = str(data.get("room_id") or settings["douyin_room_id"]).strip()
    if not room_id.isdigit() or int(room_id) <= 0:
        raise web.HTTPBadRequest(text="Douyin room ID must be a positive number")
    bridge_url = str(data.get("bridge_url") or settings["douyin_bridge_url"]).strip()
    try:
        bridge_url = validate_bridge_url(bridge_url)
    except ValueError as exc:
        raise web.HTTPBadRequest(text=str(exc)) from exc
    await configure_douyin(request.app, room_id, bridge_url)
    return web.json_response({"douyin": request.app[DOUYIN_STATUS_KEY]})


async def clear_memory(request):
    cleared = request.app[MEMORY_KEY].clear()
    return web.json_response({"cleared": cleared, "exchanges": 0})


async def post_event(request):
    try:
        raw = await request.json()
        event = normalize_event(raw)
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        raise web.HTTPBadRequest(text=str(exc)) from exc
    await publish_event(event, request.app)
    return web.json_response({"accepted": True, "id": event["id"]}, status=202)


async def get_pet(request):
    return web.json_response({"pet": request.app[PET_KEY].snapshot()})


async def apply_pet_intent(app, event):
    """弹幕指令：照料或点名动作。命中就不进模型，立刻给反馈。"""
    intent = match_command(event.get("text"))
    if not intent:
        return False
    pet = app[PET_KEY]
    user = event.get("user") or {}
    if intent["type"] == "care":
        result = pet.apply_care(
            intent["command"], user.get("id") or user.get("name") or "")
        if result.get("ok"):
            await hub.publish_packet({
                "type": "motion",
                "clip": result["clip"],
                "text": result["text"],
                "source": "care",
            })
            await hub.publish_packet({"type": "pet_state", **result["state"]})
        else:
            wait = result.get("wait")
            text = f"等一下啦，{round(wait)} 秒后再来～" if wait else "现在还不饿～"
            await hub.publish_packet({
                "type": "motion", "clip": "", "text": text, "source": "care",
            })
        return True
    await hub.publish_packet({
        "type": "motion",
        "clip": intent["clip"],
        "text": intent["text"],
        "source": "command",
    })
    return True


async def apply_song_request(app, event):
    """「点歌 X」：查本地曲库，排进队列；没在放歌就直接开播。"""
    query = parse_request(event.get("text"))
    if not query:
        return False
    settings = app[SETTINGS_KEY]
    songs = app[SONG_KEY]
    user = (event.get("user") or {}).get("name") or "观众"

    track = find_track(app[MUSIC_KEY], query)
    if track is None:
        await hub.publish_packet({
            "type": "motion", "clip": "",
            "text": f"没找到《{query}》，换个名字试试？", "source": "song",
        })
        return True

    ok, reason = songs.add(track, user)
    if not ok:
        text = "这首已经排上啦～" if reason == "duplicate" else "队列满啦，等会儿再点～"
        await hub.publish_packet({
            "type": "motion", "clip": "", "text": text, "source": "song",
        })
        return True

    if not settings["music_enabled"] or not settings["music_track"]:
        await advance_song(app, announce=False)
        text = f"收到，{user} 点了一首《{track['title']}》，这就放～"
    else:
        text = f"收到，{user} 点了一首《{track['title']}》，排在第 {len(songs)} 位"
    await hub.publish_packet({
        "type": "motion", "clip": "", "text": text, "source": "song",
    })
    await broadcast_stage_settings(app)
    return True


async def advance_song(app, announce=True):
    """队列里取下一首；空了就恢复单曲循环。"""
    songs = app[SONG_KEY]
    settings = app[SETTINGS_KEY]
    item = songs.pop()
    if item:
        if settings["music_track"] != item["id"]:
            settings["music_track"] = item["id"]
        settings["music_enabled"] = True
        persist_settings(app)
        await broadcast_stage_settings(app)
        if announce:
            await hub.publish_packet({
                "type": "motion", "clip": "",
                "text": f"下一首，《{item['title']}》", "source": "song",
            })
        return item
    await broadcast_stage_settings(app)
    return None


async def publish_event(event, app=None):
    global llm_responder
    app = app or CURRENT_APP["app"]
    event = normalize_event(event)
    await hub.publish(event)
    if app is not None and event["kind"] == "danmaku":
        if await apply_song_request(app, event):
            return
        if await apply_pet_intent(app, event):
            return
    if event["kind"] in REPLY_KINDS:
        if llm_responder and not llm_responder.submit(event):
            await hub.publish_packet({
                "type": "reply_status",
                "event_id": event["id"],
                "kind": event["kind"],
                "status": "dropped",
                **llm_responder.queue_status(),
            })
        elif not llm_responder:
            await hub.publish_packet({
                "type": "reply_status",
                "event_id": event["id"],
                "kind": event["kind"],
                "status": "unavailable",
            })


def create_app():
    global pet_state
    app = web.Application(
        middlewares=[local_only, asset_cache],
        client_max_size=max(MAX_MUSIC_BYTES, MAX_BACKGROUND_BYTES) + 2 * 1024 * 1024,
    )
    app[SETTINGS_KEY] = {}
    app[LLM_RESPONDER_KEY] = {"instance": None}
    app[BILIBILI_TASK_KEY] = {"task": None}
    app[DOUYIN_TASK_KEY] = {"task": None}
    app[PERSIST_TASK_KEY] = {"task": None}
    app[VOICE_REPORTS_KEY] = {"stage": None, "preview": None}
    app[MUSIC_KEY] = MusicLibrary(MUSIC_DIR)
    app[MEMORY_KEY] = ConversationMemory(MEMORY_FILE)
    pet_state = PetState(PET_STATE_FILE)
    app[PET_KEY] = pet_state
    app[SONG_KEY] = SongQueue()
    CURRENT_APP["app"] = app
    app[BILIBILI_STATUS_KEY] = {
        "state": "disconnected",
        "room_id": "",
        "message": "尚未连接",
    }
    app[DOUYIN_STATUS_KEY] = {
        "state": "disconnected",
        "room_id": "",
        "bridge_url": "http://127.0.0.1:8757",
        "message": "尚未连接",
    }
    app.router.add_get("/", stage)
    app.router.add_get("/control/", control)
    app.router.add_get("/api/settings", get_settings)
    app.router.add_get("/api/pet", get_pet)
    app.router.add_post("/api/settings", update_settings)
    app.router.add_post("/api/llm/test", test_llm)
    app.router.add_post("/api/bilibili", bilibili_control)
    app.router.add_post("/api/douyin", douyin_control)
    app.router.add_delete("/api/memory", clear_memory)
    app.router.add_post("/api/background", upload_background)
    app.router.add_get("/api/background/current", background)
    app.router.add_delete("/api/background", remove_background)
    app.router.add_get("/api/backgrounds", list_backgrounds)
    app.router.add_get("/api/backgrounds/{name}", background_item)
    app.router.add_delete("/api/backgrounds/{name}", delete_background)
    app.router.add_get("/api/music", list_music)
    app.router.add_post("/api/music", upload_music)
    app.router.add_post("/api/music/scan", scan_music)
    app.router.add_get("/api/music/{track_id}", music_file)
    app.router.add_post("/api/speech", synthesize_speech)
    app.router.add_delete("/api/music/{track_id}", delete_music)
    app.router.add_get("/assets/animations/{name}.webp", animation)
    app.router.add_get("/actions/{name}.webm", action_clip)
    app.router.add_get("/api/actions", list_actions)
    app.router.add_get("/health", health)
    app.router.add_get("/ws", hub.websocket)
    app.router.add_post("/api/events", post_event)
    app.router.add_static("/assets/", WEB / "assets", show_index=False)
    hub.command_handler = lambda peer, packet: handle_ws_command(app, peer, packet)
    app.on_startup.append(start_services)
    app.on_cleanup.append(stop_services)
    return app


def persist_settings(app):
    save_settings(SETTINGS_FILE, app[SETTINGS_KEY])


def schedule_persist(app, delay=0.8):
    state = app[PERSIST_TASK_KEY]
    task = state.get("task")
    if task and not task.done():
        task.cancel()

    async def persist_later():
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        save_settings(SETTINGS_FILE, app[SETTINGS_KEY])

    state["task"] = asyncio.create_task(persist_later())


def background_url(settings):
    filename = settings["background"]
    if not filename:
        return ""
    path = BACKGROUND_DIR / filename
    try:
        stamp = path.stat().st_mtime_ns
    except OSError:
        return ""
    return f"/api/background/current?v={stamp}"


def stage_settings_packet(settings, library=None, pet=None, songs=None):
    track = None
    if library is not None and settings["music_track"]:
        track, _ = library.get(settings["music_track"])
    return {
        "type": "stage_config",
        "speech": {
            "enabled": settings["speech_enabled"],
            "provider": settings["speech_provider"],
            "voice": settings["speech_voice"],
            "rate": settings["speech_rate"],
            "pitch": settings["speech_pitch"],
            "volume": settings["speech_volume"],
        },
        "idle_chatter": {
            "enabled": settings["idle_chatter_enabled"],
            "interval": settings["idle_chatter_interval"],
        },
        "stage": {
            "pet_scale": settings["pet_scale"],
            "pet_x": settings["pet_x"],
            "pet_y": settings["pet_y"],
            "pet_flip": settings["pet_flip"],
            "pet_shadow": settings["pet_shadow"],
            "show_brand": settings["show_brand"],
            "feed_enabled": settings["feed_enabled"],
            "feed_position": settings["feed_position"],
            "background_dim": settings["background_dim"],
            "background_url": background_url(settings),
            "sleep_after": settings["sleep_after"],
            "day_cycle_minutes": settings["day_cycle_minutes"],
            "day_cycle_started_at": DAY_CYCLE_STARTED_AT,
            "hud": {
                "enabled": settings["hud_enabled"],
                "position": settings["hud_position"],
                "opacity": settings["hud_opacity"],
                "scale": settings["hud_scale"],
            },
        },
        "music": {
            "enabled": settings["music_enabled"],
            "volume": settings["music_volume"],
            "duck": settings["music_duck"],
            "loop": bool(settings["music_loop"]) and not (songs is not None and len(songs)),
            "track": track["id"] if track else "",
            "title": track["title"] if track else "",
            "url": library.url(track["id"]) if track else "",
        },
        "actions": stage_actions_payload(),
        "pet": pet.snapshot() if pet is not None else None,
    }


async def broadcast_stage_settings(app):
    hub.settings_packet = stage_settings_packet(
        app[SETTINGS_KEY], app[MUSIC_KEY], app[PET_KEY], app[SONG_KEY])
    await hub.publish_packet(hub.settings_packet)


async def handle_ws_command(app, peer, packet):
    if not isinstance(packet, dict):
        await peer.send_json({"type": "command_error", "message": "Command must be an object"})
        return
    if packet.get("type") == "action":
        action = packet.get("action")
        if not isinstance(action, str) or action not in ACTION_LABELS:
            await peer.send_json({"type": "command_error", "message": "Unknown action"})
            return
        await hub.publish_packet({"type": "action", "action": action})
        return
    if packet.get("type") == "speech_test":
        await hub.publish_packet({"type": "speech_test"})
        return
    if packet.get("type") == "stage_state":
        asleep = packet.get("asleep")
        if not isinstance(asleep, bool):
            await peer.send_json({"type": "command_error", "message": "Invalid stage state"})
            return
        responder = app[LLM_RESPONDER_KEY]["instance"]
        if responder:
            responder.set_sleeping(asleep)
        return
    if packet.get("type") == "track_ended":
        await advance_song(app)
        return
    if packet.get("type") == "monologue_finished":
        event_id = packet.get("event_id")
        client_id = packet.get("client_id")
        if (isinstance(event_id, str) and len(event_id) <= 80
                and hub.is_audio_owner(peer, client_id)):
            responder = app[LLM_RESPONDER_KEY]["instance"]
            if responder:
                responder.complete_monologue(event_id)
            hub.acknowledge_monologue(event_id)
            await peer.send_json({
                "type": "monologue_finished_ack",
                "event_id": event_id,
            })
        return
    if packet.get("type") == "voice_report":
        role = packet.get("role")
        if role not in {"stage", "preview"}:
            role = "stage"
        client_id = packet.get("client_id")
        if not isinstance(client_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", client_id):
            await peer.send_json({"type": "command_error", "message": "Invalid stage client ID"})
            return
        raw_voices = packet.get("voices")
        if not isinstance(raw_voices, list):
            await peer.send_json({"type": "command_error", "message": "Invalid voice report"})
            return
        voices = []
        for item in raw_voices[:60]:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if not isinstance(name, str) or not name:
                continue
            voices.append({
                "name": name[:120],
                "lang": str(item.get("lang") or "")[:20],
                "local": bool(item.get("local")),
            })
        report = {
            "type": "voice_report",
            "role": role,
            "audio": bool(packet.get("audio")),
            "voices": voices,
            "wanted": str(packet.get("wanted") or "")[:120],
            "active": str(packet.get("active") or "")[:120],
            "missing": bool(packet.get("missing")),
        }
        app[VOICE_REPORTS_KEY][role] = report
        await hub.register_stage_client(
            peer,
            client_id,
            eligible=role == "stage" and bool(packet.get("audio")),
        )
        await hub.publish_packet(report)
        return
    if packet.get("type") == "stage_layout":
        updates = {}
        for key, (minimum, maximum) in LAYOUT_NUMBERS.items():
            if key not in packet:
                continue
            try:
                value = _number(packet[key])
            except (TypeError, ValueError):
                await peer.send_json({"type": "command_error", "message": f"Invalid {key}"})
                return
            if not minimum <= value <= maximum:
                await peer.send_json({"type": "command_error", "message": f"Invalid {key}"})
                return
            updates[key] = value
        if not updates:
            await peer.send_json({"type": "command_error", "message": "No layout values"})
            return
        app[SETTINGS_KEY].update(updates)
        origin = packet.get("origin")
        await hub.publish_packet({
            "type": "stage_layout",
            "origin": origin if isinstance(origin, str) else "",
            **updates,
        })
        schedule_persist(app)
        return
    await peer.send_json({"type": "command_error", "message": "Unknown command"})


async def start_services(app):
    app[SETTINGS_KEY].update(load_settings(SETTINGS_FILE))
    app[BILIBILI_STATUS_KEY].update({
        "state": "disconnected",
        "room_id": "",
        "message": "尚未连接",
    })
    app[DOUYIN_STATUS_KEY].update({
        "state": "disconnected",
        "room_id": "",
        "bridge_url": app[SETTINGS_KEY]["douyin_bridge_url"],
        "message": "尚未连接",
    })
    BACKGROUND_DIR.mkdir(parents=True, exist_ok=True)
    MUSIC_DIR.mkdir(parents=True, exist_ok=True)
    previous_track = app[SETTINGS_KEY]["music_track"]
    app[MUSIC_KEY].ensure_valid_track(app[SETTINGS_KEY])
    if app[SETTINGS_KEY]["music_track"] != previous_track:
        persist_settings(app)
    await broadcast_stage_settings(app)
    await start_llm(app)
    room_id = app[SETTINGS_KEY]["bili_room_id"]
    if room_id:
        await configure_bilibili(app, room_id)
    douyin_room = app[SETTINGS_KEY]["douyin_room_id"]
    if douyin_room:
        try:
            await configure_douyin(
                app,
                douyin_room,
                app[SETTINGS_KEY]["douyin_bridge_url"],
            )
        except ValueError as exc:
            set_douyin_status(app, "error", str(exc))


async def start_llm(app):
    global llm_responder
    settings = app[SETTINGS_KEY]
    base_url = settings["llm_base_url"]
    if not base_url:
        llm_responder = None
        return
    responder = LocalLLM(
        base_url=base_url,
        model=settings["llm_model"],
        api_key=settings["llm_api_key"],
        persona=settings["persona"],
        publish=hub.publish_packet,
        idle_chatter_enabled=settings["idle_chatter_enabled"],
        idle_chatter_interval=settings["idle_chatter_interval"],
        memory=app[MEMORY_KEY],
        memory_enabled=settings["memory_enabled"],
        memory_turns=settings["memory_turns"],
    )
    await responder.start()
    llm_responder = responder
    app[LLM_RESPONDER_KEY]["instance"] = responder
    log.info("Local LLM enabled at %s", responder.endpoint)


async def stop_llm(app):
    global llm_responder
    responder = app[LLM_RESPONDER_KEY]["instance"]
    if responder:
        await responder.close()
    app[LLM_RESPONDER_KEY]["instance"] = None
    llm_responder = None


async def restart_llm(app):
    await stop_llm(app)
    await start_llm(app)


def set_bilibili_status(app, state, message):
    app[BILIBILI_STATUS_KEY].update({
        "state": state,
        "room_id": app[SETTINGS_KEY]["bili_room_id"] if state != "disconnected" else "",
        "message": message,
    })


async def configure_bilibili(app, room_id):
    if room_id and (not room_id.isdigit() or int(room_id) <= 0):
        raise ValueError("BILI_ROOM_ID must be a positive number")
    task = app[BILIBILI_TASK_KEY]["task"]
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        app[BILIBILI_TASK_KEY]["task"] = None
    if not room_id:
        set_bilibili_status(app, "disconnected", "已断开")
        return
    settings = app[SETTINGS_KEY]
    if settings["bili_room_id"] != room_id:
        settings["bili_room_id"] = room_id
        persist_settings(app)
    set_bilibili_status(app, "connecting", "正在连接 B 站")
    from adapters.bilibili import run_bilibili

    app[BILIBILI_TASK_KEY]["task"] = asyncio.create_task(
        maintain_bilibili(
            run_bilibili,
            room_id,
            settings["bili_sessdata"],
            lambda state, message: set_bilibili_status(app, state, message),
        ),
        name=f"bilibili-receive-only-{room_id}",
    )


async def stop_services(app):
    task = app[PERSIST_TASK_KEY]["task"]
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        persist_settings(app)
    await stop_douyin(app)
    await stop_bilibili(app)
    await stop_llm(app)
    app[MEMORY_KEY].save()


async def maintain_bilibili(run_bilibili, room_id, sessdata, status_callback):
    delay = 5
    while True:
        try:
            await run_bilibili(room_id, sessdata, publish_event, status_callback)
            status_callback("reconnecting", "B 站连接已断开，正在重试")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            status_callback("error", f"连接失败：{type(exc).__name__}")
            log.warning("Bilibili adapter failed (%s); retrying in %s seconds",
                        type(exc).__name__, delay)
        await asyncio.sleep(delay)
        delay = min(delay * 2, 60)


async def stop_bilibili(app):
    task = app[BILIBILI_TASK_KEY]["task"]
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        app[BILIBILI_TASK_KEY]["task"] = None
    if SETTINGS_KEY in app and BILIBILI_STATUS_KEY in app:
        set_bilibili_status(app, "disconnected", "已断开")


def set_douyin_status(app, state, message):
    settings = app[SETTINGS_KEY]
    app[DOUYIN_STATUS_KEY].update({
        "state": state,
        "room_id": settings["douyin_room_id"] if state != "disconnected" else "",
        "bridge_url": settings["douyin_bridge_url"],
        "message": message,
    })


async def configure_douyin(app, room_id, bridge_url):
    room_id = str(room_id or "").strip()
    bridge_url = validate_bridge_url(bridge_url)
    if room_id and (not room_id.isdigit() or int(room_id) <= 0):
        raise ValueError("Douyin room ID must be a positive number")
    task = app[DOUYIN_TASK_KEY]["task"]
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        app[DOUYIN_TASK_KEY]["task"] = None
    settings = app[SETTINGS_KEY]
    changed = (
        settings["douyin_room_id"] != room_id
        or settings["douyin_bridge_url"] != bridge_url
    )
    settings["douyin_room_id"] = room_id
    settings["douyin_bridge_url"] = bridge_url
    if changed:
        persist_settings(app)
    if not room_id:
        set_douyin_status(app, "disconnected", "已断开")
        return
    set_douyin_status(app, "connecting", "正在连接 DyHub 抖音弹幕")
    from adapters.douyin import run_douyin

    app[DOUYIN_TASK_KEY]["task"] = asyncio.create_task(
        maintain_douyin(
            run_douyin,
            room_id,
            bridge_url,
            lambda state, message: set_douyin_status(app, state, message),
        ),
        name=f"douyin-receive-only-{room_id}",
    )


async def maintain_douyin(run_douyin, room_id, bridge_url, status_callback):
    delay = 5
    while True:
        try:
            await run_douyin(room_id, bridge_url, publish_event, status_callback)
            status_callback("reconnecting", "DyHub 连接已断开，正在重试")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            message = str(exc) if isinstance(exc, ConnectionError) else f"连接失败：{type(exc).__name__}"
            status_callback("error", f"DyHub：{message}")
            log.warning("Douyin bridge failed (%s); retrying in %s seconds",
                        type(exc).__name__, delay)
        await asyncio.sleep(delay)
        delay = min(delay * 2, 60)


async def stop_douyin(app):
    task = app[DOUYIN_TASK_KEY]["task"]
    if task:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        app[DOUYIN_TASK_KEY]["task"] = None
    if SETTINGS_KEY in app and DOUYIN_STATUS_KEY in app:
        set_douyin_status(app, "disconnected", "已断开")


def main():
    host = os.getenv("HOST", "127.0.0.1")
    if not is_lan_bind_host(host):
        raise ValueError(
            "HOST must be 0.0.0.0, a loopback address, or a private LAN address"
        )
    port = int(os.getenv("PORT", "8775"))
    if not 1024 <= port <= 65535:
        raise ValueError("PORT must be between 1024 and 65535")
    app = create_app()
    web.run_app(app, host=host, port=port, print=None)


if __name__ == "__main__":
    main()
