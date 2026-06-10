# ╔══════════════════════════════════════════════════════════════════╗
# ║        Copyright © tusar404 — All Rights Reserved               ║
# ║     AnonXMusic · Telegram Music Bot · Powered by PyTgCalls      ║
# ║        Unauthorized copying or distribution is prohibited        ║
# ╚══════════════════════════════════════════════════════════════════╝

"""
AnonXMusic/platforms/Apple.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
Apple Music handler — now powered by API-2.

Falls back to the original HTML-scrape + YouTube-search approach when
API-2 is unavailable, so the bot keeps working without config changes.
"""

import re
import logging
from typing import Optional, Union

import aiohttp
from bs4 import BeautifulSoup
from youtubesearchpython.__future__ import VideosSearch

import config

LOGGER = logging.getLogger(__name__)

_APPLE_RE = re.compile(
    r"(?i)^https?:\/\/music\.apple\.com\/[a-zA-Z-]+"
    r"\/(?:song\/(?:[^\/]+\/)?\d+|album\/[^\/]+\/\d+(?:\?i=\d+)?"
    r"|playlist\/[^\/]+\/pl\.[\w.-]+|artist\/[^\/]+\/\d+)(?:\?.*)?$"
)

class AppleAPI:
    def __init__(self) -> None:
        self.regex = r"^(https:\/\/music.apple.com\/)(.*)$"
        self.base = "https://music.apple.com/in/playlist/"

    async def valid(self, link: str) -> bool:
        return bool(_APPLE_RE.match(link.strip())) if link else False

    def _api(self):
        from AnonXMusic.platforms.Api import ApiPlatform
        return ApiPlatform()

    def _api_ready(self) -> bool:
        return bool(config.API_URL2 and config.API_KEY2)

    async def track(self, url: str, playid: Union[bool, str] = None):
        if playid:
            url = self.base + url

        if self._api_ready():
            result = await self._api().track(url)
            if result:
                return result

        return await self._scrape_track(url)

    async def _scrape_track(self, url: str):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        return False
                    html = await response.text()
            soup = BeautifulSoup(html, "html.parser")
            search = None
            for tag in soup.find_all("meta"):
                if tag.get("property") == "og:title":
                    search = tag.get("content")
            if not search:
                return False
            results = VideosSearch(search, limit=1)
            for result in (await results.next())["result"]:
                title = result["title"]
                ytlink = result["link"]
                vidid = result["id"]
                duration_min = result["duration"]
                thumbnail = result["thumbnails"][0]["url"].split("?")[0]
            track_details = {
                "title": title, "link": ytlink, "vidid": vidid,
                "duration_min": duration_min, "thumb": thumbnail,
            }
            return track_details, vidid
        except Exception as e:
            LOGGER.error(f"Apple scrape_track failed: {e}")
            return False

    async def playlist(self, url: str, playid: Union[bool, str] = None):
        if playid:
            url = self.base + url

        if self._api_ready():
            result = await self._api().playlist(url)
            if result:
                return result

        return await self._scrape_playlist(url)

    async def _scrape_playlist(self, url: str):
        try:
            playlist_id = url.split("playlist/")[1] if "playlist/" in url else url
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status != 200:
                        return False
                    html = await response.text()
            soup = BeautifulSoup(html, "html.parser")
            applelinks = soup.find_all("meta", attrs={"property": "music:song"})
            results = []
            for item in applelinks:
                try:
                    xx = (((item["content"]).split("album/")[1]).split("/")[0]).replace("-", " ")
                except Exception:
                    xx = ((item["content"]).split("album/")[1]).split("/")[0]
                results.append(xx)
            return results, playlist_id
        except Exception as e:
            LOGGER.error(f"Apple scrape_playlist failed: {e}")
            return False

    async def album(self, url: str):
        if self._api_ready():
            result = await self._api().album(url)
            if result:
                return result
        return [], url.split("/")[-1].split("?")[0]

    async def artist(self, url: str):
        if self._api_ready():
            result = await self._api().artist(url)
            if result:
                return result
        return [], url.split("/")[-1].split("?")[0]

    async def download(self, url: str, video: bool = False) -> Optional[str]:
        if not self._api_ready():
            return None
        return await self._api().download(url, video=video)
