import json
import math
import os

DEFAULT_PERSONA = "你是一只亲切、活泼的直播宠物。用简短自然的中文回应观众，不假装真人，不诱导打赏。"
LAYOUT_VERSION = 2
CHATTER_VERSION = 6
# 闲聊只是"没人说话时填空"，观众互动不再并进来，所以默认要安静一分钟才开口。
DEFAULT_CHATTER_INTERVAL = 60


def load_settings(path):
    settings = {
        "llm_base_url": os.getenv("LLM_BASE_URL", "http://127.0.0.1:11434/v1").strip(),
        "llm_model": os.getenv("LLM_MODEL", "").strip(),
        "llm_api_key": os.getenv("LLM_API_KEY", "").strip(),
        "persona": os.getenv("PET_PERSONA", DEFAULT_PERSONA).strip(),
        "bili_room_id": os.getenv("BILI_ROOM_ID", "").strip(),
        "bili_sessdata": os.getenv("BILI_SESSDATA", "").strip(),
        "speech_enabled": False,
        "speech_provider": "browser",
        "speech_rate": 1.0,
        "speech_pitch": 1.0,
        "speech_volume": 1.0,
        "speech_base_url": os.getenv("SPEECH_BASE_URL", "https://api.openai.com/v1").strip(),
        "speech_model": os.getenv("SPEECH_MODEL", "").strip(),
        "speech_openai_voice": os.getenv("SPEECH_VOICE", "").strip(),
        "speech_voice": "",
        "speech_region": os.getenv("AZURE_SPEECH_REGION", "").strip(),
        "speech_api_key": os.getenv("SPEECH_API_KEY", "").strip(),
        "speech_key": os.getenv("AZURE_SPEECH_KEY", "").strip(),
        # Stage layout. pet_x/pet_y are percentages of the stage, pet_scale is
        # a multiplier of the native animation height.
        "pet_scale": 1.0,
        "pet_x": 70.0,
        "pet_y": 55.0,
        "pet_flip": False,
        "pet_shadow": True,
        "show_brand": True,
        "feed_enabled": True,
        "feed_position": "left",
        "background_dim": 0.35,
        "layout_version": LAYOUT_VERSION,
        # Background music.
        "music_enabled": True,
        "music_track": "",
        "music_volume": 0.6,
        # 说话时背景音乐压到多少（1 = 不压）。参考项目的做法：语音和 BGM 各走
        # 一条音轨，靠这个系数让音乐让一让，而不是直接停掉。
        "music_duck": 0.35,
        "music_loop": True,
        "idle_chatter_enabled": True,
        "idle_chatter_interval": DEFAULT_CHATTER_INTERVAL,
        # 无人互动多少分钟后宠物睡觉（0 = 不睡）。睡着时服务端也不再生成自言自语。
        "sleep_after": 20,
        # 舞台左下角状态面板：可隐藏、换角、调透明度和大小。
        "hud_enabled": True,
        "hud_position": "left-bottom",
        "hud_opacity": 0.72,
        "hud_scale": 1.0,
        # 游戏内一天压缩成多少分钟（0 = 关闭作息）。60 表示现实 1 小时 = 宠物的一整天。
        "day_cycle_minutes": 60,
        "chatter_version": CHATTER_VERSION,
        "memory_enabled": True,
        "memory_turns": 6,
        "douyin_room_id": os.getenv("DOUYIN_ROOM_ID", "").strip(),
        "douyin_bridge_url": os.getenv("DOUYIN_BRIDGE_URL", "http://127.0.0.1:8757").strip(),
        "background": "",
    }
    if path.exists():
        try:
            saved = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("Could not read runtime settings") from exc
        if not isinstance(saved, dict):
            raise RuntimeError("Runtime settings must be an object")
        original = dict(saved)
        saved = _migrate(saved)
        settings.update(saved)
        if saved != original:
            # Persist one-time migrations (old chatter interval, new memory /
            # Douyin fields) so the file matches what the UI is using.
            try:
                save_settings(path, settings)
            except OSError:
                pass
    return settings


def _migrate(saved):
    """Keep old settings files useful after the stage layout was reworked."""
    if saved.get("layout_version", 1) < LAYOUT_VERSION:
        saved = dict(saved)
        try:
            old_scale = float(saved.get("pet_scale", 1.0))
        except (TypeError, ValueError):
            old_scale = 1.0
        # The old stage scaled a letterboxed 480x270 frame inside a
        # min(68vh, 620px) box. The new stage sizes the animation directly,
        # so convert roughly to the same on-screen height.
        saved["pet_scale"] = round(max(0.6, min(2.4, old_scale * 0.65)), 2)
        saved.setdefault("pet_x", 70.0)
        saved.setdefault("pet_y", 55.0)
        saved["layout_version"] = LAYOUT_VERSION
    if saved.get("chatter_version", 1) < CHATTER_VERSION:
        saved = dict(saved)
        try:
            interval = int(float(saved.get("idle_chatter_interval", 15)))
        except (TypeError, ValueError):
            interval = 15
        if interval == 30:
            interval = 15
        # v6: 互动改走独立回复，闲聊只做填空，静默门槛整体拉长到一分钟起。
        # 想要更热闹可以把控制台的滑杆调小，这里只保证老配置不再 10 秒一句。
        saved["idle_chatter_interval"] = max(DEFAULT_CHATTER_INTERVAL, interval)
        saved["chatter_version"] = CHATTER_VERSION
    return saved


def save_settings(path, settings):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(".tmp")
    temporary_path.write_text(
        json.dumps(settings, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary_path.replace(path)


def public_settings(settings):
    return {
        "llm_base_url": settings["llm_base_url"],
        "llm_model": settings["llm_model"],
        "persona": settings["persona"],
        "api_key_set": bool(settings["llm_api_key"]),
        "bili_room_id": settings["bili_room_id"],
        "sessdata_set": bool(settings["bili_sessdata"]),
        "speech_enabled": settings["speech_enabled"],
        "speech_provider": settings["speech_provider"],
        "speech_voice": settings["speech_voice"],
        "speech_rate": settings["speech_rate"],
        "speech_pitch": settings["speech_pitch"],
        "speech_volume": settings["speech_volume"],
        "speech_base_url": settings["speech_base_url"],
        "speech_model": settings["speech_model"],
        "speech_openai_voice": settings["speech_openai_voice"],
        "speech_region": settings["speech_region"],
        "speech_api_key_set": bool(settings["speech_api_key"]),
        "speech_key_set": bool(settings["speech_key"]),
        "pet_scale": settings["pet_scale"],
        "pet_x": settings["pet_x"],
        "pet_y": settings["pet_y"],
        "pet_flip": settings["pet_flip"],
        "pet_shadow": settings["pet_shadow"],
        "show_brand": settings["show_brand"],
        "feed_enabled": settings["feed_enabled"],
        "feed_position": settings["feed_position"],
        "background_dim": settings["background_dim"],
        "music_enabled": settings["music_enabled"],
        "music_track": settings["music_track"],
        "music_volume": settings["music_volume"],
        "music_duck": settings["music_duck"],
        "music_loop": settings["music_loop"],
        "idle_chatter_enabled": settings["idle_chatter_enabled"],
        "idle_chatter_interval": settings["idle_chatter_interval"],
        "sleep_after": settings["sleep_after"],
        "day_cycle_minutes": settings["day_cycle_minutes"],
        "hud_enabled": settings["hud_enabled"],
        "hud_position": settings["hud_position"],
        "hud_opacity": settings["hud_opacity"],
        "hud_scale": settings["hud_scale"],
        "memory_enabled": settings["memory_enabled"],
        "memory_turns": settings["memory_turns"],
        "douyin_room_id": settings["douyin_room_id"],
        "douyin_bridge_url": settings["douyin_bridge_url"],
        "background": settings["background"],
    }


def clamp_number(value, minimum, maximum):
    if isinstance(value, bool):
        raise ValueError("boolean is not a number")
    number = float(value)
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise ValueError("number out of range")
    return number
