import asyncio
import os
import re
import json
import logging
from typing import Union, Dict, Optional
from urllib.parse import urlparse, unquote, quote

import aiofiles
import aiohttp
from aiohttp import TCPConnector
from motor.motor_asyncio import AsyncIOMotorClient
from pyrogram.enums import MessageEntityType
from pyrogram.types import Message
from youtubesearchpython.__future__ import VideosSearch

from AnonXMusic.utils.database import get_search_cache, set_search_cache
from AnonXMusic.utils.formatters import time_to_seconds

import config

MEDIA_DB_NAME = "arcapi"
MEDIA_COLLECTION_NAME = "medias"

DOWNLOAD_DIR = "downloads"
logger = logging.getLogger(__name__)

CHUNK_SIZE = 1024 * 1024
SEARCH_RETRIES = 4
JOB_POLL_ATTEMPTS = 10
JOB_POLL_INTERVAL = 3.0

HARD_TIMEOUT = 80
PROCESS_TIMEOUT = 80

_session: Optional[aiohttp.ClientSession] = None
_session_lock = asyncio.Lock()
_MONGO_CLIENT: Optional[AsyncIOMotorClient] = None


def _get_app():
    from AnonXMusic import app
    return app


async def get_http_session() -> aiohttp.ClientSession:
    global _session
    if _session and not _session.closed:
        return _session
    async with _session_lock:
        if _session and not _session.closed:
            return _session
        timeout = aiohttp.ClientTimeout(total=HARD_TIMEOUT, sock_connect=10, sock_read=30)
        connector = TCPConnector(limit=100, ttl_dns_cache=300, enable_cleanup_closed=True)
        _session = aiohttp.ClientSession(timeout=timeout, connector=connector)
        return _session


def _get_media_collection():
    global _MONGO_CLIENT
    db_uri = config.DB_URI
    if not db_uri:
        return None
    if _MONGO_CLIENT is None:
        _MONGO_CLIENT = AsyncIOMotorClient(db_uri)
    return _MONGO_CLIENT[MEDIA_DB_NAME][MEDIA_COLLECTION_NAME]


def _ensure_dir(p: str) -> None:
    os.makedirs(p, exist_ok=True)


def is_safe_url(text: str) -> bool:
    DANGEROUS_CHARS = [
        ";", "|", "$", "`", "\n", "\r", "(", ")",
        "<", ">", "{", "}", "\\", "'", '"',
    ]
    ALLOWED_DOMAINS = {
        "youtube.com", "www.youtube.com", "m.youtube.com",
        "youtu.be", "music.youtube.com", "open.spotify.com",
    }

    if not text:
        return False
    text = str(text).strip()
    is_url = text.lower().startswith(("http:", "https:", "www."))

    if not is_url:
        CRITICAL_SHELL = [";", "|", "$", "`", "{", "}", "\n", "\r"]
        try:
            decoded = unquote(text).lower()
            if any(c in decoded for c in CRITICAL_SHELL):
                logger.warning(f"BLOCKED MALICIOUS TEXT QUERY: {text}")
                return False
        except Exception:
            return False
        return True

    try:
        target_url = text
        if target_url.lower().startswith("www."):
            target_url = "https://" + target_url

        decoded_url = unquote(target_url)
        if any(char in decoded_url for char in DANGEROUS_CHARS):
            logger.warning(f"BLOCKED MALICIOUS INJECTION: {text}")
            return False

        parsed = urlparse(target_url)
        domain = parsed.netloc.replace("www.", "")
        if domain not in ALLOWED_DOMAINS:
            logger.warning(f"BLOCKED INVALID DOMAIN: {domain}")
            return False

        return True
    except Exception as e:
        logger.error(f"URL Validation Error: {e}")
        return False


YOUTUBE_REGEX = re.compile(
    r"(?:https?://)?(?:www\.|m\.|music\.)?"
    r"(?:youtube\.com/(?:watch\?v=|shorts/|playlist\?list=)|youtu\.be/)"
    r"([A-Za-z0-9_-]{11}|PL[A-Za-z0-9_-]+)(?:[&?][^\s]*)?"
)
YOUTUBE_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{11}$")


def extract_video_id(link: str) -> str:
    if not link:
        return ""
    s = str(link).strip()
    if YOUTUBE_ID_RE.match(s):
        return s
    m = YOUTUBE_REGEX.search(s)
    if m:
        extracted = m.group(1)
        if extracted and not extracted.startswith("PL") and YOUTUBE_ID_RE.match(extracted):
            return extracted
    candidate = ""
    if "v=" in s:
        candidate = s.split("v=")[-1].split("&")[0]
    else:
        candidate = s.split("/")[-1].split("?")[0]
    if YOUTUBE_ID_RE.match(candidate):
        return candidate
    return ""


class YouTubeAPI:
    def __init__(self):
        self.base = "https://www.youtube.com/watch?v="
        self.listbase = "https://youtube.com/playlist?list="
        self.regex = YOUTUBE_REGEX
        self.dl_cache: Dict[str, str] = {}
        self.v_cache: Dict[str, str] = {}

    # ── Database cache (Telegram channel) ──────────────────────────

    async def _fetch_media_msg_id(self, track_id: str, is_video: bool) -> Optional[int]:
        col = _get_media_collection()
        if col is None:
            return None
        fname = f"{track_id}.mp4" if is_video else f"{track_id}.mp3"
        try:
            doc = await col.find_one({"track_id": fname, "isVideo": is_video})
            if doc and doc.get("message_id"):
                return int(doc["message_id"])
        except Exception:
            pass
        return None

    async def _download_from_db(self, video_id: str, is_video: bool) -> Optional[str]:
        app = _get_app()
        ch_id = getattr(config, "MEDIA_CHANNEL_ID", None)
        if not ch_id or app is None:
            return None
        try:
            ch_id = int(ch_id)
        except (ValueError, TypeError):
            return None

        msg_id = await self._fetch_media_msg_id(video_id, is_video)
        if not msg_id:
            return None

        try:
            from pyrogram.errors import FloodWait
            try:
                msg = await app.get_messages(ch_id, msg_id)
            except FloodWait:
                return None
            if not msg:
                return None
            dl_path = await asyncio.wait_for(
                app.download_media(msg),
                timeout=HARD_TIMEOUT,
            )
            if dl_path and os.path.exists(dl_path):
                logger.info(f"DB-CACHE hit for {video_id}")
                return dl_path
        except Exception as e:
            logger.error(f"DB download failed for {video_id}: {e}")
        return None

    # ── API download (ArcAPI) ──────────────────────────────────────

    async def _api_create_job(self, video_id: str, is_video: bool) -> Optional[str]:
        api_url = getattr(config, "API_URL", None)
        api_key = getattr(config, "API_KEY", None)
        if not api_url or not api_key:
            return None

        endpoint = f"{api_url.rstrip('/')}/youtube/v2/download"
        params = {
            "api_key": api_key,
            "query": video_id,
            "isVideo": str(is_video).lower(),
        }

        session = await get_http_session()
        for _ in range(3):
            try:
                async with session.get(endpoint, params=params) as resp:
                    if resp.status != 200:
                        await asyncio.sleep(1)
                        continue
                    data = await resp.json()
                    if data.get("status") != "queued":
                        await asyncio.sleep(1)
                        continue
                    job_id = data.get("job_id")
                    if not job_id:
                        await asyncio.sleep(1)
                        continue
                    return job_id
            except Exception:
                await asyncio.sleep(1)
        return None

    async def _api_poll_job(self, job_id: str) -> Optional[str]:
        api_url = getattr(config, "API_URL", None)
        if not api_url:
            return None

        endpoint = f"{api_url.rstrip('/')}/youtube/jobStatus"
        params = {"job_id": job_id}

        session = await get_http_session()
        for attempt in range(1, JOB_POLL_ATTEMPTS + 1):
            try:
                async with session.get(endpoint, params=params) as resp:
                    if resp.status != 200:
                        await asyncio.sleep(JOB_POLL_INTERVAL)
                        continue
                    data = await resp.json()
                    if data.get("status") != "success":
                        await asyncio.sleep(JOB_POLL_INTERVAL)
                        continue
                    job = data.get("job", {})
                    if job.get("status") != "done":
                        await asyncio.sleep(JOB_POLL_INTERVAL)
                        continue
                    public_url = job.get("result", {}).get("public_url")
                    if not public_url:
                        break
                    logger.info(f"ArcApi: Received #{attempt} [{api_url + public_url}]")
                    return api_url.rstrip("/") + public_url
            except Exception:
                pass
            await asyncio.sleep(JOB_POLL_INTERVAL)
        return None

    async def _api_save_file(self, url: str) -> Optional[str]:
        fpath = os.path.join(DOWNLOAD_DIR, url.split("/")[-1])
        _ensure_dir(DOWNLOAD_DIR)
        try:
            session = await get_http_session()
            async with session.get(url, timeout=None) as resp:
                if resp.status != 200:
                    return None
                async with aiofiles.open(fpath, "wb") as f:
                    async for chunk in resp.content.iter_chunked(CHUNK_SIZE):
                        if chunk:
                            await f.write(chunk)
            return fpath
        except Exception as e:
            logger.error(f"Failed to save file from API: {e}")
        return None

    async def _api_download(self, video_id: str, is_video: bool) -> Optional[str]:
        for attempt in range(2):
            job_id = await self._api_create_job(video_id, is_video)
            if not job_id:
                if attempt == 0:
                    await asyncio.sleep(2)
                continue

            dl_url = await self._api_poll_job(job_id)
            if not dl_url:
                if attempt == 0:
                    await asyncio.sleep(2)
                continue

            fpath = await self._api_save_file(dl_url)
            if not fpath:
                if attempt == 0:
                    await asyncio.sleep(2)
                continue

            return fpath
        return None

    # ── Combined download (DB first, then API) ─────────────────────

    async def _full_download(self, video_id: str, is_video: bool) -> Optional[str]:
        if is_video and video_id in self.v_cache:
            return self.v_cache[video_id]
        if not is_video and video_id in self.dl_cache:
            return self.dl_cache[video_id]

        fpath = await self._download_from_db(video_id, is_video)
        if not fpath:
            fpath = await self._api_download(video_id, is_video)
        if not fpath:
            return None

        if is_video:
            self.v_cache[video_id] = fpath
        else:
            self.dl_cache[video_id] = fpath
        return fpath

    # ── Search methods ─────────────────────────────────────────────

    async def fast_search(self, query: str, fetch_all: bool = False) -> Union[Dict[str, str], list, None]:
        if not query:
            return None

        cached_data = await get_search_cache(query)
        if cached_data and not fetch_all:
            return cached_data

        url = f"https://www.youtube.com/results?search_query={quote(str(query))}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        }
        try:
            session = await get_http_session()
            async with session.get(url, headers=headers, timeout=7) as r:
                if r.status != 200:
                    return None
                html = await r.text()

            results_list = []

            try:
                json_str = None
                match = re.search(r'(?:var ytInitialData|window\["ytInitialData"\])\s*=\s*(\{)', html)
                if match:
                    start_idx = match.start(1)
                    brace_count = 0
                    in_string = False
                    escape_next = False

                    for i in range(start_idx, len(html)):
                        char = html[i]
                        if escape_next:
                            escape_next = False
                            continue
                        if char == '\\':
                            escape_next = True
                            continue
                        if char == '"':
                            in_string = not in_string
                            continue
                        if not in_string:
                            if char == '{':
                                brace_count += 1
                            elif char == '}':
                                brace_count -= 1
                            if brace_count == 0:
                                json_str = html[start_idx:i+1]
                                break

                if json_str:
                    data = json.loads(json_str)
                    contents = (
                        data.get("contents", {})
                        .get("twoColumnSearchResultsRenderer", {})
                        .get("primaryContents", {})
                        .get("sectionListRenderer", {})
                        .get("contents", [])
                    )
                    for section in contents:
                        if "itemSectionRenderer" in section:
                            for item in section["itemSectionRenderer"].get("contents", []):
                                if "videoRenderer" in item:
                                    vr = item["videoRenderer"]
                                    vid = vr.get("videoId")
                                    if vid and not any(r["video_id"] == vid for r in results_list):
                                        title = "Unknown Title"
                                        if "title" in vr and "runs" in vr["title"]:
                                            title = "".join([run["text"] for run in vr["title"]["runs"]])
                                        duration = "0:00"
                                        if "lengthText" in vr and "simpleText" in vr["lengthText"]:
                                            duration = vr["lengthText"]["simpleText"]
                                        thumb = f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
                                        if "thumbnail" in vr and "thumbnails" in vr["thumbnail"]:
                                            thumbs = vr["thumbnail"]["thumbnails"]
                                            if thumbs:
                                                thumb = thumbs[-1]["url"].split("?")[0]
                                        results_list.append({
                                            "video_id": vid,
                                            "title": title,
                                            "duration": duration,
                                            "thumbnail": thumb,
                                            "url": f"https://www.youtube.com/watch?v={vid}",
                                        })
            except Exception as e:
                logger.error(f"JSON Parsing failed: {e}")

            if not results_list:
                video_ids = re.findall(r'"videoRenderer":\{"videoId":"([a-zA-Z0-9_-]{11})"', html)
                seen = set()
                for vid in video_ids:
                    if vid not in seen:
                        seen.add(vid)
                        results_list.append({
                            "video_id": vid,
                            "title": "Unknown Title",
                            "duration": "0:00",
                            "thumbnail": f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg",
                            "url": f"https://www.youtube.com/watch?v={vid}",
                        })

            if not results_list:
                return None

            if fetch_all:
                return results_list

            best_result = results_list[0]
            await set_search_cache(query, best_result)
            return best_result

        except Exception as e:
            logger.error(f"Fast Search Failed: {e}")
            return None

    async def exists(self, link: str, videoid: Union[bool, str] = None) -> bool:
        if not link:
            return False
        link = str(link)
        if videoid:
            link = self.base + link
        if not is_safe_url(link):
            return False
        return bool(self.regex.match(link) or self.regex.search(link))

    async def url(self, message_1: Message) -> Union[str, None]:
        messages = [message_1]
        if message_1.reply_to_message:
            messages.append(message_1.reply_to_message)
        text = ""
        offset = None
        length = None
        for message in messages:
            if offset:
                break
            if message.entities:
                for entity in message.entities:
                    if entity.type == MessageEntityType.URL:
                        text = message.text or message.caption
                        offset, length = entity.offset, entity.length
                        break
            elif message.caption_entities:
                for entity in message.caption_entities:
                    if entity.type == MessageEntityType.TEXT_LINK:
                        return entity.url
        if offset is None:
            return None
        return text[offset: offset + length]

    async def details(self, link: str, videoid: Union[bool, str] = None):
        if not link:
            raise Exception("Track search query is empty.")
        link = str(link)
        if videoid:
            link = self.base + link
        if not is_safe_url(link):
            raise Exception("Blocked by Security Check.")

        cached = await get_search_cache(link)
        if cached:
            duration_sec = 0 if str(cached["duration"]) == "None" else int(time_to_seconds(cached["duration"]))
            return cached["title"], cached["duration"], duration_sec, cached["thumbnail"], cached["video_id"]

        vid = extract_video_id(link)
        last_error = None

        for attempt in range(SEARCH_RETRIES):
            try:
                if vid:
                    exact_url = f"https://www.youtube.com/watch?v={vid}"
                    results = VideosSearch(exact_url, limit=1)
                    res = await results.next()
                    if not res or not res.get("result"):
                        raise Exception("PySearch returned no data.")
                    result = res["result"][0]
                    title = result.get("title", "Unknown Title")
                    duration_min = result.get("duration", "0:00")
                    vidid = result.get("id", "")
                    thumbs = result.get("thumbnails", [])
                    thumbnail = thumbs[0]["url"].split("?")[0] if thumbs else getattr(config, "YOUTUBE_IMG_URL", "")
                else:
                    scrape_res = await self.fast_search(link)
                    if not scrape_res:
                        raise Exception("Scraper failed")
                    if scrape_res.get("title") and scrape_res["title"] != "Unknown Title":
                        title = scrape_res["title"]
                        duration_min = scrape_res["duration"]
                        vidid = scrape_res["video_id"]
                        thumbnail = scrape_res["thumbnail"]
                    else:
                        exact_url = f"https://www.youtube.com/watch?v={scrape_res['video_id']}"
                        results = VideosSearch(exact_url, limit=1)
                        res = await results.next()
                        if not res or not res.get("result"):
                            raise Exception("PySearch returned no data.")
                        result = res["result"][0]
                        title = result.get("title", "Unknown Title")
                        duration_min = result.get("duration", "0:00")
                        vidid = result.get("id", "")
                        thumbs = result.get("thumbnails", [])
                        thumbnail = thumbs[0]["url"].split("?")[0] if thumbs else getattr(config, "YOUTUBE_IMG_URL", "")

                duration_sec = 0 if str(duration_min) == "None" else int(time_to_seconds(duration_min))

                await set_search_cache(link, {
                    "title": title, "duration": duration_min, "video_id": vidid,
                    "thumbnail": thumbnail, "url": link,
                })
                return title, duration_min, duration_sec, thumbnail, vidid

            except Exception as e:
                last_error = e
                if attempt < SEARCH_RETRIES - 1:
                    await asyncio.sleep(0.5)

        logger.error(f"Details extraction failed for {link} after {SEARCH_RETRIES} attempts: {last_error}")
        raise Exception(f"Failed to fetch track details after {SEARCH_RETRIES} attempts: {last_error}")

    async def title(self, link: str, videoid: Union[bool, str] = None) -> str:
        if not link:
            return "Unknown Title"
        link = str(link)
        if videoid:
            link = self.base + link
        if not is_safe_url(link):
            return "Unknown Title"

        cached = await get_search_cache(link)
        if cached:
            return cached["title"]

        vid = extract_video_id(link)
        for attempt in range(SEARCH_RETRIES):
            try:
                if vid:
                    results = VideosSearch(f"https://www.youtube.com/watch?v={vid}", limit=1)
                    res = await results.next()
                    if res and res.get("result"):
                        return res["result"][0].get("title", "Unknown Title")
                else:
                    scrape_res = await self.fast_search(link)
                    if scrape_res and scrape_res.get("title") != "Unknown Title":
                        return scrape_res["title"]
                raise Exception("No result")
            except Exception:
                if attempt < SEARCH_RETRIES - 1:
                    await asyncio.sleep(0.5)
        return "Unknown Title"

    async def duration(self, link: str, videoid: Union[bool, str] = None) -> str:
        if not link:
            return "0:00"
        link = str(link)
        if videoid:
            link = self.base + link
        if not is_safe_url(link):
            return "0:00"

        cached = await get_search_cache(link)
        if cached:
            return cached["duration"]

        vid = extract_video_id(link)
        for attempt in range(SEARCH_RETRIES):
            try:
                if vid:
                    results = VideosSearch(f"https://www.youtube.com/watch?v={vid}", limit=1)
                    res = await results.next()
                    if res and res.get("result"):
                        return res["result"][0].get("duration", "0:00")
                else:
                    scrape_res = await self.fast_search(link)
                    if scrape_res and scrape_res.get("duration"):
                        return scrape_res["duration"]
                raise Exception("No result")
            except Exception:
                if attempt < SEARCH_RETRIES - 1:
                    await asyncio.sleep(0.5)
        return "0:00"

    async def thumbnail(self, link: str, videoid: Union[bool, str] = None) -> str:
        if not link:
            return getattr(config, "YOUTUBE_IMG_URL", "")
        link = str(link)
        if videoid:
            link = self.base + link
        if not is_safe_url(link):
            return getattr(config, "YOUTUBE_IMG_URL", "")

        cached = await get_search_cache(link)
        if cached:
            return cached["thumbnail"]

        vid = extract_video_id(link)
        for attempt in range(SEARCH_RETRIES):
            try:
                if vid:
                    results = VideosSearch(f"https://www.youtube.com/watch?v={vid}", limit=1)
                    res = await results.next()
                    if res and res.get("result"):
                        thumbs = res["result"][0].get("thumbnails", [])
                        return thumbs[0]["url"].split("?")[0] if thumbs else getattr(config, "YOUTUBE_IMG_URL", "")
                else:
                    scrape_res = await self.fast_search(link)
                    if scrape_res and scrape_res.get("thumbnail"):
                        return scrape_res["thumbnail"]
                raise Exception("No result")
            except Exception:
                if attempt < SEARCH_RETRIES - 1:
                    await asyncio.sleep(0.5)
        return getattr(config, "YOUTUBE_IMG_URL", "")

    async def video(self, link: str, videoid: Union[bool, str] = None):
        if not link:
            return 0, "No link provided"
        link = str(link)
        if videoid:
            link = self.base + link
        if not is_safe_url(link):
            return 0, "Unsafe URL"
        if "&" in link:
            link = link.split("&")[0]

        vid = extract_video_id(link)
        if not vid:
            return 0, "Could not extract video ID"

        try:
            fpath = await asyncio.wait_for(
                self._full_download(vid, is_video=True),
                timeout=PROCESS_TIMEOUT,
            )
            if fpath and os.path.exists(fpath):
                return 1, fpath
        except asyncio.TimeoutError:
            logger.error(f"Video download timed out: {vid}")
        except Exception as e:
            logger.error(f"Video download failed: {e}")
        return 0, "Download failed"

    async def playlist(self, link, limit, user_id, videoid: Union[bool, str] = None):
        if not link:
            return []
        link = str(link)
        if videoid:
            link = self.listbase + link
        if not is_safe_url(link):
            return []
        if "&" in link:
            link = link.split("&")[0]

        try:
            results = VideosSearch(link, limit=limit)
            res = await results.next()
            if not res or not res.get("result"):
                return []
            return [r.get("id", "") for r in res["result"] if r.get("id")]
        except Exception as e:
            logger.error(f"Playlist fetch failed: {e}")
            return []

    async def track(self, link: str, videoid: Union[bool, str] = None):
        if not link:
            raise Exception("No search query or link provided.")
        link = str(link)
        if videoid:
            link = self.base + link
        if not is_safe_url(link):
            raise Exception("Blocked by Security Check.")

        cached = await get_search_cache(link)
        if cached:
            return {
                "title": cached["title"], "link": cached["url"],
                "vidid": cached["video_id"], "duration_min": cached["duration"],
                "thumb": cached["thumbnail"],
            }, cached["video_id"]

        vid = extract_video_id(link)
        last_error = None

        for attempt in range(SEARCH_RETRIES):
            try:
                if vid:
                    exact_url = f"https://www.youtube.com/watch?v={vid}"
                    results = VideosSearch(exact_url, limit=1)
                    res = await results.next()
                    if not res or not res.get("result"):
                        raise Exception("Track not found or PySearch failed.")
                    result = res["result"][0]
                    yturl = result.get("link") or f"https://www.youtube.com/watch?v={result.get('id', '')}"
                    title = result.get("title", "Unknown Title")
                    duration_min = result.get("duration", "0:00")
                    vidid = result.get("id", "")
                    thumbs = result.get("thumbnails", [])
                    thumbnail = thumbs[0]["url"].split("?")[0] if thumbs else getattr(config, "YOUTUBE_IMG_URL", "")
                else:
                    scrape_res = await self.fast_search(link)
                    if not scrape_res:
                        raise Exception("Scraper failed")
                    if scrape_res.get("title") and scrape_res["title"] != "Unknown Title":
                        title = scrape_res["title"]
                        duration_min = scrape_res["duration"]
                        vidid = scrape_res["video_id"]
                        yturl = scrape_res["url"]
                        thumbnail = scrape_res["thumbnail"]
                    else:
                        exact_url = f"https://www.youtube.com/watch?v={scrape_res['video_id']}"
                        results = VideosSearch(exact_url, limit=1)
                        res = await results.next()
                        if not res or not res.get("result"):
                            raise Exception("Track not found.")
                        result = res["result"][0]
                        yturl = result.get("link") or f"https://www.youtube.com/watch?v={result.get('id', '')}"
                        title = result.get("title", "Unknown Title")
                        duration_min = result.get("duration", "0:00")
                        vidid = result.get("id", "")
                        thumbs = result.get("thumbnails", [])
                        thumbnail = thumbs[0]["url"].split("?")[0] if thumbs else getattr(config, "YOUTUBE_IMG_URL", "")

                if not is_safe_url(yturl):
                    raise Exception("Unsafe URL Returned from YouTube.")

                await set_search_cache(link, {
                    "title": title, "duration": duration_min, "video_id": vidid,
                    "thumbnail": thumbnail, "url": yturl,
                })

                track_details = {
                    "title": title, "link": yturl, "vidid": vidid,
                    "duration_min": duration_min, "thumb": thumbnail,
                }
                return track_details, vidid

            except Exception as e:
                last_error = e
                if attempt < SEARCH_RETRIES - 1:
                    await asyncio.sleep(0.5)

        logger.error(f"Track extraction failed for {link} after {SEARCH_RETRIES} attempts: {last_error}")
        raise Exception(f"Could not process track request after {SEARCH_RETRIES} attempts: {last_error}")

    async def slider(self, link: str, query_type: int, videoid: Union[bool, str] = None):
        if not link:
            raise Exception("Query is empty.")
        link = str(link)
        if videoid:
            link = self.base + link
        if not is_safe_url(link):
            raise Exception("Blocked by Security Check.")

        vid = extract_video_id(link)
        last_error = None

        for attempt in range(SEARCH_RETRIES):
            try:
                if not vid:
                    scraped_data = await self.fast_search(link, fetch_all=True)
                    if scraped_data and isinstance(scraped_data, list) and len(scraped_data) > query_type:
                        target = scraped_data[query_type]
                        if target.get("title") and target["title"] != "Unknown Title":
                            return target["title"], target["duration"], target["thumbnail"], target["video_id"]
                        exact_url = f"https://www.youtube.com/watch?v={target['video_id']}"
                        results = VideosSearch(exact_url, limit=1)
                        res = await results.next()
                        if res and res.get("result"):
                            result = res["result"][0]
                            title = result.get("title", "Unknown")
                            duration_min = result.get("duration", "0:00")
                            vidid = result.get("id", target["video_id"])
                            thumbs = result.get("thumbnails", [])
                            thumbnail = thumbs[0]["url"].split("?")[0] if thumbs else getattr(config, "YOUTUBE_IMG_URL", "")
                            return title, duration_min, thumbnail, vidid

                a = VideosSearch(link, limit=10)
                res = await a.next()
                if not res or not res.get("result") or len(res["result"]) <= query_type:
                    raise Exception("Slider data not found.")

                result = res.get("result")
                title = result[query_type].get("title", "Unknown")
                duration_min = result[query_type].get("duration", "0:00")
                vidid = result[query_type].get("id", "")
                thumbs = result[query_type].get("thumbnails", [])
                thumbnail = thumbs[0]["url"].split("?")[0] if thumbs else getattr(config, "YOUTUBE_IMG_URL", "")

                return title, duration_min, thumbnail, vidid
            except Exception as e:
                last_error = e
                if attempt < SEARCH_RETRIES - 1:
                    await asyncio.sleep(0.5)

        logger.error(f"Slider extraction failed for {link} after {SEARCH_RETRIES} attempts: {last_error}")
        raise Exception(f"Failed to load search options after {SEARCH_RETRIES} attempts: {last_error}")

    async def download(
        self,
        link: str,
        mystic,
        video: Union[bool, str] = None,
        videoid: Union[bool, str] = None,
        songaudio: Union[bool, str] = None,
        songvideo: Union[bool, str] = None,
        format_id: Union[bool, str] = None,
        title: Union[bool, str] = None,
    ) -> Union[str, tuple]:
        is_video = bool(video or songvideo)

        if not link:
            if songvideo or songaudio:
                return None
            return None, False

        link = str(link)
        if videoid:
            link = self.base + link
        if not is_safe_url(link):
            if songvideo or songaudio:
                return None
            return None, False
        if "&" in link:
            link = link.split("&")[0]

        vid = extract_video_id(link)
        if not vid:
            vid = link

        try:
            fpath = await asyncio.wait_for(
                self._full_download(vid, is_video),
                timeout=PROCESS_TIMEOUT,
            )
            if fpath and os.path.exists(fpath):
                if songvideo or songaudio:
                    return fpath
                return fpath, True
        except asyncio.TimeoutError:
            logger.error(f"Download timed out: {link}")
        except Exception as e:
            logger.error(f"Download failed: {e}")

        if songvideo or songaudio:
            return None
        return None, False
