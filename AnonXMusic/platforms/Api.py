from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from typing import Optional

import aiohttp
import aiofiles

import config
from AnonXMusic.utils.database import increment_api_usage
from AnonXMusic.utils.error_logger import error_logger
from AnonXMusic.utils.formatters import seconds_to_min

LOGGER = logging.getLogger(__name__)

_PATTERNS: dict[str, re.Pattern] = {
    "apple": re.compile(
        r"(?i)^https?:\/\/music\.apple\.com\/[a-zA-Z-]+"
        r"\/(?:song\/(?:[^\/]+\/)?\d+|album\/[^\/]+\/\d+(?:\?i=\d+)?"
        r"|playlist\/[^\/]+\/pl\.[\w.-]+|artist\/[^\/]+\/\d+)(?:\?.*)?$"
    ),
    "spotify": re.compile(
        r"(?i)^(https?://)?([a-z0-9-]+\.)*spotify\.com"
        r"/(track|playlist|album|artist)/[a-zA-Z0-9]+(\?.*)?$"
    ),
    "jiosaavn": re.compile(
        r"(?i)https?:\/\/(?:www\.)?jiosaavn\.com\/(song|album|playlist|featured)"
        r"\/[^\/]+\/([A-Za-z0-9_]+)"
    ),
    "deezer": re.compile(
        r"(?i)https?:\/\/(?:www\.)?deezer\.com\/(?:[a-z]{2}\/)?"
        r"(track|album|playlist)\/(\d+)"
    ),
    "soundcloud": re.compile(
        r"(?i)^(https?://)?(www\.)?soundcloud\.com/[a-zA-Z0-9_-]+"
        r"/(sets/)?[a-zA-Z0-9._-]+(\?.*)?$"
    ),
    "gaana": re.compile(
        r"(?i)https?:\/\/(?:www\.)?gaana\.com\/(song|album|playlist|artist)"
        r"\/([A-Za-z0-9\-]+)"
    ),
    "tidal": re.compile(
        r"(?i)https?:\/\/(?:www\.|listen\.)?tidal\.com\/(?:browse\/)?"
        r"(track|album|playlist)\/([a-zA-Z0-9-]+)(?:[\/?].*)?"
    ),
    "mxplayer": re.compile(
        r"(?i)https?:\/\/(?:www\.)?mxplayer\.in\/(?:show|movie)\/.*"
    ),
    "twitch": re.compile(
        r"(?i)https?:\/\/(?:www\.|m\.)?twitch\.tv\/(?:videos|[\w._-]+\/video)\/\d+"
    ),
    "twitchclip": re.compile(
        r"(?i)https?:\/\/(?:www\.|m\.)?(?:"
        r"twitch\.tv\/clip\/[\w-]+|"
        r"clips\.twitch\.tv\/[\w-]+|"
        r"twitch\.tv\/[\w-]+\/clip\/[\w-]+"
        r")"
    ),
    "kick": re.compile(
        r"(?i)https?:\/\/(?:www\.)?kick\.com\/[\w._-]+\/videos\/[a-fA-F0-9-]+"
    ),
    "kickclip": re.compile(
        r"(?i)https?:\/\/(?:www\.)?kick\.com\/[\w._-]+\/clips\/[\w-]+"
    ),
}

_ENCRYPTED_PLATFORMS = {"spotify"}

CHUNK = 1024 * 1024
DOWNLOAD_TIMEOUT = 120
DOWNLOADS_DIR = os.path.join(os.getcwd(), "downloads")

_session: Optional[aiohttp.ClientSession] = None
_session_lock = asyncio.Lock()

async def _get_session() -> aiohttp.ClientSession:
    global _session
    if _session and not _session.closed:
        return _session
    async with _session_lock:
        if _session and not _session.closed:
            return _session
        connector = aiohttp.TCPConnector(limit=50, ttl_dns_cache=300)
        timeout = aiohttp.ClientTimeout(total=30, sock_connect=10, sock_read=20)
        _session = aiohttp.ClientSession(connector=connector, timeout=timeout)
        return _session

def _decrypt_spotify(data: bytes, hex_key: str) -> bytes:
    """Decrypt Spotify CDN audio with AES-128-CTR."""
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.backends import default_backend

    key = bytes.fromhex(hex_key)
    iv = bytes.fromhex("72e067fbddcbcf77ebe8bc643f630d93")
    cipher = Cipher(algorithms.AES(key), modes.CTR(iv), backend=default_backend())
    dec = cipher.decryptor()
    return dec.update(data) + dec.finalize()

def _rebuild_ogg(data: bytearray) -> bytearray:
    """
    Patch OGG/Vorbis headers in-memory (mirrors rebuildOGG in spotify_dl.go).
    The Go version patches specific byte offsets that Spotify scrambles.
    """
    patches: dict[int, bytes] = {
        0:  b"OggS",
        6:  b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00",
        26: b"\x01\x1E\x01vorbis",
        39: b"\x02",
        40: b"\x44\xAC\x00\x00",
        48: b"\x00\xE2\x04\x00",
        56: b"\xB8\x01",
        58: b"OggS",
        62: b"\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00",
    }
    for offset, patch in patches.items():
        end = offset + len(patch)
        if end <= len(data):
            data[offset:end] = patch
    return data

async def _fix_ogg_ffmpeg(src: str, dst: str) -> bool:
    """Re-mux OGG with ffmpeg (mirrors fixOGG in spotify_dl.go)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", src, "-c", "copy", dst,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.communicate(), timeout=30)
        return os.path.exists(dst) and os.path.getsize(dst) > 0
    except Exception as e:
        LOGGER.error(f"ffmpeg fixOGG failed: {e}")
        return False

async def _stream_to_file(url: str, path: str) -> bool:
    """Stream-download a URL to *path*. Returns True on success."""
    os.makedirs(DOWNLOADS_DIR, exist_ok=True)
    tmp = path + ".part"
    try:
        session = await _get_session()
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT)) as resp:
            if resp.status != 200:
                return False
            async with aiofiles.open(tmp, "wb") as f:
                async for chunk in resp.content.iter_chunked(CHUNK):
                    if chunk:
                        await f.write(chunk)
        os.replace(tmp, path)
        return os.path.exists(path) and os.path.getsize(path) > 0
    except Exception as e:
        LOGGER.error(f"Stream download failed: {e}")
        try:
            os.remove(tmp)
        except OSError:
            pass
        return False

async def _download_spotify_track(cdn_url: str, hex_key: str, track_id: str) -> Optional[str]:
    """
    Download + AES-CTR decrypt a Spotify CDN track, rebuild OGG headers,
    then re-mux with ffmpeg — mirrors processSpotify() in spotify_dl.go.
    """
    os.makedirs(DOWNLOADS_DIR, exist_ok=True)
    safe_id = os.path.basename(track_id)
    out_ogg = os.path.join(DOWNLOADS_DIR, f"{safe_id}.ogg")

    if os.path.exists(out_ogg) and os.path.getsize(out_ogg) > 0:
        return out_ogg

    enc_path = os.path.join(DOWNLOADS_DIR, f"{safe_id}.encrypted")
    dec_path = os.path.join(DOWNLOADS_DIR, f"{safe_id}_decrypted.ogg")

    try:
        session = await _get_session()
        async with session.get(cdn_url, timeout=aiohttp.ClientTimeout(total=DOWNLOAD_TIMEOUT)) as resp:
            if resp.status != 200:
                LOGGER.error(f"Spotify CDN returned {resp.status}")
                return None
            raw = await resp.read()

        async with aiofiles.open(enc_path, "wb") as f:
            await f.write(raw)

        t0 = time.monotonic()
        decrypted = _decrypt_spotify(raw, hex_key)
        LOGGER.info(f"Spotify decrypt took {(time.monotonic()-t0)*1000:.0f}ms")

        patched = _rebuild_ogg(bytearray(decrypted))
        async with aiofiles.open(dec_path, "wb") as f:
            await f.write(bytes(patched))

        ok = await _fix_ogg_ffmpeg(dec_path, out_ogg)
        if not ok:
            os.replace(dec_path, out_ogg)

        return out_ogg if os.path.exists(out_ogg) else None

    except ImportError:
        LOGGER.error(
            "cryptography package missing — install it: pip install cryptography"
        )
        return None
    except Exception as e:
        LOGGER.error(f"Spotify download/decrypt failed: {e}")
        return None
    finally:
        for p in (enc_path, dec_path):
            try:
                os.remove(p)
            except OSError:
                pass

async def _download_direct(cdn_url: str, track_id: str, ext: str = "mp3") -> Optional[str]:
    """Direct (unencrypted) CDN download — used for all non-Spotify platforms."""
    os.makedirs(DOWNLOADS_DIR, exist_ok=True)
    safe_id = os.path.basename(track_id) if track_id else "track"
    out_path = os.path.join(DOWNLOADS_DIR, f"{safe_id}.{ext}")
    if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
        return out_path
    ok = await _stream_to_file(cdn_url, out_path)
    return out_path if ok else None

class ApiPlatform:
    """
    Unified wrapper for all non-YouTube platforms via API-2.

    Constructor:
        ApiPlatform()  — reads API_URL2 / API_KEY2 from config at call time.

    Public helpers mirror TgMusicBot's DownloaderWrapper interface:
        valid(url)           → bool
        platform_of(url)     → str  (e.g. "spotify", "apple", …)
        get_info(url)        → PlatformTracks dict  (playlist / album / artist)
        get_track(url)       → TrackInfo dict        (single track + CDN URL)
        search(query)        → list[TrackInfo]       (up to 5 results)
        download(url, video) → file_path str
    """

    def __init__(self) -> None:
        self._patterns = _PATTERNS

    @property
    def _api_url(self) -> str:
        return config.API_URL2.rstrip("/") if config.API_URL2 else ""

    @property
    def _api_key(self) -> str:
        return config.API_KEY2 or ""

    @property
    def _ready(self) -> bool:
        return bool(self._api_url and self._api_key)

    def _headers(self) -> dict:
        return {"X-API-Key": self._api_key, "Accept": "application/json"}

    def valid(self, url: str) -> bool:
        if not url:
            return False
        for pat in self._patterns.values():
            if pat.match(url.strip()):
                return True
        return False

    def platform_of(self, url: str) -> str:
        """Return the short platform name for *url*, or empty string."""
        for name, pat in self._patterns.items():
            if pat.match(url.strip()):
                if name in ("twitchclip",):
                    return "twitch"
                if name in ("kickclip",):
                    return "kick"
                return name
        return ""

    async def _get(self, endpoint: str, params: dict) -> Optional[dict]:
        if not self._ready:
            return None
        url = f"{self._api_url}{endpoint}"
        try:
            session = await _get_session()
            async with session.get(
                url, params=params, headers=self._headers(),
                timeout=aiohttp.ClientTimeout(total=20),
            ) as resp:
                if resp.status != 200:
                    LOGGER.warning(f"API-2 {endpoint} → HTTP {resp.status}")
                    return None
                return await resp.json(content_type=None)
        except Exception as e:
            LOGGER.error(f"API-2 GET {endpoint} failed: {e}")
            return None

    @error_logger(label="API get_info")
    async def get_info(self, url: str) -> Optional[dict]:
        """
        Fetch playlist / album / artist metadata.
        Maps to GET /api/get_url?url=<url>
        Returns the raw PlatformTracks dict from the API.
        """
        return await self._get("/api/get_url", {"url": url})

    async def get_track(self, url: str) -> Optional[dict]:
        """
        Fetch single-track info including CDN URL (and key for Spotify).
        Maps to GET /api/track?url=<url>
        Returns the raw TrackInfo dict from the API.
        """
        return await self._get("/api/track", {"url": url})

    async def search(self, query: str, limit: int = 5) -> Optional[dict]:
        """
        Search the platform for *query*.
        Maps to GET /api/search?query=<query>&limit=<limit>
        Returns the raw PlatformTracks dict from the API.
        """
        return await self._get("/api/search", {"query": query, "limit": str(limit)})

    async def track(self, url: str) -> Optional[tuple[dict, str]]:
        """
        Returns (track_details_dict, track_id) like the old Spotify.track(),
        so existing play.py call-sites work without change.

        track_details_dict keys: title, link, vidid, duration_min, thumb

        We use /api/get_url (-> PlatformTracks with full MusicTrack metadata)
        instead of /api/track (-> TrackInfo which only carries the CDN URL and key,
        *no* duration / title / thumbnail).  This is exactly how TgMusicBot's own
        play.go works: it calls wrapper.GetInfo() to get metadata, then downloads
        separately via wrapper.GetTrack() + DownloadTrack().
        """
        data = await self.get_info(url)
        if not data:
            return None

        results = data.get("results") or []
        if not results:
            return None

        song = results[0]
        track_id = song.get("id") or url
        duration_sec = song.get("duration") or 0
        if isinstance(duration_sec, int) and duration_sec > 0:
            duration_min = seconds_to_min(duration_sec)
        else:
            duration_min = None

        details = {
            "title":        song.get("title") or song.get("name") or "Unknown",
            "link":         song.get("url") or url,
            "vidid":        track_id,
            "duration_min": duration_min,
            "thumb":        song.get("thumbnail") or "",
            "channel":      song.get("channel") or "",
            "views":        song.get("views") or "",
            "_url":         url,
        }
        return details, track_id

    @staticmethod
    def _extract_music_tracks(data: dict) -> list[dict]:
        """
        Extract a list of MusicTrack dicts from a PlatformTracks API response.

        Mirrors Go's PlatformTracks.Results []MusicTrack exactly:
          MusicTrack { title, id, url, thumbnail, duration (int secs),
                       channel, views, platform }

        The API always returns the top-level key "results" for /api/get_url.
        We also check "tracks" and "items" for compatibility with any
        platform wrapper that uses a different key.
        """
        raw = (
            data.get("results")
            or data.get("tracks")
            or data.get("items")
            or []
        )
        tracks: list[dict] = []
        for t in raw:
            if not isinstance(t, dict):
                continue
            title = (
                t.get("title")
                or t.get("name")
                or t.get("track_name")
                or ""
            )
            if not title:
                title = "Unknown Track"
            tracks.append({
                "title":     title,
                "id":        t.get("id") or "",
                "url":       t.get("url") or "",
                "thumbnail": t.get("thumbnail") or "",
                "duration":  int(t.get("duration") or 0),
                "channel":   t.get("channel") or "",
                "views":     t.get("views") or "",
                "platform":  t.get("platform") or "",
            })
        return tracks

    @error_logger(label="API Playlist Fetch")
    async def playlist(self, url: str) -> Optional[tuple[list[dict], str]]:
        """
        Returns (list_of_MusicTrack_dicts, playlist_id).

        Each dict mirrors Go's MusicTrack:
          { title, id, url, thumbnail, duration (int secs), channel, views, platform }

        Callers (stream.py playlist handler) should use track["url"] directly
        for API-2 platforms and fall back to YouTube search using track["title"]
        only when track["url"] is empty.
        """
        data = await self.get_info(url)
        if not data:
            return None
        tracks = self._extract_music_tracks(data)
        return tracks, url

    async def album(self, url: str) -> Optional[tuple[list[dict], str]]:
        """Same shape as playlist() — reuses get_info."""
        return await self.playlist(url)

    async def artist(self, url: str) -> Optional[tuple[list[dict], str]]:
        """Top tracks for an artist page."""
        data = await self.get_info(url)
        if not data:
            return None
        tracks = self._extract_music_tracks(data)
        return tracks, url

    @error_logger(label="API Track Download")
    async def download(self, url: str, video: bool = False) -> Optional[str]:
        """
        Full download pipeline:
          1. Fetch CDN info from API-2 (/api/track → TrackInfo)
          2. If Spotify + key → AES-CTR decrypt (mirrors spotify_dl.go)
          3. For live/stream platforms (Twitch, Kick, MX Player) → return HLS URL directly
          4. Otherwise → direct CDN stream download

        Note: /api/track returns TrackInfo {Id, URL, CdnURL, Key, Platform}.
              /api/get_url returns PlatformTracks with full MusicTrack metadata.
              We call /api/track here for the CDN URL only; metadata comes from track().
        Returns local file path (or live stream URL) on success, None on failure.
        """
        info = await self.get_track(url)
        if not info:
            LOGGER.error(f"API-2: get_track returned nothing for {url}")
            return None

        cdn_url: str = info.get("cdnurl") or info.get("cdn_url") or info.get("url") or ""
        if not cdn_url:
            LOGGER.error(f"API-2: no CDN URL in track info for {url}")
            return None

        platform: str = (info.get("platform") or self.platform_of(url)).lower()
        hex_key: str = info.get("key") or ""
        track_id: str = info.get("id") or os.path.basename(cdn_url.split("?")[0])

        if platform in _ENCRYPTED_PLATFORMS and hex_key:
            LOGGER.info(f"API-2: Spotify encrypted download for {track_id}")
            result = await _download_spotify_track(cdn_url, hex_key, track_id)
            if result:
                await increment_api_usage("api_2")
            return result

        stream_platforms = {"twitch", "twitchclip", "kick", "kickclip", "mxplayer"}
        if platform in stream_platforms:
            LOGGER.info(f"API-2: stream URL passthrough for {platform}")
            await increment_api_usage("api_2")
            return cdn_url

        ext = "mp4" if video else "mp3"
        result = await _download_direct(cdn_url, track_id, ext)
        if result:
            await increment_api_usage("api_2")
        return result
