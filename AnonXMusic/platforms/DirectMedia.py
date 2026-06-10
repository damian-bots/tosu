# ╔══════════════════════════════════════════════════════════════════╗
# ║        Copyright © tusar404 — All Rights Reserved               ║
# ║     AnonXMusic · Telegram Music Bot · Powered by PyTgCalls      ║
# ║        Unauthorized copying or distribution is prohibited        ║
# ╚══════════════════════════════════════════════════════════════════╝

"""
DirectMedia.py — Handler for raw HTTP/HTTPS URLs and M3U8 streams.

Modelled after TgMusicBot's direct_link.go approach:
  1. Validate the URL looks like HTTP/HTTPS.
  2. Use ffprobe (subprocess) to verify the URL is playable and extract metadata.
  3. Return the URL directly as the stream path (no download needed for M3U8).
  4. For direct media files, return the URL so pytgcalls/ffmpeg can stream it.

This mirrors how TgMusicBot's `direct_link.go` works — using ffprobe for
validation and returning the raw URL as the CDN/play URL.
"""

import asyncio
import json
import re
from urllib.parse import urlparse, unquote

import aiohttp

_MEDIA_EXTENSIONS = {
    ".mp3", ".flac", ".wav", ".ogg", ".opus", ".aac", ".m4a",
    ".mp4", ".mkv", ".webm", ".avi", ".mov", ".ts",
    ".m3u8", ".m3u",
}

_FFPROBE_TIMEOUT = 10  # seconds

_PATH_TITLE_RE = re.compile(r"([^/?#]+)(?:\.[a-zA-Z0-9]{2,5})?$")


def _is_valid_url(url: str) -> bool:
    """Return True if the string is a http/https URL."""
    try:
        parsed = urlparse(url)
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except Exception:
        return False


def _is_m3u8(url: str) -> bool:
    clean = url.split("?")[0].lower()
    return clean.endswith(".m3u8") or clean.endswith(".m3u")


def _title_from_url(url: str) -> str:
    """Extract a human-readable title from the URL path."""
    try:
        path = urlparse(url).path
        match = _PATH_TITLE_RE.search(path)
        if match:
            raw = unquote(match.group(1))
            return re.sub(r"[-_]+", " ", raw).strip().title() or "Direct Stream"
    except Exception:
        pass
    return "Direct Stream"


async def _ffprobe_info(url: str) -> dict | None:
    """
    Run ffprobe against the URL to verify it is playable and get metadata.
    Returns a dict with keys: title, duration_sec, is_live, codec_type
    Returns None if ffprobe fails or URL is unplayable.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffprobe",
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=_FFPROBE_TIMEOUT)
        except asyncio.TimeoutError:
            proc.kill()
            return None

        if proc.returncode != 0 or not stdout:
            return None

        data = json.loads(stdout.decode("utf-8", errors="replace"))
        fmt = data.get("format", {})
        streams = data.get("streams", [])

        duration_sec = 0
        raw_dur = fmt.get("duration", "")
        if raw_dur and raw_dur not in ("N/A", ""):
            try:
                duration_sec = int(float(raw_dur))
            except (ValueError, TypeError):
                pass

        is_live = duration_sec == 0

        tags = fmt.get("tags", {})
        title = (
            tags.get("title")
            or tags.get("Title")
            or ""
        )
        if not title:
            title = _title_from_url(url)

        has_video = any(s.get("codec_type") == "video" for s in streams)
        codec_type = "video" if has_video else "audio"

        return {
            "title": title[:60],
            "duration_sec": duration_sec,
            "is_live": is_live,
            "codec_type": codec_type,
        }
    except Exception:
        return None


async def _head_check(url: str) -> bool:
    """
    Quick HEAD request to confirm the URL is reachable.
    Used as a fast pre-check before the heavier ffprobe call.
    """
    try:
        timeout = aiohttp.ClientTimeout(total=8)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.head(url, allow_redirects=True) as resp:
                return resp.status < 400
    except Exception:
        return False


class DirectMediaAPI:
    """
    Handles raw HTTP/HTTPS media URLs including M3U8 streams.

    Usage in play.py:
        from AnonXMusic.platforms.DirectMedia import DirectMediaAPI
        DirectMedia = DirectMediaAPI()

        if DirectMedia.is_valid(url):
            info = await DirectMedia.get_info(url)
            ...stream info["url"] directly...
    """

    @staticmethod
    def is_valid(url: str) -> bool:
        """Check if the URL looks like a direct media link worth attempting."""
        if not _is_valid_url(url):
            return False
        if _is_m3u8(url):
            return True
        path_lower = urlparse(url).path.lower()
        for ext in _MEDIA_EXTENSIONS:
            if path_lower.endswith(ext):
                return True
        return True

    async def get_info(self, url: str) -> dict | None:
        """
        Validate the URL and extract metadata.

        Returns a dict compatible with how play.py expects `details`:
          {
            "title": str,
            "link": str,
            "url": str,        # the raw URL — used as the stream path
            "duration_min": str | None,
            "duration_sec": int,
            "thumb": None,
            "is_live": bool,
            "stream_type": "m3u8" | "direct",
          }

        Returns None if the URL is invalid or unplayable.
        """
        if not _is_valid_url(url):
            return None

        if not _is_m3u8(url):
            reachable = await _head_check(url)
            if not reachable:
                return None

        info = await _ffprobe_info(url)
        if info is None:
            return None

        duration_sec = info["duration_sec"]
        if duration_sec > 0:
            mins = duration_sec // 60
            secs = duration_sec % 60
            duration_min = f"{mins:02d}:{secs:02d}"
        else:
            duration_min = None  # live stream

        stream_type = "m3u8" if _is_m3u8(url) else "direct"

        return {
            "title": info["title"],
            "link": url,
            "url": url,
            "duration_min": duration_min,
            "duration_sec": duration_sec,
            "thumb": None,
            "is_live": info["is_live"],
            "stream_type": stream_type,
            "codec_type": info["codec_type"],
        }
