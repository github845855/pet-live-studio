"""Small local background-music library backed by the runtime folder.

Tracks are stored as ``<id><ext>`` files with an ``index.json`` describing the
display title, so uploaded file names never reach the filesystem and the UI can
show a nice name even after the original file is gone.
"""

import json
import re
import time
import uuid
from pathlib import Path
from urllib.parse import unquote

MIME_TYPES = {
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".opus": "audio/ogg",
    ".wav": "audio/wav",
    ".flac": "audio/flac",
    ".webm": "audio/webm",
}

MAX_TITLE = 80
# "歌手 - 歌名.mp3" 这种最常见的命名，导入时顺手拆开，省得在控制台里一首首改名。
# 破折号两边必须有空格，否则 "night-vibe" 这种带连字符的标题会被误拆。
ARTIST_SPLIT = re.compile(r"^(.{1,40}?)\s+[-–—]\s+(.{1,60})$")


def sniff_audio(content):
    """Return a file extension for a supported audio container, or None."""
    if len(content) < 12:
        return None
    if content[:3] == b"ID3" or content[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return ".mp3"
    if content[:4] == b"OggS":
        return ".ogg"
    if content[:4] == b"RIFF" and content[8:12] == b"WAVE":
        return ".wav"
    if content[:4] == b"fLaC":
        return ".flac"
    if content[4:8] == b"ftyp":
        return ".m4a"
    if content[:4] == b"\x1a\x45\xdf\xa3":
        return ".webm"
    if content[:2] in (b"\xff\xf1", b"\xff\xf9"):
        return ".aac"
    return None


def clean_title(filename):
    stem = Path(filename or "").stem
    # Some multipart clients percent-encode non-ASCII filenames.
    if "%" in stem:
        stem = unquote(stem)
    stem = re.sub(r"[\x00-\x1f\x7f]", " ", stem)
    stem = " ".join(stem.split())
    return (stem or "背景音乐")[:MAX_TITLE]


def parse_track_name(filename):
    """Return ``(title, artist)`` parsed from a file name."""
    stem = clean_title(filename)
    match = ARTIST_SPLIT.match(stem)
    if match:
        return match.group(2).strip(), match.group(1).strip()
    return stem, ""


class MusicLibrary:
    def __init__(self, directory):
        self.directory = Path(directory)

    @property
    def index_file(self):
        return self.directory / "index.json"

    def _read_raw(self):
        try:
            raw = json.loads(self.index_file.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return []
        return raw if isinstance(raw, list) else []

    def _read_index(self):
        tracks = []
        for item in self._read_raw():
            if not isinstance(item, dict):
                continue
            track_id = item.get("id")
            ext = item.get("ext")
            if not isinstance(track_id, str) or not re.fullmatch(r"[a-f0-9]{12}", track_id):
                continue
            if ext not in MIME_TYPES or not self._file(track_id, ext).is_file():
                continue
            tracks.append({
                "id": track_id,
                "title": str(item.get("title") or "背景音乐")[:MAX_TITLE],
                "by": str(item.get("by") or "")[:MAX_TITLE],
                "ext": ext,
                "size": self._file(track_id, ext).stat().st_size,
                "created": float(item.get("created") or 0),
            })
        return tracks

    def _write_index(self, tracks):
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.index_file.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(tracks, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(self.index_file)

    def _file(self, track_id, ext):
        return self.directory / f"{track_id}{ext}"

    def tracks(self):
        """Return the current track list, pruning records whose file vanished."""
        raw = self._read_raw()
        tracks = self._read_index()
        if len(tracks) != len(raw):
            self._write_index(tracks)
        return tracks

    def add(self, content, filename):
        ext = sniff_audio(content)
        if ext is None:
            raise ValueError("Use an MP3, M4A, AAC, OGG, WAV, FLAC, or WebM audio file")
        self.directory.mkdir(parents=True, exist_ok=True)
        tracks = self._read_index()
        existing = {track["id"] for track in tracks}
        track_id = uuid.uuid4().hex[:12]
        while track_id in existing:
            track_id = uuid.uuid4().hex[:12]
        self._file(track_id, ext).write_bytes(content)
        title, artist = parse_track_name(filename)
        track = {
            "id": track_id,
            "title": title,
            "by": artist,
            "ext": ext,
            "size": len(content),
            "created": time.time(),
        }
        tracks.append(track)
        self._write_index(tracks)
        return track

    def scan(self):
        """Index audio files copied straight into the folder (NAS / SMB / rsync).

        Files are renamed to ``<id><ext>`` so the index stays authoritative, but
        the display title comes from the original name ("歌手 - 歌名").
        """
        self.directory.mkdir(parents=True, exist_ok=True)
        tracks = self._read_index()
        known = {self._file(track["id"], track["ext"]).name for track in tracks}
        added = []
        for path in sorted(self.directory.iterdir()):
            if not path.is_file() or path.name in known:
                continue
            ext = path.suffix.lower()
            if ext not in MIME_TYPES:
                continue
            stem = path.stem
            title, artist = parse_track_name(path.name)
            if re.fullmatch(r"[a-f0-9]{12}", stem):
                track_id = stem
            else:
                track_id = uuid.uuid4().hex[:12]
                while self._file(track_id, ext).exists():
                    track_id = uuid.uuid4().hex[:12]
                path.replace(self._file(track_id, ext))
            try:
                size = self._file(track_id, ext).stat().st_size
            except OSError:
                continue
            track = {
                "id": track_id,
                "title": title,
                "by": artist,
                "ext": ext,
                "size": size,
                "created": time.time(),
            }
            tracks.append(track)
            added.append(track)
        if added:
            self._write_index(tracks)
        return added

    def remove(self, track_id):
        tracks = self._read_index()
        kept = []
        removed = None
        for track in tracks:
            if track["id"] == track_id:
                removed = track
                self._file(track["id"], track["ext"]).unlink(missing_ok=True)
            else:
                kept.append(track)
        if removed is None:
            return None
        self._write_index(kept)
        return removed

    def get(self, track_id):
        for track in self._read_index():
            if track["id"] == track_id:
                return track, self._file(track["id"], track["ext"])
        return None, None

    def rename(self, track_id, title):
        tracks = self._read_index()
        for track in tracks:
            if track["id"] == track_id:
                track["title"] = clean_title(title)
                self._write_index(tracks)
                return track
        return None

    def mime(self, track):
        return MIME_TYPES.get(track["ext"], "application/octet-stream")

    def url(self, track_id):
        return f"/api/music/{track_id}"

    def ensure_valid_track(self, settings):
        """Clear the selected track when its file was deleted outside the UI."""
        track_id = settings.get("music_track") or ""
        if not track_id:
            return
        track, _ = self.get(track_id)
        if track is None:
            settings["music_track"] = ""


def track_public(track):
    return {
        "id": track["id"],
        "title": track["title"],
        "by": track.get("by") or "",
        "ext": track["ext"],
        "size": track["size"],
        "url": f"/api/music/{track['id']}",
    }
