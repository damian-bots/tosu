"""AnonXMusic/platforms/Twitch.py — Twitch VODs & clips via API-2."""
import re, logging
from typing import Optional
import config
LOGGER = logging.getLogger(__name__)

_VOD_RE = re.compile(
    r"(?i)https?:\/\/(?:www\.|m\.)?twitch\.tv\/(?:videos|[\w._-]+\/video)\/\d+"
)
_CLIP_RE = re.compile(
    r"(?i)https?:\/\/(?:www\.|m\.)?(?:"
    r"twitch\.tv\/clip\/[\w-]+|"
    r"clips\.twitch\.tv\/[\w-]+|"
    r"twitch\.tv\/[\w-]+\/clip\/[\w-]+"
    r")"
)

class TwitchAPI:
    async def valid(self, link: str) -> bool:
        if not link: return False
        return bool(_VOD_RE.search(link) or _CLIP_RE.search(link))

    def _api(self):
        from AnonXMusic.platforms.Api import ApiPlatform
        return ApiPlatform()

    def _ready(self) -> bool:
        return bool(config.API_URL2 and config.API_KEY2)

    async def track(self, url: str):
        """Returns (track_details, track_id) — title is the VOD/clip title."""
        if not self._ready(): return None
        return await self._api().track(url)

    async def download(self, url: str, video: bool = True) -> Optional[str]:
        """
        For Twitch the API-2 usually returns an HLS stream URL.
        ApiPlatform.download() passes it through directly so ntgcalls can play it.
        """
        if not self._ready(): return None
        return await self._api().download(url, video=video)
