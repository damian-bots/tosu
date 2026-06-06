"""AnonXMusic/platforms/Gaana.py — Gaana via API-2."""
import logging
from typing import Optional
import config
LOGGER = logging.getLogger(__name__)

class GaanaAPI:
    def _api(self):
        from AnonXMusic.platforms.Api import ApiPlatform
        return ApiPlatform()

    def _ready(self) -> bool:
        return bool(getattr(config, "API_URL2", "") and getattr(config, "API_KEY2", ""))

    async def valid(self, link: str) -> bool:
        if not link: return False
        name_lower = "Gaana".lower()
        return name_lower in link.lower()

    async def track(self, url: str):
        if not self._ready(): return None
        return await self._api().track(url)

    async def playlist(self, url: str):
        if not self._ready(): return [], url
        result = await self._api().playlist(url)
        return result if result else ([], url)

    async def album(self, url: str):
        if not self._ready(): return [], url
        result = await self._api().album(url)
        return result if result else ([], url)

    async def download(self, url: str, video: bool = False) -> Optional[str]:
        if not self._ready(): return None
        return await self._api().download(url, video=video)
