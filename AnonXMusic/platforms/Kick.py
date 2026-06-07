"""AnonXMusic/platforms/Kick.py — Kick VODs & clips via API-2."""
import re, logging
from typing import Optional
import config
LOGGER = logging.getLogger(__name__)

_VOD_RE = re.compile(
    r"(?i)https?:\/\/(?:www\.)?kick\.com\/[\w._-]+\/videos\/[a-fA-F0-9-]+"
)
_CLIP_RE = re.compile(
    r"(?i)https?:\/\/(?:www\.)?kick\.com\/[\w._-]+\/clips\/[\w-]+"
)

class KickAPI:
    async def valid(self, link: str) -> bool:
        if not link: return False
        return bool(_VOD_RE.search(link) or _CLIP_RE.search(link))

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
