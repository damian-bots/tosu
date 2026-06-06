# AnonXMusic/platforms/Youtube.py
# API + Database downloading only (no yt-dlp)

import time
import asyncio
import os
import re
import json
import logging
from typing import Union, Dict, Optional, Any
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

# Lazy import to avoid circular dependency during package init
def _get_app():
    from AnonXMusic import app
    return app

MEDIA_DB_NAME = "arcapi"
MEDIA_COLLECTION_NAME = "medias"

DOWNLOAD_DIR = "downloads"
LOGGER = logging.getLogger(__name__)

CHUNK_SIZE = 1024 * 1024
SEARCH_RETRIES = 4
JOB_POLL_ATTEMPTS = 10
JOB_POLL_INTERVAL = 3.0
CDN_RETRIES = 5
CDN_RETRY_DELAY = 2

HARD_TIMEOUT = 80
PROCESS_TIMEOUT = 80

TG_FLOOD_COOLDOWN = 0.0

DOWNLOAD_STATS: Dict[str, int] = {
    "total": 0, "success": 0, "failed": 0,
    "success_audio": 0, "success_video": 0,
    "failed_audio": 0, "failed_video": 0,
    "search_total": 0, "search_success": 0, "search_failed": 0,
    "api_fail_5xx": 0, "network_fail": 0, "timeout_fail": 0,
    "no_candidate": 0, "tg_fail": 0, "tg_flood_skip": 0, "cdn_fail": 0,
    "media_db_hit": 0, "media_db_miss": 0, "media_db_fail": 0,
}

def get_download_stats() -> Dict[str, Any]:
    stats = dict(DOWNLOAD_STATS)
    a_tot = stats["success_audio"] + stats["failed_audio"]
    stats["audio_success_rate"] = f"{round((stats['success_audio'] / a_tot) * 100, 2)}%" if a_tot > 0 else "0%"
    v_tot = stats["success_video"] + stats["failed_video"]
    stats["video_success_rate"] = f"{round((stats['success_video'] / v_tot) * 100, 2)}%" if v_tot > 0 else "0%"
    dl_tot = stats["success"] + stats["failed"]
    stats["download_success_rate"] = f"{round((stats['success'] / dl_tot) * 100, 2)}%" if dl_tot > 0 else "0%"
    s_tot = stats["search_total"]
    stats["search_success_rate"] = f"{round((stats['search_success'] / s_tot) * 100, 2)}%" if s_tot > 0 else "0%"
    return stats

def reset_download_stats() -> None:
    for k in list(DOWNLOAD_STATS.keys()): DOWNLOAD_STATS[k] = 0

def _inc(key: str, n: int = 1) -> None:
    DOWNLOAD_STATS[key] = DOWNLOAD_STATS.get(key, 0) + n

_session: Optional[aiohttp.ClientSession] = None
_session_lock = asyncio.Lock()
_MONGO_CLIENT: Optional[AsyncIOMotorClient] = None

# In-memory download caches
_dl_cache: Dict[str, str] = {}
_v_cache: Dict[str, str] = {}

async def get_http_session() -> aiohttp.ClientSession:
    global _session
    if _session and not _session.closed: return _session
    async with _session_lock:
        if _session and not _session.closed: return _session
        timeout = aiohttp.ClientTimeout(total=HARD_TIMEOUT, sock_connect=10, sock_read=30)
        connector = TCPConnector(limit=100, ttl_dns_cache=300, enable_cleanup_closed=True)
        _session = aiohttp.ClientSession(timeout=timeout, connector=connector)
        return _session

def _get_media_collection():
    global _MONGO_CLIENT
    db_uri = config.DB_URI
    if not db_uri: return None
    if _MONGO_CLIENT is None: _MONGO_CLIENT = AsyncIOMotorClient(db_uri)
    db = _MONGO_CLIENT[MEDIA_DB_NAME]
    return db[MEDIA_COLLECTION_NAME]

async def is_media(track_id: str, isVideo: bool = False) -> bool:
    col = _get_media_collection()
    if col is None: return False
    try: return bool(await col.find_one({"track_id": track_id, "isVideo": isVideo}, {"_id": 1}))
    except Exception: return False

async def get_media_id(track_id: str, isVideo: bool = False) -> Optional[int]:
    col = _get_media_collection()
    if col is None: return None
    try:
        doc = await col.find_one({"track_id": track_id, "isVideo": isVideo}, {"message_id": 1})
        if doc and doc.get("message_id"): return int(doc.get("message_id"))
    except Exception: pass
    return None

def _ensure_dir(p: str) -> None: os.makedirs(p, exist_ok=True)


def is_safe_url(text: str) -> bool:
    DANGEROUS_CHARS = [
        ";", "|", "$", "`", "\n", "\r", "(", ")",
        "<", ">", "{", "}", "\\", "'", '"'
    ]
    ALLOWED_DOMAINS = {
        "youtube.com", "www.youtube.com", "m.youtube.com",
        "youtu.be", "music.youtube.com", "open.spotify.com"
    }

    if not text: return False
    text = str(text).strip()
    is_url = text.lower().startswith(("http:", "https:", "www."))

    if not is_url:
        CRITICAL_SHELL = [";", "|", "$", "`", "{", "}", "\n", "\r"]
        try:
            decoded = unquote(text).lower()
            if any(c in decoded for c in CRITICAL_SHELL):
                LOGGER.warning(f"BLOCKED MALICIOUS TEXT QUERY: {text}")
                return False
        except: return False
        return True

    try:
        target_url = text
        if target_url.lower().startswith("www."):
            target_url = "https://" + target_url

        decoded_url = unquote(target_url)

        if any(char in decoded_url for char in DANGEROUS_CHARS):
            LOGGER.warning(f"BLOCKED MALICIOUS INJECTION: {text}")
            return False

        parsed = urlparse(target_url)
        domain = parsed.netloc.replace("www.", "")

        if domain not in ALLOWED_DOMAINS:
            LOGGER.warning(f"BLOCKED INVALID DOMAIN: {domain}")
            return False

        return True
    except Exception as e:
        LOGGER.error(f"URL Validation Error: {e}")
        return False

YOUTUBE_REGEX = re.compile(
    r"(?:https?://)?(?:www\.|m\.|music\.)?"
    r"(?:youtube\.com/(?:watch\?v=|shorts/|playlist\?list=)|youtu\.be/)"
    r"([A-Za-z0-9_-]{11}|PL[A-Za-z0-9_-]+)(?:[&?][^\s]*)?"
)
YOUTUBE_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{11}$")

def extract_video_id(link: str) -> str:
    if not link: return ""
    s = str(link).strip()
    if YOUTUBE_ID_RE.match(s): return s

    m = YOUTUBE_REGEX.search(s)
    if m:
        extracted = m.group(1)
        if extracted and not extracted.startswith("PL") and YOUTUBE_ID_RE.match(extracted):
            return extracted

    candidate = ""
    if "v=" in s: candidate = s.split("v=")[-1].split("&")[0]
    else: candidate = s.split("/")[-1].split("?")[0]
    if YOUTUBE_ID_RE.match(candidate): return candidate
    return ""


# ── Database cache (Telegram channel) ──────────────────────────────

async def _download_from_media_db(track_id: str, is_video: bool) -> Optional[str]:
    global TG_FLOOD_COOLDOWN
    TG_APP = _get_app()
    db_uri = getattr(config, "MONGO_DB_URI", getattr(config, "DB_URI", None))
    ch_id_str = getattr(config, "MEDIA_CHANNEL_ID", None)
    if not track_id or TG_APP is None or not db_uri or not ch_id_str: return None
    if time.time() < TG_FLOOD_COOLDOWN:
        _inc("tg_flood_skip")
        return None
    try: ch_id = int(ch_id_str)
    except Exception: return None
    ext = "mp4" if is_video else "mp3"
    keys_to_try = [
        f"{track_id}.{ext}", track_id, f"{track_id}_{'v' if is_video else 'a'}",
        f"{track_id}_{'v' if is_video else 'a'}.{ext}",
    ]
    msg_id = None
    try:
        for k in keys_to_try:
            if await is_media(k, isVideo=is_video):
                msg_id = await get_media_id(k, isVideo=is_video)
                break
    except Exception:
        _inc("media_db_fail")
        return None
    if not msg_id:
        _inc("media_db_miss")
        return None

    _inc("media_db_hit")
    _ensure_dir(DOWNLOAD_DIR)
    final_path = os.path.join(DOWNLOAD_DIR, f"{track_id}.{ext}")
    if os.path.exists(final_path) and os.path.getsize(final_path) > 0: return final_path

    try:
        from pyrogram.errors import FloodWait
        try: msg = await TG_APP.get_messages(ch_id, msg_id)
        except FloodWait as e:
            TG_FLOOD_COOLDOWN = time.time() + e.value + 5
            _inc("tg_fail")
            return None
        if not msg:
            _inc("media_db_fail")
            return None
        try: dl_res = await asyncio.wait_for(TG_APP.download_media(msg), timeout=HARD_TIMEOUT)
        except asyncio.TimeoutError:
            _inc("timeout_fail")
            return None
        except FloodWait as e:
            TG_FLOOD_COOLDOWN = time.time() + e.value + 5
            _inc("tg_fail")
            return None
        if dl_res and os.path.exists(dl_res):
            LOGGER.info(f"DB-CACHE hit for {track_id}")
            return dl_res
        _inc("media_db_fail")
        return None
    except Exception:
        _inc("media_db_fail")
        return None


# ── API download (ArcAPI) ──────────────────────────────────────────

async def _api_create_job(video_id: str, is_video: bool) -> Optional[str]:
    api_url = getattr(config, "API_URL", None)
    api_key = getattr(config, "API_KEY", None)
    if not api_url or not api_key: return None

    endpoint = f"{api_url.rstrip('/')}/youtube/v2/download"
    params = {"api_key": api_key, "query": video_id, "isVideo": str(is_video).lower()}

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

async def _api_poll_job(job_id: str) -> Optional[str]:
    api_url = getattr(config, "API_URL", None)
    if not api_url: return None

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
                LOGGER.info(f"ArcApi: Received #{attempt} [{api_url + public_url}]")
                return api_url.rstrip("/") + public_url
        except Exception:
            pass
        await asyncio.sleep(JOB_POLL_INTERVAL)
    return None

async def _api_save_file(url: str) -> Optional[str]:
    fpath = os.path.join(DOWNLOAD_DIR, url.split("/")[-1])
    _ensure_dir(DOWNLOAD_DIR)
    try:
        session = await get_http_session()
        async with session.get(url, timeout=None) as resp:
            if resp.status != 200: return None
            async with aiofiles.open(fpath, "wb") as f:
                async for chunk in resp.content.iter_chunked(CHUNK_SIZE):
                    if chunk: await f.write(chunk)
        return fpath
    except Exception as e:
        LOGGER.error(f"Failed to save file from API: {e}")
    return None

async def _api_download(video_id: str, is_video: bool) -> Optional[str]:
    for attempt in range(2):
        job_id = await _api_create_job(video_id, is_video)
        if not job_id:
            if attempt == 0: await asyncio.sleep(2)
            continue

        dl_url = await _api_poll_job(job_id)
        if not dl_url:
            if attempt == 0: await asyncio.sleep(2)
            continue

        fpath = await _api_save_file(dl_url)
        if not fpath:
            if attempt == 0: await asyncio.sleep(2)
            continue

        return fpath
    return None


# ── Combined download (DB first, then API) ─────────────────────────

async def optimized_download(link: str, is_video: bool) -> Optional[str]:
    if not link: return None
    vid = extract_video_id(str(link))

    # Check in-memory cache
    if vid:
        if is_video and vid in _v_cache: return _v_cache[vid]
        if not is_video and vid in _dl_cache: return _dl_cache[vid]

    # 1. Try DB cache (Telegram channel)
    if vid:
        db_path = await _download_from_media_db(vid, is_video=is_video)
        if db_path and os.path.exists(db_path):
            _inc("success")
            if is_video: _inc("success_video")
            else: _inc("success_audio")
            if is_video: _v_cache[vid] = db_path
            else: _dl_cache[vid] = db_path
            LOGGER.info(f"DB-CACHE | {vid}")
            return db_path

    # 2. Try API download
    api_url = getattr(config, "API_URL", None)
    api_key = getattr(config, "API_KEY", None)
    if api_url and api_key:
        query = vid or str(link)
        api_path = await _api_download(query, is_video)
        if api_path and os.path.exists(api_path):
            _inc("success")
            if is_video: _inc("success_video")
            else: _inc("success_audio")
            if vid:
                if is_video: _v_cache[vid] = api_path
                else: _dl_cache[vid] = api_path
            LOGGER.info(f"API | {vid or link}")
            return api_path

    return None


class YouTubeAPI:
    def __init__(self):
        self.base = "https://www.youtube.com/watch?v="
        self.listbase = "https://youtube.com/playlist?list="
        self.regex = YOUTUBE_REGEX

    async def fast_search(self, query: str, fetch_all: bool = False) -> Union[Dict[str, str], list, None]:
        if not query: return None

        cached_data = await get_search_cache(query)
        if cached_data and not fetch_all:
            return cached_data

        url = f"https://www.youtube.com/results?search_query={quote(str(query))}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9"
        }
        try:
            session = await get_http_session()
            async with session.get(url, headers=headers, timeout=7) as r:
                if r.status != 200: return None
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
                    contents = data.get("contents", {}).get("twoColumnSearchResultsRenderer", {}).get("primaryContents", {}).get("sectionListRenderer", {}).get("contents", [])
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
                                            if thumbs: thumb = thumbs[-1]["url"].split("?")[0]

                                        results_list.append({
                                            "video_id": vid,
                                            "title": title,
                                            "duration": duration,
                                            "thumbnail": thumb,
                                            "url": f"https://www.youtube.com/watch?v={vid}"
                                        })
            except Exception as e:
                LOGGER.error(f"JSON Parsing failed: {e}")

            # FALLBACK REGEX
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
                            "url": f"https://www.youtube.com/watch?v={vid}"
                        })

            if not results_list: return None

            if fetch_all:
                return results_list

            best_result = results_list[0]
            await set_search_cache(query, best_result)
            return best_result

        except Exception as e:
            LOGGER.error(f"Fast Search Failed: {e}")
            return None

    async def exists(self, link: str, videoid: Union[bool, str] = None) -> bool:
        if not link: return False
        link = str(link)
        if videoid: link = self.base + link
        if not is_safe_url(link): return False
        return bool(self.regex.match(link) or self.regex.search(link))

    async def url(self, message_1: Message) -> Union[str, None]:
        messages = [message_1]
        if message_1.reply_to_message: messages.append(message_1.reply_to_message)
        text = ""; offset = None; length = None
        for message in messages:
            if offset: break
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
        if offset in (None,): return None
        return text[offset: offset + length]

    async def details(self, link: str, videoid: Union[bool, str] = None):
        _inc("search_total")
        if not link:
            _inc("search_failed")
            raise Exception("Track search query is empty.")

        link = str(link)
        if videoid: link = self.base + link

        if not is_safe_url(link):
            _inc("search_failed")
            raise Exception("Blocked by Security Check.")

        cached = await get_search_cache(link)
        if cached:
            duration_sec = 0 if str(cached["duration"]) == "None" else int(time_to_seconds(cached["duration"]))
            _inc("search_success")
            return cached["title"], cached["duration"], duration_sec, cached["thumbnail"], cached["video_id"]

        vid = extract_video_id(link)
        last_error = None

        for attempt in range(SEARCH_RETRIES):
            try:
                if vid:
                    exact_url = f"https://www.youtube.com/watch?v={vid}"
                    results = VideosSearch(exact_url, limit=1)
                    res = await results.next()
                    if not res or not res.get("result"): raise Exception("PySearch returned no data.")
                    result = res["result"][0]
                    yturl = result.get("link") or f"https://www.youtube.com/watch?v={result.get('id', '')}"
                    title = result.get("title", "Unknown Title")
                    duration_min = result.get("duration", "0:00")
                    vidid = result.get("id", "")
                    thumbs = result.get("thumbnails", [])
                    thumbnail = thumbs[0]["url"].split("?")[0] if thumbs else getattr(config, "YOUTUBE_IMG_URL", "")
                else:
                    scrape_res = await self.fast_search(link)
                    if not scrape_res: raise Exception("Scraper failed")

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
                        if not res or not res.get("result"): raise Exception("PySearch returned no data.")
                        result = res["result"][0]
                        yturl = result.get("link") or f"https://www.youtube.com/watch?v={result.get('id', '')}"
                        title = result.get("title", "Unknown Title")
                        duration_min = result.get("duration", "0:00")
                        vidid = result.get("id", "")
                        thumbs = result.get("thumbnails", [])
                        thumbnail = thumbs[0]["url"].split("?")[0] if thumbs else getattr(config, "YOUTUBE_IMG_URL", "")

                if not is_safe_url(yturl): raise Exception("Unsafe URL Returned.")

                duration_sec = 0 if str(duration_min) == "None" else int(time_to_seconds(duration_min))

                await set_search_cache(link, {
                    "title": title, "duration": duration_min, "video_id": vidid,
                    "thumbnail": thumbnail, "url": yturl
                })

                _inc("search_success")
                return title, duration_min, duration_sec, thumbnail, vidid

            except Exception as e:
                last_error = e
                if attempt < SEARCH_RETRIES - 1:
                    await asyncio.sleep(0.5)

        LOGGER.error(f"Details extraction failed for {link} after {SEARCH_RETRIES} attempts: {last_error}")
        _inc("search_failed")
        raise Exception(f"Failed to fetch track details after {SEARCH_RETRIES} attempts: {last_error}")

    async def title(self, link: str, videoid: Union[bool, str] = None) -> str:
        if not link: return "Unknown Title"
        link = str(link)
        if videoid: link = self.base + link
        if not is_safe_url(link): return "Unknown Title"

        cached = await get_search_cache(link)
        if cached: return cached["title"]

        vid = extract_video_id(link)
        for attempt in range(SEARCH_RETRIES):
            try:
                if vid:
                    exact_url = f"https://www.youtube.com/watch?v={vid}"
                    results = VideosSearch(exact_url, limit=1)
                    res = await results.next()
                    if res and res.get("result"): return res["result"][0].get("title", "Unknown Title")
                else:
                    scrape_res = await self.fast_search(link)
                    if scrape_res and scrape_res.get("title") != "Unknown Title": return scrape_res["title"]
                raise Exception("No result")
            except Exception:
                if attempt < SEARCH_RETRIES - 1: await asyncio.sleep(0.5)
        return "Unknown Title"

    async def duration(self, link: str, videoid: Union[bool, str] = None) -> str:
        if not link: return "0:00"
        link = str(link)
        if videoid: link = self.base + link
        if not is_safe_url(link): return "0:00"

        cached = await get_search_cache(link)
        if cached: return cached["duration"]

        vid = extract_video_id(link)
        for attempt in range(SEARCH_RETRIES):
            try:
                if vid:
                    exact_url = f"https://www.youtube.com/watch?v={vid}"
                    results = VideosSearch(exact_url, limit=1)
                    res = await results.next()
                    if res and res.get("result"): return res["result"][0].get("duration", "0:00")
                else:
                    scrape_res = await self.fast_search(link)
                    if scrape_res and scrape_res.get("duration"): return scrape_res["duration"]
                raise Exception("No result")
            except Exception:
                if attempt < SEARCH_RETRIES - 1: await asyncio.sleep(0.5)
        return "0:00"

    async def thumbnail(self, link: str, videoid: Union[bool, str] = None) -> str:
        if not link: return getattr(config, "YOUTUBE_IMG_URL", "")
        link = str(link)
        if videoid: link = self.base + link
        if not is_safe_url(link): return getattr(config, "YOUTUBE_IMG_URL", "")

        cached = await get_search_cache(link)
        if cached: return cached["thumbnail"]

        vid = extract_video_id(link)
        for attempt in range(SEARCH_RETRIES):
            try:
                if vid:
                    exact_url = f"https://www.youtube.com/watch?v={vid}"
                    results = VideosSearch(exact_url, limit=1)
                    res = await results.next()
                    if res and res.get("result"):
                        thumbs = res["result"][0].get("thumbnails", [])
                        return thumbs[0]["url"].split("?")[0] if thumbs else getattr(config, "YOUTUBE_IMG_URL", "")
                else:
                    scrape_res = await self.fast_search(link)
                    if scrape_res and scrape_res.get("thumbnail"): return scrape_res["thumbnail"]
                raise Exception("No result")
            except Exception:
                if attempt < SEARCH_RETRIES - 1: await asyncio.sleep(0.5)
        return getattr(config, "YOUTUBE_IMG_URL", "")

    async def video(self, link: str, videoid: Union[bool, str] = None):
        if not link: return 0, "No link provided"
        link = str(link)
        if videoid: link = self.base + link
        if not is_safe_url(link): return 0, "Unsafe URL"
        if "&" in link: link = link.split("&")[0]

        vid = extract_video_id(link)
        if not vid: return 0, "Could not extract video ID"

        try:
            fpath = await asyncio.wait_for(
                optimized_download(link, is_video=True),
                timeout=PROCESS_TIMEOUT,
            )
            if fpath and os.path.exists(fpath):
                return 1, fpath
        except asyncio.TimeoutError:
            LOGGER.error(f"Video download timed out: {vid}")
        except Exception as e:
            LOGGER.error(f"Video download failed: {e}")
        return 0, "Download failed"

    async def playlist(self, link, limit, user_id, videoid: Union[bool, str] = None):
        if not link: return []
        link = str(link)
        if videoid: link = self.listbase + link
        if not is_safe_url(link): return []
        if "&" in link: link = link.split("&")[0]

        try:
            results = VideosSearch(link, limit=limit)
            res = await results.next()
            if not res or not res.get("result"): return []
            return [r.get("id", "") for r in res["result"] if r.get("id")]
        except Exception as e:
            LOGGER.error(f"Playlist fetch failed: {e}")
            return []

    async def track(self, link: str, videoid: Union[bool, str] = None):
        _inc("search_total")

        if not link:
            _inc("search_failed")
            raise Exception("No search query or link provided.")

        link = str(link)
        if videoid: link = self.base + link

        if not is_safe_url(link):
            _inc("search_failed")
            raise Exception("Blocked by Security Check.")

        cached = await get_search_cache(link)
        if cached:
            _inc("search_success")
            return {
                "title": cached["title"], "link": cached["url"],
                "vidid": cached["video_id"], "duration_min": cached["duration"], "thumb": cached["thumbnail"]
            }, cached["video_id"]

        vid = extract_video_id(link)
        last_error = None

        for attempt in range(SEARCH_RETRIES):
            try:
                if vid:
                    exact_url = f"https://www.youtube.com/watch?v={vid}"
                    results = VideosSearch(exact_url, limit=1)
                    res = await results.next()
                    if not res or not res.get("result"): raise Exception("Track not found or PySearch failed.")

                    result = res["result"][0]
                    yturl = result.get("link") or f"https://www.youtube.com/watch?v={result.get('id', '')}"
                    title = result.get("title", "Unknown Title")
                    duration_min = result.get("duration", "0:00")
                    vidid = result.get("id", "")
                    thumbs = result.get("thumbnails", [])
                    thumbnail = thumbs[0]["url"].split("?")[0] if thumbs else getattr(config, "YOUTUBE_IMG_URL", "")
                else:
                    scrape_res = await self.fast_search(link)
                    if not scrape_res: raise Exception("Scraper failed")

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
                        if not res or not res.get("result"): raise Exception("Track not found.")
                        result = res["result"][0]
                        yturl = result.get("link") or f"https://www.youtube.com/watch?v={result.get('id', '')}"
                        title = result.get("title", "Unknown Title")
                        duration_min = result.get("duration", "0:00")
                        vidid = result.get("id", "")
                        thumbs = result.get("thumbnails", [])
                        thumbnail = thumbs[0]["url"].split("?")[0] if thumbs else getattr(config, "YOUTUBE_IMG_URL", "")

                if not is_safe_url(yturl): raise Exception("Unsafe URL Returned from YouTube.")

                await set_search_cache(link, {
                    "title": title, "duration": duration_min, "video_id": vidid,
                    "thumbnail": thumbnail, "url": yturl
                })

                track_details = {"title": title, "link": yturl, "vidid": vidid, "duration_min": duration_min, "thumb": thumbnail}
                _inc("search_success")
                return track_details, vidid

            except Exception as e:
                last_error = e
                if attempt < SEARCH_RETRIES - 1:
                    await asyncio.sleep(0.5)

        LOGGER.error(f"Track extraction failed for {link} after {SEARCH_RETRIES} attempts: {last_error}")
        _inc("search_failed")
        raise Exception(f"Could not process track request after {SEARCH_RETRIES} attempts: {last_error}")

    async def slider(self, link: str, query_type: int, videoid: Union[bool, str] = None):
        _inc("search_total")
        if not link:
            _inc("search_failed")
            raise Exception("Query is empty.")

        link = str(link)
        if videoid: link = self.base + link

        if not is_safe_url(link):
            _inc("search_failed")
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
                            _inc("search_success")
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
                            _inc("search_success")
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

                _inc("search_success")
                return title, duration_min, thumbnail, vidid
            except Exception as e:
                last_error = e
                if attempt < SEARCH_RETRIES - 1:
                    await asyncio.sleep(0.5)

        LOGGER.error(f"Slider extraction failed for {link} after {SEARCH_RETRIES} attempts: {last_error}")
        _inc("search_failed")
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

        _inc("total")
        is_video = bool(video or songvideo)

        if not link:
            _inc("failed")
            if is_video: _inc("failed_video")
            else: _inc("failed_audio")
            if songvideo or songaudio: return None
            return None, False

        link = str(link)
        if videoid: link = self.base + link

        if not is_safe_url(link):
            _inc("failed")
            if is_video: _inc("failed_video")
            else: _inc("failed_audio")
            if songvideo or songaudio: return None
            return None, False

        if "&" in link:
            link = link.split("&")[0]

        # ============================================
        # API + DB DOWNLOAD (no yt-dlp)
        # ============================================
        try:
            optimized_path = await asyncio.wait_for(optimized_download(link, is_video), timeout=PROCESS_TIMEOUT)
            if optimized_path and os.path.exists(optimized_path):
                if songvideo or songaudio:
                    return optimized_path
                return optimized_path, True
        except asyncio.TimeoutError:
            LOGGER.error(f"Download Timed Out ({PROCESS_TIMEOUT}s limit): {link}")
        except Exception as e:
            LOGGER.error(f"Download Failed: {e}")

        _inc("failed")
        if is_video: _inc("failed_video")
        else: _inc("failed_audio")
        if songvideo or songaudio: return None
        return None, False
