"""
AnonXMusic/platforms/Soundcloud.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
SoundCloud handler — now powered by API-2.
Falls back to yt-dlp download when API-2 is unavailable.
"""

import logging
import os
from os import path
from typing import Optional

from yt_dlp import YoutubeDL

import config
from AnonXMusic.utils.formatters import seconds_to_min

LOGGER = logging.getLogger(__name__)


class SoundAPI:
    def __init__(self) -> None:
        self.opts = {
            "outtmpl": "downloads/%(id)s.%(ext)s",
            "format": "best",
            "retries": 3,
            "nooverwrites": False,
            "continuedl": True,
        }

    async def valid(self, link: str) -> bool:
        return "soundcloud" in link.lower() if link else False

    def _api(self):
        from AnonXMusic.platforms.Api import ApiPlatform
        return ApiPlatform()

    def _api_ready(self) -> bool:
        return bool(getattr(config, "API_URL2", "") and getattr(config, "API_KEY2", ""))

    # ── track info ─────────────────────────────────────────────
    async def track(self, url: str):
        """Returns (track_details_dict, track_id) via API-2."""
        if self._api_ready():
            result = await self._api().track(url)
            if result:
                return result
        return None

    # ── download ───────────────────────────────────────────────
    async def download(self, url: str, video: bool = False) -> Optional[str]:
        """
        Download via API-2 if configured; otherwise fall back to yt-dlp.
        Returns (track_details_dict, file_path) to preserve old call-site shape,
        or just file_path string when called from the new stream handler.
        """
        if self._api_ready():
            file_path = await self._api().download(url, video=video)
            if file_path:
                return file_path

        # yt-dlp fallback (original behaviour)
        return await self._ytdlp_download(url)

    async def _ytdlp_download(self, url: str):
        """Legacy yt-dlp download — returns (track_details, filepath)."""
        import asyncio
        loop = asyncio.get_running_loop()
        try:
            info, xyz = await loop.run_in_executor(None, self._sync_ytdlp, url)
            return info, xyz
        except Exception as e:
            LOGGER.error(f"SoundCloud yt-dlp download failed: {e}")
            return False

    def _sync_ytdlp(self, url: str):
        d = YoutubeDL(self.opts)
        info = d.extract_info(url)
        xyz = path.join("downloads", f"{info['id']}.{info['ext']}")
        duration_min = seconds_to_min(info["duration"])
        track_details = {
            "title": info["title"],
            "duration_sec": info["duration"],
            "duration_min": duration_min,
            "uploader": info["uploader"],
            "filepath": xyz,
        }
        return track_details, xyz
