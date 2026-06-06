"""
AnonXMusic/platforms/Spotify.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Spotify platform handler — now powered by API-2 (API_URL2 / API_KEY2).

Old behaviour:
  Used spotipy + YouTube search as a two-step workaround.

New behaviour (mirrors TgMusicBot api.go):
  - Fetches real track / playlist / album / artist metadata via API-2
  - Downloads the actual Spotify CDN audio with AES-CTR decryption
  - Falls back to the existing YouTube.download() path on error
    (so the bot keeps working even if API-2 is unavailable)
"""

import re
import logging
from typing import Optional

import config

LOGGER = logging.getLogger(__name__)

_SPOTIFY_RE = re.compile(
    r"(?i)^(https?://)?([a-z0-9-]+\.)*spotify\.com"
    r"/(track|playlist|album|artist)/[a-zA-Z0-9]+(\?.*)?$"
)


class SpotifyAPI:
    def __init__(self) -> None:
        # Keep the old regex so callers using self.regex still work
        self.regex = r"^(https:\/\/open.spotify.com\/)(.*)$"

    # ── validity ───────────────────────────────────────────────
    async def valid(self, link: str) -> bool:
        return bool(_SPOTIFY_RE.match(link.strip())) if link else False

    def _api(self):
        """Lazy import to avoid circular imports at module load time."""
        from AnonXMusic.platforms.Api import ApiPlatform
        return ApiPlatform()

    # ── single track ───────────────────────────────────────────
    async def track(self, link: str):
        """
        Returns (track_details_dict, track_id).
        track_details_dict keys: title, link, vidid, duration_min, thumb
        """
        result = await self._api().track(link)
        if result:
            return result
        LOGGER.warning("Spotify.track: API-2 failed, falling back to YouTube search")
        return await self._yt_fallback_track(link)

    async def _yt_fallback_track(self, link: str):
        """Last-resort: search YouTube by track title scraped from the URL."""
        from youtubesearchpython.__future__ import VideosSearch
        # We can at least parse the Spotify track ID from the URL and search
        # using whatever title the URL path suggests.
        slug = link.rstrip("/").split("/")[-1].split("?")[0]
        query = slug.replace("-", " ")
        results = VideosSearch(query, limit=1)
        for result in (await results.next())["result"]:
            ytlink = result["link"]
            title = result["title"]
            vidid = result["id"]
            duration_min = result["duration"]
            thumbnail = result["thumbnails"][0]["url"].split("?")[0]
        track_details = {
            "title": title,
            "link": ytlink,
            "vidid": vidid,
            "duration_min": duration_min,
            "thumb": thumbnail,
        }
        return track_details, vidid

    # ── playlist ───────────────────────────────────────────────
    async def playlist(self, url: str):
        """Returns (list_of_search_queries, playlist_id)."""
        result = await self._api().playlist(url)
        if result:
            return result
        return [], url.split("/")[-1].split("?")[0]

    # ── album ──────────────────────────────────────────────────
    async def album(self, url: str):
        """Returns (list_of_search_queries, album_id)."""
        result = await self._api().album(url)
        if result:
            return result
        return [], url.split("/")[-1].split("?")[0]

    # ── artist ─────────────────────────────────────────────────
    async def artist(self, url: str):
        """Returns (list_of_search_queries, artist_id)."""
        result = await self._api().artist(url)
        if result:
            return result
        return [], url.split("/")[-1].split("?")[0]

    # ── download ───────────────────────────────────────────────
    async def download(self, url: str, video: bool = False) -> Optional[str]:
        """
        Download a Spotify track via API-2 (encrypted CDN → AES-CTR → OGG).
        Returns local file path on success, None on failure.
        """
        if not config.API_URL2 or not config.API_KEY2:
            LOGGER.warning("Spotify.download: API_URL2/API_KEY2 not set")
            return None
        return await self._api().download(url, video=video)
