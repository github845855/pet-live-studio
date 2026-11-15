import asyncio
import html
import os
import re

import aiohttp

from core.llm import LocalLLM

AZURE_VOICES = (
    "zh-CN-XiaoxiaoNeural",
    "zh-CN-YunxiNeural",
    "zh-CN-YunyangNeural",
    "zh-CN-XiaoyiNeural",
)
AZURE_REGION_PATTERN = re.compile(r"^[a-z0-9-]{2,40}$")
AZURE_VOICE_PATTERN = re.compile(r"^[A-Za-z0-9-]{1,80}$")
MAX_SPEECH_CHARS = 180
MAX_SPEECH_BYTES = 5 * 1024 * 1024
OPENAI_SPEECH_FORMAT = "mp3"
# Local TTS services (CosyVoice, GPT-SoVITS, IndexTTS …) need far more than the
# five seconds a hosted API takes: measured on this machine a 40 character
# Chinese line costs 12-40 seconds and a 130 character line can exceed 75. The
# old 25 second budget silently dropped every long reply, which is exactly what
# "吞语音" looked like on the stage.
DEFAULT_SPEECH_TIMEOUT = 120.0
MIN_SPEECH_TIMEOUT = 10.0
MAX_SPEECH_TIMEOUT = 300.0
SPEECH_CONNECT_TIMEOUT = 8.0


class SpeechUpstreamError(RuntimeError):
    """Speech synthesis failed upstream.

    ``retryable`` marks failures that a second identical request may fix: a
    dropped connection, a 5xx, a rate limit or a response without audio. Bad
    configuration, 4xx and MP3 metadata without frames are deterministic, so
    retrying them would only delay the stage.
    """

    def __init__(self, message, *, retryable=False, status=None):
        super().__init__(message)
        self.retryable = retryable
        self.status = status


def speech_timeout(settings=None):
    """Per-request timeout for one synthesis call, in seconds."""
    raw = None
    if isinstance(settings, dict):
        raw = settings.get("speech_timeout")
    if raw in (None, ""):
        raw = os.getenv("SPEECH_TIMEOUT")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_SPEECH_TIMEOUT
    return min(MAX_SPEECH_TIMEOUT, max(MIN_SPEECH_TIMEOUT, value))


def speech_client_timeout(settings=None):
    return aiohttp.ClientTimeout(total=speech_timeout(settings), connect=SPEECH_CONNECT_TIMEOUT)


def detect_audio_mime(audio):
    """Guess the container from the payload itself.

    Local OpenAI-compatible wrappers ignore ``response_format`` often enough
    that returning a fixed ``audio/mpeg`` makes browsers decode garbage.
    """
    if not audio:
        return "audio/mpeg"
    if audio.startswith(b"ID3") or audio[:3] == b"\xff\xfb\x90":
        return "audio/mpeg"
    if len(audio) >= 2 and audio[0] == 0xFF and (audio[1] & 0xE0) == 0xE0:
        return "audio/mpeg"
    if audio.startswith(b"RIFF") and audio[8:12] == b"WAVE":
        return "audio/wav"
    if audio.startswith(b"OggS"):
        return "audio/ogg"
    if audio.startswith(b"fLaC"):
        return "audio/flac"
    if audio.startswith(b"\x1a\x45\xdf\xa3"):
        return "audio/webm"
    if len(audio) >= 12 and audio[4:8] == b"ftyp":
        return "audio/mp4"
    return "audio/mpeg"


def validate_speech_text(text):
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_SPEECH_CHARS:
        raise ValueError(
            f"Speech text must contain 1 to {MAX_SPEECH_CHARS} characters"
        )
    return text.strip()


def _mp3_has_no_frames(audio):
    if len(audio) < 10 or not audio.startswith(b"ID3"):
        return False
    tag_size = sum(
        (byte & 0x7F) << shift
        for byte, shift in zip(audio[6:10], (21, 14, 7, 0))
    )
    return len(audio) <= 10 + tag_size


def build_azure_ssml(text, voice, rate=1.0, pitch=1.0):
    text = validate_speech_text(text)
    if voice not in AZURE_VOICES or not AZURE_VOICE_PATTERN.fullmatch(voice):
        raise ValueError("Unsupported Azure Speech voice")
    try:
        rate = float(rate)
        pitch = float(pitch)
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid speech rate or pitch") from exc
    if not 0.5 <= rate <= 2 or not 0.5 <= pitch <= 2:
        raise ValueError("Speech rate and pitch must be between 0.5 and 2")
    rate_percent = round((rate - 1) * 100)
    pitch_percent = round((pitch - 1) * 50)
    return (
        '<speak version="1.0" xml:lang="zh-CN">'
        f'<voice name="{voice}"><prosody rate="{rate_percent:+d}%" '
        f'pitch="{pitch_percent:+d}%">{html.escape(text, quote=False)}</prosody></voice>'
        "</speak>"
    )


async def synthesize_azure_speech(session, text, settings):
    region = str(settings.get("speech_region") or "").strip().lower()
    key = str(settings.get("speech_key") or "").strip()
    if not AZURE_REGION_PATTERN.fullmatch(region):
        raise ValueError("Enter a valid Azure Speech region")
    if not key:
        raise ValueError("Enter an Azure Speech key")
    ssml = build_azure_ssml(
        text,
        settings.get("speech_voice") or AZURE_VOICES[0],
        settings.get("speech_rate", 1.0),
        settings.get("speech_pitch", 1.0),
    )
    endpoint = f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1"
    headers = {
        "Ocp-Apim-Subscription-Key": key,
        "Content-Type": "application/ssml+xml",
        "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3",
        "User-Agent": "Pet-Live-Studio",
    }
    try:
        async with session.post(
                endpoint,
                data=ssml.encode("utf-8"),
                headers=headers,
                allow_redirects=False,
                timeout=speech_client_timeout(settings)) as response:
            if response.status >= 400:
                raise SpeechUpstreamError(
                    f"Azure Speech returned HTTP {response.status}",
                    status=response.status,
                    retryable=response.status >= 500 or response.status == 429,
                )
            audio = await response.read()
    except asyncio.TimeoutError as exc:
        raise SpeechUpstreamError(
            f"Azure Speech timed out after {speech_timeout(settings):.0f}s"
        ) from exc
    except aiohttp.ClientError as exc:
        raise SpeechUpstreamError(
            f"Azure Speech request failed ({type(exc).__name__})",
            retryable=True,
        ) from exc
    if not audio:
        raise SpeechUpstreamError("Azure Speech returned empty audio", retryable=True)
    if len(audio) > MAX_SPEECH_BYTES:
        raise SpeechUpstreamError("Azure Speech response exceeded the size limit")
    return audio


async def synthesize_openai_speech(session, text, settings):
    text = validate_speech_text(text)
    base_url = str(settings.get("speech_base_url") or "").strip()
    model = str(settings.get("speech_model") or "").strip()
    voice = str(settings.get("speech_openai_voice") or "").strip()
    key = str(settings.get("speech_api_key") or "").strip()
    try:
        api_root = LocalLLM.validate_base_url(base_url)
    except ValueError as exc:
        raise ValueError("Enter a valid OpenAI-compatible speech API base URL") from exc
    if not model or len(model) > 120:
        raise ValueError("Enter a speech model")
    if not voice or len(voice) > 200:
        raise ValueError("Enter a speech voice")

    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    payload = {
        "model": model,
        "input": text,
        "voice": voice,
        "response_format": OPENAI_SPEECH_FORMAT,
        "speed": float(settings.get("speech_rate", 1.0)),
    }
    try:
        async with session.post(
                f"{api_root}/audio/speech",
                json=payload,
                headers=headers,
                allow_redirects=False,
                timeout=speech_client_timeout(settings)) as response:
            if not 200 <= response.status < 300:
                raise SpeechUpstreamError(
                    f"Speech API returned HTTP {response.status}",
                    status=response.status,
                    retryable=response.status >= 500 or response.status == 429,
                )
            audio = await response.content.read(MAX_SPEECH_BYTES + 1)
    except asyncio.TimeoutError as exc:
        raise SpeechUpstreamError(
            f"Speech API timed out after {speech_timeout(settings):.0f}s"
            " (raise SPEECH_TIMEOUT for a slow local TTS service)"
        ) from exc
    except aiohttp.ClientError as exc:
        raise SpeechUpstreamError(
            f"Speech API request failed ({type(exc).__name__})",
            retryable=True,
        ) from exc
    if not audio:
        raise SpeechUpstreamError("Speech API returned empty audio", retryable=True)
    if len(audio) > MAX_SPEECH_BYTES:
        raise SpeechUpstreamError("Speech API response exceeded the size limit")
    if _mp3_has_no_frames(audio):
        raise SpeechUpstreamError(
            "Speech API returned MP3 metadata without audio frames"
        )
    return audio
