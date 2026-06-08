"""AnonXMusic/platforms/Gaana.py — Gaana via API-2.

Flow for single track:
  1. GET /api/get_url?url=<gaana_url>  → full metadata (title, duration, thumbnail, url)
  2. GET /api/track?url=<gaana_url>    → TrackInfo with cdnurl
  3. Download cdnurl directly (unencrypted mp3)

The /api/get_url step gives us the metadata we display.
The /api/track step gives us the actual CDN download URL.
This mirrors how TgMusicBot's Go code handles Gaana:
  getInfo() for metadata → getTrack() for CDN → downloadTrack().
"""
import logging
from typing import Optional
import config
LOGGER = logging.getLogger(__name__)


class GaanaAPI:
    def _api(self):
        from AnonXMusic.platforms.Api import ApiPlatform
        return ApiPlatform()

    def _ready(self) -> bool:
        return bool(config.API_URL2 and config.API_KEY2)

    async def valid(self, link: str) -> bool:
        if not link:
            return False
        return "gaana.com" in link.lower()

    async def track(self, url: str):
        """
        Returns (details_dict, track_id) using /api/get_url for metadata.
        details_dict contains title, link, vidid, duration_min, thumb.
        """
        if not self._ready():
            return None
        return await self._api().track(url)

    async def playlist(self, url: str):
        if not self._ready():
            return [], url
        result = await self._api().playlist(url)
        return result if result else ([], url)

    async def album(self, url: str):
        if not self._ready():
            return [], url
        result = await self._api().album(url)
        return result if result else ([], url)

    async def download(self, url: str, video: bool = False) -> Optional[str]:
        """
        Download a Gaana track:
          1. Call /api/track?url=<gaana_url> to get the CDN URL
          2. Stream-download the CDN URL to a local file

        This is the critical fix: previously the code called ApiPlatform.download()
        which also calls get_track() internally — but Gaana tracks on the API need
        the original Gaana page URL passed to /api/track, not a resolved CDN URL.
        """
        if not self._ready():
            return None
        api = self._api()
        # Step 1: get CDN info from /api/track
        info = await api.get_track(url)
        if not info:
            LOGGER.error(f"Gaana: /api/track returned nothing for {url}")
            return None

        cdn_url: str = (
            info.get("cdnurl")
        )
        if not cdn_url:
            LOGGER.error(f"Gaana: no CDN URL in /api/track response for {url}. Response: {info}")
            return None

        track_id: str = info.get("id") or url.rstrip("/").split("/")[-1]
        LOGGER.info(f"Gaana: downloading from CDN for track_id={track_id}")

        # Step 2: direct download (Gaana tracks are plain mp3, not encrypted)
        from AnonXMusic.platforms.Api import _download_direct
        result = await _download_direct(cdn_url, track_id, "mp3")
        if result:
            from AnonXMusic.utils.database import increment_api_usage
            await increment_api_usage("api_2")
        return result
