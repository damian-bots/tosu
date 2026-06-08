# AnonXMusic — Youtube.py  (v1.0.4)
# Clean rewrite — API-1 download modelled after Spotify3-dev helpers/_api.py
# Full error logging to terminal + ERROR_LOG_ID on every failure/exception.

import asyncio
import glob
import json
import os
import random
import re
import traceback
from pathlib import Path
from typing import Any, Dict, Optional, Union
from urllib.parse import quote, unquote, urlparse

import aiofiles
import aiohttp
from aiohttp import TCPConnector
from motor.motor_asyncio import AsyncIOMotorClient
from pyrogram.enums import MessageEntityType
from pyrogram.types import Message
from youtubesearchpython.__future__ import VideosSearch

import config
from AnonXMusic import app as TG_APP
from AnonXMusic.logging import LOGGER as _LOGGER
from AnonXMusic.utils.database import (
    get_search_cache,
    increment_api_usage,
    is_on_off,
    set_search_cache,
)
from AnonXMusic.utils.formatters import time_to_seconds

LOGGER = _LOGGER(__name__)

# ── Constants ────────────────────────────────────────────────────────────────
DOWNLOAD_DIR        = os.path.join(os.getcwd(), "downloads")
CHUNK_SIZE          = 1024 * 1024
SEARCH_RETRIES      = 4
HARD_TIMEOUT        = 80
PROCESS_TIMEOUT     = 80
V2_POLL_RETRIES     = 12
V2_CREATE_RETRIES   = 3
V2_DOWNLOAD_CYCLES  = 2
CDN_RETRIES         = 5
CDN_RETRY_DELAY     = 2

MEDIA_DB_NAME        = "arcapi"
MEDIA_COLLECTION_NAME = "medias"

_session: Optional[aiohttp.ClientSession] = None
_session_lock = asyncio.Lock()
_MONGO_CLIENT: Optional[AsyncIOMotorClient] = None
_TG_FLOOD_COOLDOWN: float = 0.0

# ── Error helpers ─────────────────────────────────────────────────────────────

async def _log_error(context: str, exc: Exception) -> None:
    """Print full traceback to terminal AND forward to ERROR_LOG_ID."""
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    LOGGER.error(f"[YouTube] {context}:\n{tb}")

    try:
        import html
        from pyrogram.enums import ParseMode
        text = (
            f"🚨 <b>YouTube Error</b>\n\n"
            f"📌 <b>Context:</b> <code>{html.escape(context)}</code>\n"
            f"❌ <b>Exception:</b> <code>{html.escape(type(exc).__name__)}</code>\n"
            f"💬 <b>Message:</b> <code>{html.escape(str(exc))}</code>\n\n"
            f"📄 <b>Traceback:</b>\n<pre>{html.escape(tb[:3000])}</pre>"
        )
        await TG_APP.send_message(
            chat_id=config.ERROR_LOG_ID,
            text=text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except Exception as send_err:
        LOGGER.warning(f"[YouTube] Could not send error to ERROR_LOG_ID: {send_err}")


# ── Cookies ───────────────────────────────────────────────────────────────────

def cookie_txt_file() -> str:
    folder = os.path.join(os.getcwd(), "cookies")
    txt_files = glob.glob(os.path.join(folder, "*.txt"))
    if not txt_files:
        return "cookies/cookies.txt"
    return f"cookies/{os.path.basename(random.choice(txt_files))}"


# ── HTTP session (singleton) ──────────────────────────────────────────────────

async def get_http_session() -> aiohttp.ClientSession:
    global _session
    if _session and not _session.closed:
        return _session
    async with _session_lock:
        if _session and not _session.closed:
            return _session
        connector = TCPConnector(limit=100, ttl_dns_cache=300, enable_cleanup_closed=True)
        timeout = aiohttp.ClientTimeout(total=HARD_TIMEOUT, sock_connect=10, sock_read=30)
        _session = aiohttp.ClientSession(timeout=timeout, connector=connector)
        return _session


# ── Mongo media-cache helpers ─────────────────────────────────────────────────

def _get_media_collection():
    global _MONGO_CLIENT
    db_uri = config.DB_URI
    if not db_uri:
        return None
    if _MONGO_CLIENT is None:
        _MONGO_CLIENT = AsyncIOMotorClient(db_uri)
    return _MONGO_CLIENT[MEDIA_DB_NAME][MEDIA_COLLECTION_NAME]


async def _is_media(track_id: str, is_video: bool = False) -> bool:
    col = _get_media_collection()
    if col is None:
        return False
    try:
        return bool(await col.find_one({"track_id": track_id, "isVideo": is_video}, {"_id": 1}))
    except Exception as e:
        await _log_error(f"MongoDB is_media({track_id})", e)
        return False


async def _get_media_msg_id(track_id: str, is_video: bool = False) -> Optional[int]:
    col = _get_media_collection()
    if col is None:
        return None
    try:
        doc = await col.find_one({"track_id": track_id, "isVideo": is_video}, {"message_id": 1})
        if doc and doc.get("message_id"):
            return int(doc["message_id"])
    except Exception as e:
        await _log_error(f"MongoDB get_media_msg_id({track_id})", e)
    return None


# ── URL / ID helpers ──────────────────────────────────────────────────────────

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
    candidate = s.split("v=")[-1].split("&")[0] if "v=" in s else s.split("/")[-1].split("?")[0]
    return candidate if YOUTUBE_ID_RE.match(candidate) else ""


def is_safe_url(text: str) -> bool:
    DANGEROUS = [";", "|", "$", "`", "\n", "\r", "(", ")", "<", ">", "{", "}", "\\", "'", '"']
    ALLOWED   = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "music.youtube.com"}
    if not text:
        return False
    text = str(text).strip()
    if not text.lower().startswith(("http:", "https:", "www.")):
        CRITICAL = [";", "|", "$", "`", "{", "}", "\n", "\r"]
        try:
            if any(c in unquote(text).lower() for c in CRITICAL):
                return False
        except Exception:
            return False
        return True
    try:
        target = text if not text.lower().startswith("www.") else "https://" + text
        if any(c in unquote(target) for c in DANGEROUS):
            return False
        domain = urlparse(target).netloc.replace("www.", "")
        return domain in ALLOWED
    except Exception:
        return False


def _ensure_dir(p: str) -> None:
    os.makedirs(p, exist_ok=True)


# ── Telegram media-DB download (channel cache) ───────────────────────────────

async def _download_from_media_db(track_id: str, is_video: bool) -> Optional[str]:
    global _TG_FLOOD_COOLDOWN
    if not track_id or TG_APP is None or not config.DB_URI:
        return None
    try:
        ch_id = int(config.MEDIA_CHANNEL_ID)
    except Exception:
        return None

    import time
    if time.time() < _TG_FLOOD_COOLDOWN:
        return None

    ext = "mp4" if is_video else "mp3"
    keys = [f"{track_id}.{ext}", track_id, f"{track_id}_{'v' if is_video else 'a'}.{ext}"]

    msg_id = None
    try:
        for k in keys:
            if await _is_media(k, is_video):
                msg_id = await _get_media_msg_id(k, is_video)
                break
    except Exception as e:
        await _log_error(f"Media-DB lookup failed for {track_id}", e)
        return None

    if not msg_id:
        return None

    _ensure_dir(DOWNLOAD_DIR)
    final_path = os.path.join(DOWNLOAD_DIR, f"{track_id}.{ext}")
    if os.path.exists(final_path) and os.path.getsize(final_path) > 0:
        LOGGER.info(f"[MediaDB] Local cache hit › {track_id}")
        return final_path

    tmp_path = final_path + ".temp"
    try:
        from pyrogram.errors import FloodWait
        msg = await TG_APP.get_messages(ch_id, msg_id)
        if not msg:
            return None
        dl = await asyncio.wait_for(
            TG_APP.download_media(msg, file_name=tmp_path), timeout=HARD_TIMEOUT
        )
        if not dl or not os.path.exists(str(dl)) or os.path.getsize(str(dl)) <= 0:
            return None
        if str(dl) != final_path:
            os.replace(str(dl), final_path)
        LOGGER.info(f"[MediaDB] Channel cache hit › {track_id}")
        return final_path
    except asyncio.TimeoutError as e:
        await _log_error(f"Media-DB download timeout for {track_id}", e)
        return None
    except Exception as e:
        if "FloodWait" in type(e).__name__:
            import time
            _TG_FLOOD_COOLDOWN = time.time() + getattr(e, "value", 30) + 5
        await _log_error(f"Media-DB download failed for {track_id}", e)
        return None


# ── API-1 (Arc API) — modelled after Spotify3 helpers/_api.py ─────────────────
# Three clean steps:  create_job → get_url (poll) → save_file
# Each step has proper retries and logs every failure.

async def _api1_create_job(
    session: aiohttp.ClientSession,
    api_url: str,
    api_key: str,
    video_id: str,
    is_video: bool,
) -> Optional[str]:
    endpoint = f"{api_url.rstrip('/')}/youtube/v2/download"
    params = {"api_key": api_key, "query": video_id, "isVideo": str(is_video).lower()}

    for attempt in range(V2_CREATE_RETRIES):
        try:
            async with session.get(
                endpoint, params=params,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status != 200:
                    LOGGER.warning(f"[API-1] create_job HTTP {resp.status} (attempt {attempt+1})")
                    await asyncio.sleep(1)
                    continue
                data = await resp.json(content_type=None)
                if data.get("status") != "queued":
                    LOGGER.warning(f"[API-1] create_job status={data.get('status')} (attempt {attempt+1})")
                    await asyncio.sleep(1)
                    continue
                job_id = data.get("job_id")
                if not job_id:
                    LOGGER.warning(f"[API-1] create_job no job_id (attempt {attempt+1})")
                    await asyncio.sleep(1)
                    continue
                return job_id
        except Exception as e:
            await _log_error(f"API-1 create_job attempt {attempt+1} for {video_id}", e)
            await asyncio.sleep(1)
    return None


async def _api1_get_url(
    session: aiohttp.ClientSession,
    api_url: str,
    job_id: str,
) -> Optional[str]:
    endpoint = f"{api_url.rstrip('/')}/youtube/jobStatus"
    params = {"job_id": job_id}

    for attempt in range(1, V2_POLL_RETRIES + 1):
        try:
            async with session.get(
                endpoint, params=params,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                if resp.status != 200:
                    await asyncio.sleep(3)
                    continue
                data = await resp.json(content_type=None)
                if data.get("status") != "success":
                    await asyncio.sleep(3)
                    continue
                job = data.get("job", {})
                if job.get("status", "") != "done":
                    await asyncio.sleep(3)
                    continue
                public_url = job.get("result", {}).get("public_url")
                if not public_url:
                    LOGGER.warning(f"[API-1] get_url: no public_url in response")
                    break
                full_url = (
                    f"{api_url.rstrip('/')}{public_url}"
                    if public_url.startswith("/")
                    else public_url
                )
                LOGGER.info(f"[API-1] Download URL ready (attempt {attempt})")
                return full_url
        except Exception as e:
            await _log_error(f"API-1 get_url attempt {attempt} for job {job_id}", e)
        await asyncio.sleep(3)

    LOGGER.error(f"[API-1] get_url exhausted {V2_POLL_RETRIES} retries for job {job_id}")
    return None


async def _api1_save_file(
    session: aiohttp.ClientSession,
    url: str,
    out_path: str,
) -> Optional[str]:
    _ensure_dir(str(Path(out_path).parent))
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=None)) as resp:
            if resp.status != 200:
                LOGGER.error(f"[API-1] save_file HTTP {resp.status} for {url}")
                return None
            async with aiofiles.open(out_path, "wb") as f:
                async for chunk in resp.content.iter_chunked(CHUNK_SIZE):
                    if chunk:
                        await f.write(chunk)
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            return out_path
        LOGGER.error(f"[API-1] save_file resulted in empty file at {out_path}")
        return None
    except Exception as e:
        await _log_error(f"API-1 save_file from {url}", e)
        return None


async def _api1_download(link: str, is_video: bool) -> Optional[str]:
    """Full API-1 download: job → poll → save. Mirrors Spotify3 API.download()."""
    api_url = getattr(config, "API_URL", "")
    api_key = getattr(config, "API_KEY", "")
    if not api_url or not api_key:
        LOGGER.warning("[API-1] API_URL or API_KEY not configured — skipping")
        return None

    vid = extract_video_id(str(link))
    query = vid or str(link)
    ext = "mp4" if is_video else "m4a"
    out_path = os.path.join(DOWNLOAD_DIR, f"{vid or 'audio'}.{ext}")

    if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
        LOGGER.info(f"[API-1] Local file cache hit › {out_path}")
        return out_path

    session = await get_http_session()

    for cycle in range(V2_DOWNLOAD_CYCLES):
        job_id = await _api1_create_job(session, api_url, api_key, query, is_video)
        if not job_id:
            LOGGER.error(f"[API-1] create_job failed (cycle {cycle+1}) for {query}")
            if cycle == 0:
                await asyncio.sleep(2)
            continue

        dl_url = await _api1_get_url(session, api_url, job_id)
        if not dl_url:
            LOGGER.error(f"[API-1] get_url failed (cycle {cycle+1}) for job {job_id}")
            if cycle == 0:
                await asyncio.sleep(2)
            continue

        fpath = await _api1_save_file(session, dl_url, out_path)
        if not fpath:
            LOGGER.error(f"[API-1] save_file failed (cycle {cycle+1}) for {dl_url}")
            if cycle == 0:
                await asyncio.sleep(2)
            continue

        await increment_api_usage("api_1")
        LOGGER.info(f"[API-1] Download success › {fpath}")
        return fpath

    LOGGER.error(f"[API-1] All {V2_DOWNLOAD_CYCLES} cycles failed for {link}")
    return None


async def _optimized_download(link: str, is_video: bool) -> Optional[str]:
    """Try media-DB channel cache first, then API-1."""
    vid = extract_video_id(str(link))

    # 1. Channel media-DB cache
    if vid:
        try:
            db_path = await _download_from_media_db(vid, is_video)
            if db_path and os.path.exists(db_path):
                return db_path
        except Exception as e:
            await _log_error(f"Media-DB lookup for {vid}", e)

    # 2. API-1
    try:
        api_path = await _api1_download(str(link), is_video)
        if api_path and os.path.exists(api_path):
            return api_path
    except Exception as e:
        await _log_error(f"API-1 download for {link}", e)

    return None


# ── Search helpers ────────────────────────────────────────────────────────────

async def _fast_search(query: str, fetch_all: bool = False):
    """Scrape YouTube search results without yt-dlp."""
    if not query:
        return None

    cached = await get_search_cache(query)
    if cached and not fetch_all:
        return cached

    url = f"https://www.youtube.com/results?search_query={quote(str(query))}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "Chrome/120.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
    }

    try:
        session = await get_http_session()
        async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=7)) as r:
            if r.status != 200:
                LOGGER.warning(f"[Search] fast_search HTTP {r.status} for {query!r}")
                return None
            html_text = await r.text()
    except Exception as e:
        await _log_error(f"fast_search HTTP fetch for {query!r}", e)
        return None

    results: list = []

    # Try JSON parse
    try:
        match = re.search(r'(?:var ytInitialData|window\["ytInitialData"\])\s*=\s*(\{)', html_text)
        if match:
            start = match.start(1)
            depth = in_str = escape = 0
            json_str = None
            for i in range(start, len(html_text)):
                c = html_text[i]
                if escape:
                    escape = False
                    continue
                if c == "\\":
                    escape = True
                    continue
                if c == '"':
                    in_str = not in_str
                    continue
                if not in_str:
                    if c == "{":
                        depth += 1
                    elif c == "}":
                        depth -= 1
                    if depth == 0:
                        json_str = html_text[start:i + 1]
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
                                vid_id = vr.get("videoId")
                                if vid_id and not any(r["video_id"] == vid_id for r in results):
                                    title = "Unknown Title"
                                    if "title" in vr and "runs" in vr["title"]:
                                        title = "".join(run["text"] for run in vr["title"]["runs"])
                                    dur = "0:00"
                                    if "lengthText" in vr and "simpleText" in vr["lengthText"]:
                                        dur = vr["lengthText"]["simpleText"]
                                    thumb = f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg"
                                    if "thumbnail" in vr and "thumbnails" in vr["thumbnail"]:
                                        tbs = vr["thumbnail"]["thumbnails"]
                                        if tbs:
                                            thumb = tbs[-1]["url"].split("?")[0]
                                    results.append({
                                        "video_id": vid_id,
                                        "title": title,
                                        "duration": dur,
                                        "thumbnail": thumb,
                                        "url": f"https://www.youtube.com/watch?v={vid_id}",
                                    })
    except Exception as e:
        await _log_error(f"fast_search JSON parse for {query!r}", e)

    # Regex fallback
    if not results:
        for vid_id in dict.fromkeys(re.findall(r'"videoId":"([a-zA-Z0-9_-]{11})"', html_text)):
            results.append({
                "video_id": vid_id,
                "title": "Unknown Title",
                "duration": "0:00",
                "thumbnail": f"https://i.ytimg.com/vi/{vid_id}/hqdefault.jpg",
                "url": f"https://www.youtube.com/watch?v={vid_id}",
            })

    if not results:
        LOGGER.warning(f"[Search] fast_search returned no results for {query!r}")
        return None

    if fetch_all:
        return results

    best = results[0]
    await set_search_cache(query, best)
    return best


# ── Main YouTubeAPI class ─────────────────────────────────────────────────────

class YouTubeAPI:
    def __init__(self):
        self.base     = "https://www.youtube.com/watch?v="
        self.listbase = "https://youtube.com/playlist?list="
        self.regex    = YOUTUBE_REGEX

    # ── exists / url ──────────────────────────────────────────────────────────

    async def exists(self, link: str, videoid: Union[bool, str] = None) -> bool:
        if not link:
            return False
        link = str(link)
        if videoid:
            link = self.base + link
        if not is_safe_url(link):
            return False
        return bool(self.regex.match(link) or self.regex.search(link))

    async def url(self, message_1: Message) -> Optional[str]:
        messages = [message_1]
        if message_1.reply_to_message:
            messages.append(message_1.reply_to_message)
        for message in messages:
            if message.entities:
                for entity in message.entities:
                    if entity.type == MessageEntityType.URL:
                        text = message.text or message.caption
                        return text[entity.offset: entity.offset + entity.length]
            if message.caption_entities:
                for entity in message.caption_entities:
                    if entity.type == MessageEntityType.TEXT_LINK:
                        return entity.url
        return None

    # ── details ───────────────────────────────────────────────────────────────

    async def details(self, link: str, videoid: Union[bool, str] = None):
        if not link:
            raise Exception("❌ Track search query is empty.")
        link = str(link)
        if videoid:
            link = self.base + link
        if not is_safe_url(link):
            raise Exception("❌ Blocked by security check.")

        cached = await get_search_cache(link)
        if cached:
            dur_sec = 0 if str(cached["duration"]) == "None" else int(time_to_seconds(cached["duration"]))
            return cached["title"], cached["duration"], dur_sec, cached["thumbnail"], cached["video_id"]

        vid = extract_video_id(link)
        last_err = None

        for attempt in range(SEARCH_RETRIES):
            try:
                title, duration_min, vidid, thumbnail, yturl = await self._resolve_meta(link, vid)
                if not is_safe_url(yturl):
                    raise Exception("Unsafe URL returned.")
                dur_sec = 0 if str(duration_min) == "None" else int(time_to_seconds(duration_min))
                await set_search_cache(link, {
                    "title": title, "duration": duration_min,
                    "video_id": vidid, "thumbnail": thumbnail, "url": yturl,
                })
                return title, duration_min, dur_sec, thumbnail, vidid
            except Exception as e:
                last_err = e
                if attempt < SEARCH_RETRIES - 1:
                    await asyncio.sleep(0.5)

        await _log_error(f"details() failed for {link}", last_err)
        raise Exception(f"❌ Failed to fetch track details: {last_err}")

    async def title(self, link: str, videoid: Union[bool, str] = None) -> str:
        try:
            t, *_ = await self.details(link, videoid)
            return t
        except Exception:
            return "Unknown Title"

    async def duration(self, link: str, videoid: Union[bool, str] = None) -> str:
        try:
            _, d, *_ = await self.details(link, videoid)
            return d
        except Exception:
            return "0:00"

    async def thumbnail(self, link: str, videoid: Union[bool, str] = None) -> str:
        fallback = config.YOUTUBE_IMG_URL
        try:
            *_, thumb, _ = await self.details(link, videoid)
            return thumb or fallback
        except Exception:
            return fallback

    # ── track ─────────────────────────────────────────────────────────────────

    async def track(self, link: str, videoid: Union[bool, str] = None):
        if not link:
            raise Exception("❌ No search query or link provided.")
        link = str(link)
        if videoid:
            link = self.base + link
        if not is_safe_url(link):
            raise Exception("❌ Blocked by security check.")

        cached = await get_search_cache(link)
        if cached:
            return {
                "title": cached["title"], "link": cached["url"],
                "vidid": cached["video_id"], "duration_min": cached["duration"],
                "thumb": cached["thumbnail"],
            }, cached["video_id"]

        vid = extract_video_id(link)
        last_err = None

        for attempt in range(SEARCH_RETRIES):
            try:
                title, duration_min, vidid, thumbnail, yturl = await self._resolve_meta(link, vid)
                if not is_safe_url(yturl):
                    raise Exception("Unsafe URL returned.")
                await set_search_cache(link, {
                    "title": title, "duration": duration_min,
                    "video_id": vidid, "thumbnail": thumbnail, "url": yturl,
                })
                return {
                    "title": title, "link": yturl,
                    "vidid": vidid, "duration_min": duration_min, "thumb": thumbnail,
                }, vidid
            except Exception as e:
                last_err = e
                if attempt < SEARCH_RETRIES - 1:
                    await asyncio.sleep(0.5)

        await _log_error(f"track() failed for {link}", last_err)
        raise Exception(f"❌ Could not process track: {last_err}")

    # ── slider ────────────────────────────────────────────────────────────────

    async def slider(self, link: str, query_type: int, videoid: Union[bool, str] = None):
        if not link:
            raise Exception("❌ Query is empty.")
        link = str(link)
        if videoid:
            link = self.base + link
        if not is_safe_url(link):
            raise Exception("❌ Blocked by security check.")

        vid = extract_video_id(link)
        last_err = None

        for attempt in range(SEARCH_RETRIES):
            try:
                if not vid:
                    scraped = await _fast_search(link, fetch_all=True)
                    if scraped and isinstance(scraped, list) and len(scraped) > query_type:
                        target = scraped[query_type]
                        if target.get("title") and target["title"] != "Unknown Title":
                            return target["title"], target["duration"], target["thumbnail"], target["video_id"]
                        # Enrich via VideosSearch
                        res = await VideosSearch(
                            f"https://www.youtube.com/watch?v={target['video_id']}", limit=1
                        ).next()
                        if res and res.get("result"):
                            r = res["result"][0]
                            thumbs = r.get("thumbnails", [])
                            return (
                                r.get("title", "Unknown"),
                                r.get("duration", "0:00"),
                                thumbs[0]["url"].split("?")[0] if thumbs else config.YOUTUBE_IMG_URL,
                                r.get("id", target["video_id"]),
                            )

                a = VideosSearch(link, limit=10)
                res = await a.next()
                if not res or not res.get("result") or len(res["result"]) <= query_type:
                    raise Exception("Slider data not found.")
                r = res["result"][query_type]
                thumbs = r.get("thumbnails", [])
                return (
                    r.get("title", "Unknown"),
                    r.get("duration", "0:00"),
                    thumbs[0]["url"].split("?")[0] if thumbs else config.YOUTUBE_IMG_URL,
                    r.get("id", ""),
                )
            except Exception as e:
                last_err = e
                if attempt < SEARCH_RETRIES - 1:
                    await asyncio.sleep(0.5)

        await _log_error(f"slider() failed for {link}", last_err)
        raise Exception(f"❌ Failed to load search options: {last_err}")

    # ── video (live stream URL) ────────────────────────────────────────────────

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
        proc = await asyncio.create_subprocess_exec(
            "yt-dlp", "--cookies", cookie_txt_file(),
            "-g", "-f", "best[height<=?720][width<=?1280]", link,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=PROCESS_TIMEOUT)
        except asyncio.TimeoutError as e:
            proc.kill()
            await _log_error(f"video() yt-dlp timeout for {link}", e)
            return 0, "Timeout: video extraction took too long"
        except Exception as e:
            await _log_error(f"video() yt-dlp error for {link}", e)
            return 0, str(e)
        return (1, stdout.decode().split("\n")[0]) if stdout else (0, stderr.decode())

    # ── playlist ──────────────────────────────────────────────────────────────

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
        proc = await asyncio.create_subprocess_exec(
            "yt-dlp", "-i", "--get-id", "--flat-playlist",
            "--cookies", cookie_txt_file(),
            "--playlist-end", str(limit),
            "--skip-download", link,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=PROCESS_TIMEOUT)
        except asyncio.TimeoutError as e:
            proc.kill()
            await _log_error(f"playlist() timeout for {link}", e)
            return []
        except Exception as e:
            await _log_error(f"playlist() error for {link}", e)
            return []
        try:
            out = stdout.decode("utf-8") if stdout else ""
            return [x for x in out.split("\n") if x]
        except Exception as e:
            await _log_error(f"playlist() decode error for {link}", e)
            return []

    # ── formats ───────────────────────────────────────────────────────────────

    async def formats(self, link: str, videoid: Union[bool, str] = None):
        import yt_dlp
        if not link:
            return [], ""
        link = str(link)
        if videoid:
            link = self.base + link
        if not is_safe_url(link):
            return [], link
        if "&" in link:
            link = link.split("&")[0]
        try:
            ydl_opts = {"quiet": True, "cookiefile": cookie_txt_file()}
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                formats_available = []
                r = ydl.extract_info(link, download=False)
                for fmt in r.get("formats", []):
                    try:
                        if "dash" in str(fmt.get("format", "")).lower():
                            continue
                        formats_available.append({
                            "format": fmt["format"], "filesize": fmt["filesize"],
                            "format_id": fmt["format_id"], "ext": fmt["ext"],
                            "format_note": fmt["format_note"], "yturl": link,
                        })
                    except Exception:
                        continue
            return formats_available, link
        except Exception as e:
            await _log_error(f"formats() for {link}", e)
            return [], link

    # ── download (main entry point) ───────────────────────────────────────────

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
        """
        Download via media-DB cache → API-1.
        All failures are logged to terminal + ERROR_LOG_ID.
        Returns filepath (str) for song modes, (filepath, True/False) for play modes.
        """
        is_video = bool(video or songvideo)
        song_mode = bool(songvideo or songaudio)

        def _fail():
            return None if song_mode else (None, False)

        if not link:
            LOGGER.error("[Download] Called with empty link")
            return _fail()

        link = str(link)
        if videoid:
            link = self.base + link
        if not is_safe_url(link):
            LOGGER.error(f"[Download] Unsafe URL blocked: {link}")
            return _fail()
        if "&" in link:
            link = link.split("&")[0]

        try:
            path = await asyncio.wait_for(
                _optimized_download(link, is_video),
                timeout=PROCESS_TIMEOUT,
            )
            if path and os.path.exists(path):
                LOGGER.info(f"[Download] Success › {path}")
                return path if song_mode else (path, True)
        except asyncio.TimeoutError as e:
            await _log_error(f"Download timeout for {link}", e)
        except Exception as e:
            await _log_error(f"Download failed for {link}", e)

        LOGGER.error(f"[Download] All methods failed for {link}")
        return _fail()

    # ── internal meta resolver ────────────────────────────────────────────────

    async def _resolve_meta(self, link: str, vid: str):
        """Return (title, duration_min, vidid, thumbnail, yturl)."""
        if vid:
            results = VideosSearch(f"https://www.youtube.com/watch?v={vid}", limit=1)
            res = await results.next()
            if not res or not res.get("result"):
                raise Exception("PySearch returned no data.")
            r = res["result"][0]
            thumbs = r.get("thumbnails", [])
            return (
                r.get("title", "Unknown Title"),
                r.get("duration", "0:00"),
                r.get("id", ""),
                thumbs[0]["url"].split("?")[0] if thumbs else config.YOUTUBE_IMG_URL,
                r.get("link") or f"https://www.youtube.com/watch?v={r.get('id', '')}",
            )
        else:
            scrape_res = await _fast_search(link)
            if not scrape_res:
                raise Exception("Scraper returned no results.")
            if scrape_res.get("title") and scrape_res["title"] != "Unknown Title":
                return (
                    scrape_res["title"], scrape_res["duration"],
                    scrape_res["video_id"], scrape_res["thumbnail"], scrape_res["url"],
                )
            # Enrich with VideosSearch
            results = VideosSearch(
                f"https://www.youtube.com/watch?v={scrape_res['video_id']}", limit=1
            )
            res = await results.next()
            if not res or not res.get("result"):
                raise Exception("PySearch returned no data on enrichment.")
            r = res["result"][0]
            thumbs = r.get("thumbnails", [])
            return (
                r.get("title", "Unknown Title"),
                r.get("duration", "0:00"),
                r.get("id", ""),
                thumbs[0]["url"].split("?")[0] if thumbs else config.YOUTUBE_IMG_URL,
                r.get("link") or f"https://www.youtube.com/watch?v={r.get('id', '')}",
            )
