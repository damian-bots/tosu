"""AnonXMusic/platforms/MXPlayer.py — MX Player via API-2."""
import re, logging
from typing import Optional
import config
LOGGER = logging.getLogger(__name__)

_RE = re.compile(r"(?i)https?:\/\/(?:www\.)?mxplayer\.in\/(?:show|movie)\/.*")

class MXPlayerAPI:
    async def valid(self, link: str) -> bool:
        return bool(_RE.search(link)) if link else False

    def _api(self):
        from AnonXMusic.platforms.Api import ApiPlatform
        return ApiPlatform()

    def _ready(self) -> bool:
        return bool(config.API_URL2 and config.API_KEY2)

    async def track(self, url: str):
        if not self._ready(): return None
        return await self._api().track(url)

    async def download(self, url: str, video: bool = True) -> Optional[str]:
        if not self._ready(): return None
        return await self._api().download(url, video=video)
