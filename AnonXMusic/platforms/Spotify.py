"""
AnonXMusic/platforms/Spotify.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Spotify platform handler with three-tier metadata resolution:

  Tier 1 – API-2 (API_URL2 / API_KEY2)
    Full CDN download with AES-CTR decryption.

  Tier 2 – spotipy (SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET)
    Fetches track metadata (title, duration, artist, thumbnail) from the
    official Spotify Web API, then hands off to YouTube for audio.
    Used automatically when API-2 is unavailable or returns nothing.

  Tier 3 – URL-slug YouTube search
    Last resort: derives a search query from the Spotify URL slug.

For playlists / albums / artists the fallback builds a list of
(title, artist) search queries that stream.py resolves via YouTube.
"""

import asyncio
import logging
import re
from typing import Optional

import config

LOGGER = logging.getLogger(__name__)

_SPOTIFY_RE = re.compile(
    r"(?i)^(https?://)?([a-z0-9-]+\.)*spotify\.com"
    r"/(track|playlist|album|artist)/[a-zA-Z0-9]+(\?.*)?$"
)

# ── spotipy helper ────────────────────────────────────────────────────────────

def _sp_client():
    """
    Return an authenticated spotipy.Spotify instance using client credentials.
    Returns None if spotipy is not installed or credentials are missing.
    """
    if not (config.SPOTIFY_CLIENT_ID and config.SPOTIFY_CLIENT_SECRET):
        return None
    try:
        import spotipy
        from spotipy.oauth2 import SpotifyClientCredentials
        auth = SpotifyClientCredentials(
            client_id=config.SPOTIFY_CLIENT_ID,
            client_secret=config.SPOTIFY_CLIENT_SECRET,
        )
        return spotipy.Spotify(auth_manager=auth)
    except Exception as exc:
        LOGGER.warning(f"Spotify: spotipy client init failed: {exc}")
        return None


def _spotipy_track_details(sp_track: dict) -> dict:
    """
    Convert a raw spotipy track dict into the standard details dict used
    throughout the bot.  Works for both full track objects and the
    simplified objects inside playlist/album items.
    """
    # Spotipy wraps playlist track items in {"track": {...}}
    t = sp_track.get("track") or sp_track

    title     = t.get("name") or "Unknown"
    artists   = ", ".join(a["name"] for a in (t.get("artists") or []))
    duration_ms = t.get("duration_ms") or 0
    duration_sec = duration_ms // 1000
    album     = t.get("album") or {}
    images    = album.get("images") or []
    thumbnail = images[0]["url"] if images else ""
    track_id  = t.get("id") or ""
    track_url = t.get("external_urls", {}).get("spotify", "") or ""

    from AnonXMusic.utils.formatters import seconds_to_min
    duration_min = seconds_to_min(duration_sec) if duration_sec > 0 else "0:00"

    return {
        "title":        f"{title} {artists}".strip() if artists else title,
        "clean_title":  title,
        "artist":       artists,
        "link":         track_url,
        "vidid":        track_id,
        "duration_min": duration_min,
        "duration_sec": duration_sec,
        "thumb":        thumbnail,
        "_source":      "spotipy",
    }


async def _spotipy_playlist_tracks(url: str) -> list[dict]:
    """
    Fetch all tracks from a Spotify playlist/album/artist via spotipy.
    Returns a list of track-detail dicts (same shape as _spotipy_track_details).
    Returns [] on any error.
    """
    sp = await asyncio.get_event_loop().run_in_executor(None, _sp_client)
    if not sp:
        return []

    try:
        playlist_id = url.rstrip("/").split("/")[-1].split("?")[0]

        if "/playlist/" in url:
            raw = await asyncio.get_event_loop().run_in_executor(
                None, lambda: sp.playlist_tracks(playlist_id, limit=100)
            )
            items = raw.get("items") or []
            tracks = [_spotipy_track_details(i) for i in items if i.get("track")]

        elif "/album/" in url:
            raw = await asyncio.get_event_loop().run_in_executor(
                None, lambda: sp.album_tracks(playlist_id, limit=50)
            )
            items = raw.get("items") or []
            # album tracks don't carry album art — fetch album separately
            album_info = await asyncio.get_event_loop().run_in_executor(
                None, lambda: sp.album(playlist_id)
            )
            album_images = (album_info.get("images") or [])
            album_thumb = album_images[0]["url"] if album_images else ""

            from AnonXMusic.utils.formatters import seconds_to_min
            tracks = []
            for t in items:
                title    = t.get("name") or "Unknown"
                artists  = ", ".join(a["name"] for a in (t.get("artists") or []))
                dur_sec  = (t.get("duration_ms") or 0) // 1000
                tracks.append({
                    "title":        f"{title} {artists}".strip() if artists else title,
                    "clean_title":  title,
                    "artist":       artists,
                    "link":         t.get("external_urls", {}).get("spotify", ""),
                    "vidid":        t.get("id") or "",
                    "duration_min": seconds_to_min(dur_sec) if dur_sec > 0 else "0:00",
                    "duration_sec": dur_sec,
                    "thumb":        album_thumb,
                    "_source":      "spotipy",
                })

        elif "/artist/" in url:
            raw = await asyncio.get_event_loop().run_in_executor(
                None, lambda: sp.artist_top_tracks(playlist_id)
            )
            items = raw.get("tracks") or []
            tracks = [_spotipy_track_details(t) for t in items]

        else:
            return []

        return [t for t in tracks if t.get("title")]

    except Exception as exc:
        LOGGER.warning(f"Spotify: spotipy playlist fetch failed: {exc}")
        return []


# ── Main SpotifyAPI class ─────────────────────────────────────────────────────

class SpotifyAPI:
    def __init__(self) -> None:
        self.regex = r"^(https:\/\/open.spotify.com\/)(.*)$"

    # ── validity ───────────────────────────────────────────────────────────
    async def valid(self, link: str) -> bool:
        return bool(_SPOTIFY_RE.match(link.strip())) if link else False

    def _api(self):
        from AnonXMusic.platforms.Api import ApiPlatform
        return ApiPlatform()

    # ── single track ───────────────────────────────────────────────────────
    async def track(self, link: str):
        """
        Returns (track_details_dict, track_id).

        Tier-1: API-2
        Tier-2: spotipy
        Tier-3: URL-slug YouTube search
        """
        # Tier 1 — API-2
        if config.API_URL2 and config.API_KEY2:
            try:
                result = await self._api().track(link)
                if result:
                    return result
            except Exception as exc:
                LOGGER.warning(f"Spotify.track API-2 failed: {exc}")

        # Tier 2 — spotipy
        try:
            sp = await asyncio.get_event_loop().run_in_executor(None, _sp_client)
            if sp:
                track_id = link.rstrip("/").split("/")[-1].split("?")[0]
                raw = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: sp.track(track_id)
                )
                if raw:
                    details = _spotipy_track_details(raw)
                    return details, details["vidid"] or track_id
        except Exception as exc:
            LOGGER.warning(f"Spotify.track spotipy fallback failed: {exc}")

        # Tier 3 — URL-slug search
        return await self._yt_fallback_track(link)

    async def _yt_fallback_track(self, link: str):
        from youtubesearchpython.__future__ import VideosSearch
        slug = link.rstrip("/").split("/")[-1].split("?")[0]
        query = slug.replace("-", " ")
        try:
            results = VideosSearch(query, limit=1)
            for result in (await results.next())["result"]:
                from AnonXMusic.utils.formatters import seconds_to_min
                track_details = {
                    "title":        result["title"],
                    "link":         result["link"],
                    "vidid":        result["id"],
                    "duration_min": result["duration"],
                    "thumb":        result["thumbnails"][0]["url"].split("?")[0],
                    "_source":      "yt_fallback",
                }
                return track_details, result["id"]
        except Exception as exc:
            LOGGER.warning(f"Spotify.track YT-slug fallback failed: {exc}")
        return None

    # ── playlist / album / artist ──────────────────────────────────────────
    async def playlist(self, url: str):
        """Returns (list_of_track_dicts, playlist_id)."""
        return await self._fetch_collection(url, "playlist")

    async def album(self, url: str):
        return await self._fetch_collection(url, "album")

    async def artist(self, url: str):
        return await self._fetch_collection(url, "artist")

    async def _fetch_collection(self, url: str, kind: str):
        """
        Three-tier collection fetch.

        API-2 returns list[MusicTrack dicts] with full metadata.
        spotipy returns list[detail dicts] with title/artist/duration.
        Tracks from spotipy are tagged _source="spotipy" so stream.py
        knows to resolve them via YouTube search using the title.
        """
        collection_id = url.rstrip("/").split("/")[-1].split("?")[0]

        # Tier 1 — API-2
        if config.API_URL2 and config.API_KEY2:
            try:
                result = await self._api().playlist(url)
                if result and result[0]:
                    return result
            except Exception as exc:
                LOGGER.warning(f"Spotify._fetch_collection API-2 failed: {exc}")

        # Tier 2 — spotipy
        try:
            tracks = await _spotipy_playlist_tracks(url)
            if tracks:
                LOGGER.info(
                    f"Spotify: spotipy fallback fetched {len(tracks)} tracks "
                    f"for {kind} {collection_id}"
                )
                return tracks, collection_id
        except Exception as exc:
            LOGGER.warning(f"Spotify._fetch_collection spotipy failed: {exc}")

        # Both tiers failed — return empty
        LOGGER.error(
            f"Spotify: both API-2 and spotipy failed for {kind} {url}"
        )
        return [], collection_id

    # ── download ───────────────────────────────────────────────────────────
    async def download(self, url: str, video: bool = False) -> Optional[str]:
        """
        Download a Spotify track via API-2.
        Returns local file path on success, None on failure.
        spotipy doesn't provide a download capability — we always need API-2.
        """
        if not config.API_URL2 or not config.API_KEY2:
            return None
        try:
            return await self._api().download(url, video=video)
        except Exception as exc:
            LOGGER.error(f"Spotify.download failed: {exc}")
            return None
