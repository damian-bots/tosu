import time
import asyncio
import os
import re
import json
import glob
import random
import logging
from typing import Any, Dict, Union, Optional
from pathlib import Path
from urllib.parse import urlparse, unquote, quote

import aiofiles
import aiohttp
from aiohttp import TCPConnector
from motor.motor_asyncio import AsyncIOMotorClient
import yt_dlp
from pyrogram.enums import MessageEntityType
from pyrogram.types import Message
from youtubesearchpython.__future__ import VideosSearch

from AnonXMusic.utils.database import is_on_off, get_search_cache, set_search_cache, increment_api_usage
from AnonXMusic.utils.error_logger import error_logger
from AnonXMusic.utils.formatters import time_to_seconds

import config
from AnonXMusic import app as TG_APP
from AnonXMusic import LOGGER as LOG

MEDIA_DB_NAME = "arcapi"
MEDIA_COLLECTION_NAME = "medias"

DOWNLOAD_DIR = "downloads"
LOGGER = LOG(__name__)

CHUNK_SIZE = 1024 * 1024
V2_JOB_POLL_RETRIES = 12
V2_CREATE_JOB_RETRIES = 3
V2_DOWNLOAD_CYCLES = 2
SEARCH_RETRIES = 4
HARD_RETRY_WAIT = 3
CDN_RETRIES = 5
CDN_RETRY_DELAY = 2

HARD_TIMEOUT = 80
PROCESS_TIMEOUT = 80

TG_FLOOD_COOLDOWN = 0.0

def _inc(key: str, n: int = 1) -> None:
    pass  # download stats tracking removed; use /usage for API usage stats

_session: Optional[aiohttp.ClientSession] = None
_session_lock = asyncio.Lock()
_MONGO_CLIENT: Optional[AsyncIOMotorClient] = None

def cookie_txt_file() -> str:
    folder_path = os.path.join(os.getcwd(), "cookies")
    filename = os.path.join(os.getcwd(), "cookies", "logs.csv")
    txt_files = glob.glob(os.path.join(folder_path, "*.txt"))
    if not txt_files:
        return "cookies/cookies.txt"
    cookie_file = random.choice(txt_files)
    try:
        with open(filename, "a") as file:
            file.write(f"Choosen File : {cookie_file}\n")
    except Exception:
        pass
    return f"cookies/{os.path.basename(cookie_file)}"

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

async def is_media(track_id: str, isVideo: bool = False) -> bool:
    col = _get_media_collection()
    if col is None:
        return False
    try:
        return bool(await col.find_one({"track_id": track_id, "isVideo": isVideo}, {"_id": 1}))
    except Exception:
        return False

async def get_media_id(track_id: str, isVideo: bool = False) -> Optional[int]:
    col = _get_media_collection()
    if col is None:
        return None
    try:
        doc = await col.find_one({"track_id": track_id, "isVideo": isVideo}, {"message_id": 1})
        if doc and doc.get("message_id"):
            return int(doc["message_id"])
    except Exception:
        pass
    return None

def _ensure_dir(p: str) -> None:
    os.makedirs(p, exist_ok=True)

def _resolve_if_dir(download_result: str) -> Optional[str]:
    if not download_result:
        return None
    p = Path(download_result)
    if p.exists() and p.is_file():
        return str(p)
    if p.exists() and p.is_dir():
        files = [x for x in p.iterdir() if x.is_file()]
        if not files:
            return None
        return str(max(files, key=lambda x: x.stat().st_mtime))
    return download_result

def is_safe_url(text: str) -> bool:
    DANGEROUS_CHARS = [";", "|", "$", "`", "\n", "\r", "(", ")", "<", ">", "{", "}", "\\", "'", '"']
    ALLOWED_DOMAINS = {
        "youtube.com", "www.youtube.com", "m.youtube.com",
        "youtu.be", "music.youtube.com",
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
            return False

        parsed = urlparse(target_url)
        domain = parsed.netloc.replace("www.", "")
        return domain in ALLOWED_DOMAINS

    except Exception:
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
    if "v=" in s:
        candidate = s.split("v=")[-1].split("&")[0]
    else:
        candidate = s.split("/")[-1].split("?")[0]
    return candidate if YOUTUBE_ID_RE.match(candidate) else ""

async def check_file_size(link):
    if not link or not is_safe_url(str(link)):
        return None

    async def get_format_info(url):
        proc = await asyncio.create_subprocess_exec(
            "yt-dlp", "-J", url,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        except asyncio.TimeoutError:
            proc.kill()
            return None
        return json.loads(stdout.decode()) if proc.returncode == 0 else None

    info = await get_format_info(str(link))
    if not info:
        return None
    formats = info.get("formats", [])
    return sum(f["filesize"] for f in formats if "filesize" in f) if formats else None

async def _download_from_cdn(cdn_url: str, out_path: str) -> Optional[str]:
    if not cdn_url:
        return None
    for attempt in range(1, CDN_RETRIES + 1):
        try:
            session = await get_http_session()
            async with session.get(cdn_url, timeout=HARD_TIMEOUT) as resp:
                if resp.status != 200:
                    if resp.status in (429, 500, 502, 503, 504) and attempt < CDN_RETRIES:
                        await asyncio.sleep(CDN_RETRY_DELAY)
                        continue
                    return None
                _ensure_dir(str(Path(out_path).parent))
                async with aiofiles.open(out_path, "wb") as f:
                    async for chunk in resp.content.iter_chunked(CHUNK_SIZE):
                        if chunk:
                            await f.write(chunk)
            return out_path if os.path.exists(out_path) else None
        except asyncio.TimeoutError:
            _inc("timeout_fail")
            if attempt < CDN_RETRIES:
                await asyncio.sleep(CDN_RETRY_DELAY)
        except Exception:
            _inc("network_fail")
            if attempt < CDN_RETRIES:
                await asyncio.sleep(CDN_RETRY_DELAY)
    return None

async def _download_from_media_db(track_id: str, is_video: bool) -> Optional[str]:
    global TG_FLOOD_COOLDOWN
    db_uri = config.DB_URI
    ch_id_str = config.MEDIA_CHANNEL_ID
    if not track_id or TG_APP is None or not db_uri or not ch_id_str:
        return None
    if time.time() < TG_FLOOD_COOLDOWN:
        _inc("tg_flood_skip")
        return None
    try:
        ch_id = int(ch_id_str)
    except Exception:
        return None

    ext = "mp4" if is_video else "mp3"
    keys_to_try = [
        f"{track_id}.{ext}",
        track_id,
        f"{track_id}_{'v' if is_video else 'a'}",
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
    tmp_path = final_path + ".temp"

    if os.path.exists(final_path) and os.path.getsize(final_path) > 0:
        return final_path

    try:
        from pyrogram.errors import FloodWait
        try:
            msg = await TG_APP.get_messages(ch_id, msg_id)
        except FloodWait as e:
            TG_FLOOD_COOLDOWN = time.time() + e.value + 5
            _inc("tg_fail")
            return None

        if not msg:
            _inc("media_db_fail")
            return None

        try:
            dl_res = await asyncio.wait_for(
                TG_APP.download_media(msg, file_name=tmp_path),
                timeout=HARD_TIMEOUT,
            )
        except asyncio.TimeoutError:
            _inc("timeout_fail")
            return None
        except FloodWait as e:
            TG_FLOOD_COOLDOWN = time.time() + e.value + 5
            _inc("tg_fail")
            return None

        fixed = _resolve_if_dir(dl_res) if isinstance(dl_res, str) else None
        if not fixed or not os.path.exists(fixed) or os.path.getsize(fixed) <= 0:
            _inc("media_db_fail")
            try:
                if tmp_path and os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass
            return None

        try:
            if fixed != final_path:
                os.replace(fixed, final_path)
        except Exception:
            final_path = fixed

        if os.path.exists(final_path) and os.path.getsize(final_path) > 0:
            LOGGER.info(f"DB cache hit  › {track_id}")
            return final_path

        _inc("media_db_fail")
        return None

    except Exception:
        _inc("media_db_fail")
        return None

def _extract_candidate(obj: Any) -> Optional[str]:
    if obj is None:
        return None
    if isinstance(obj, str):
        return obj.strip() or None
    if isinstance(obj, list) and obj:
        return _extract_candidate(obj[0])
    if isinstance(obj, dict):
        job = obj.get("job")
        if isinstance(job, dict):
            res = job.get("result")
            if isinstance(res, dict):
                for k in ("public_url", "cdnurl", "download_url", "url"):
                    v = res.get(k)
                    if isinstance(v, str) and v.strip():
                        return v.strip()
        for k in ("public_url", "cdnurl", "download_url", "url", "tg_link"):
            v = obj.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        for wrap in ("result", "results", "data", "items"):
            v = obj.get(wrap)
            if v:
                c = _extract_candidate(v)
                if c:
                    return c
    return None

def _normalize_candidate_to_url(candidate: str, api_url: str) -> Optional[str]:
    if not candidate:
        return None
    c = candidate.strip()
    if c.startswith(("http://", "https://")):
        return c
    if c.startswith("/"):
        if c.startswith(("/root", "/home")):
            return None
        return f"{api_url.rstrip('/')}{c}" if api_url else None
    return f"{api_url.rstrip('/')}/{c.lstrip('/')}" if api_url else None

async def _v2_create_job(
    session: aiohttp.ClientSession,
    api_url: str,
    api_key: str,
    video_id: str,
    is_video: bool,
) -> Optional[str]:
    endpoint = f"{api_url.rstrip('/')}/youtube/v2/download"
    params = {"api_key": api_key, "query": video_id, "isVideo": str(is_video).lower()}
    for _ in range(3):
        try:
            async with session.get(endpoint, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    await asyncio.sleep(1)
                    continue
                data = await resp.json(content_type=None)
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

async def _v2_get_url(
    session: aiohttp.ClientSession,
    api_url: str,
    job_id: str,
    retries: int = 12,
) -> Optional[str]:
    endpoint = f"{api_url.rstrip('/')}/youtube/jobStatus"
    params = {"job_id": job_id}
    for attempt in range(1, retries + 1):
        try:
            async with session.get(endpoint, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
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
                    break
                full_url = (
                    f"{api_url.rstrip('/')}{public_url}"
                    if public_url.startswith("/")
                    else public_url
                )
                LOGGER.info(f"V2 URL ready  › attempt {attempt}")
                return full_url
        except Exception:
            pass
        await asyncio.sleep(3)
    return None

async def _v2_save_file(
    session: aiohttp.ClientSession,
    url: str,
    out_path: str,
) -> Optional[str]:
    _ensure_dir(str(Path(out_path).parent))
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=None)) as resp:
            if resp.status != 200:
                return None
            async with aiofiles.open(out_path, "wb") as f:
                async for chunk in resp.content.iter_chunked(CHUNK_SIZE):
                    if chunk:
                        await f.write(chunk)
        return out_path if os.path.exists(out_path) and os.path.getsize(out_path) > 0 else None
    except Exception as e:
        LOGGER.error(f"V2 save failed: {e}")
        return None

async def v2_download(link: str, media_type: str) -> Optional[str]:
    if not link:
        return None
    api_url = config.API_URL
    api_key = config.API_KEY
    if not config.API_URL or not config.API_KEY:
        return None

    is_video = media_type == "video"
    vid = extract_video_id(str(link))
    query = vid or str(link)
    ext = "mp4" if is_video else "m4a"
    base_name = vid or "audio"
    out_path = os.path.join(DOWNLOAD_DIR, f"{base_name}.{ext}")

    if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
        return out_path

    session = await get_http_session()

    for attempt in range(2):
        job_id = await _v2_create_job(session, api_url, api_key, query, is_video)
        if not job_id:
            _inc("hard_cycle_retries")
            if attempt == 0:
                await asyncio.sleep(2)
            continue

        dl_url = await _v2_get_url(session, api_url, job_id)
        if not dl_url:
            _inc("no_candidate")
            if attempt == 0:
                await asyncio.sleep(2)
            continue

        fpath = await _v2_save_file(session, dl_url, out_path)
        if not fpath:
            _inc("cdn_fail")
            if attempt == 0:
                await asyncio.sleep(2)
            continue

        await increment_api_usage("api_1")
        return fpath

    return None

async def optimized_download(link: str, is_video: bool) -> Optional[str]:
    if not link:
        return None

    vid = extract_video_id(str(link))

    if vid:
        db_path = await _download_from_media_db(vid, is_video=is_video)
        if db_path and os.path.exists(db_path):
            _inc("success")
            _inc("success_video" if is_video else "success_audio")
            return db_path

    api_url = config.API_URL
    api_key = config.API_KEY
    if config.API_URL and config.API_KEY:
        v2_path = await v2_download(str(link), media_type="video" if is_video else "audio")
        if v2_path and os.path.exists(v2_path):
            _inc("success")
            _inc("success_video" if is_video else "success_audio")
            LOGGER.info(f"V2 download   › {vid or link}")
            return v2_path

    return None

class YouTubeAPI:
    def __init__(self):
        self.base = "https://www.youtube.com/watch?v="
        self.listbase = "https://youtube.com/playlist?list="
        self.regex = YOUTUBE_REGEX

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
                        if char == "\\":
                            escape_next = True
                            continue
                        if char == '"':
                            in_string = not in_string
                            continue
                        if not in_string:
                            if char == "{":
                                brace_count += 1
                            elif char == "}":
                                brace_count -= 1
                            if brace_count == 0:
                                json_str = html[start_idx : i + 1]
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
                                            title = "".join(run["text"] for run in vr["title"]["runs"])
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
                LOGGER.error(f"JSON parse error in fast_search: {e}")

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

            best = results_list[0]
            await set_search_cache(query, best)
            return best

        except Exception as e:
            LOGGER.error(f"fast_search failed: {e}")
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
        return text[offset : offset + length]

    async def details(self, link: str, videoid: Union[bool, str] = None):
        _inc("search_total")
        if not link:
            _inc("search_failed")
            raise Exception("❌ Track search query is empty.")

        link = str(link)
        if videoid:
            link = self.base + link
        if not is_safe_url(link):
            _inc("search_failed")
            raise Exception("❌ Blocked by security check.")

        cached = await get_search_cache(link)
        if cached:
            duration_sec = (
                0 if str(cached["duration"]) == "None"
                else int(time_to_seconds(cached["duration"]))
            )
            _inc("search_success")
            return cached["title"], cached["duration"], duration_sec, cached["thumbnail"], cached["video_id"]

        vid = extract_video_id(link)
        last_error = None

        for attempt in range(SEARCH_RETRIES):
            try:
                if vid:
                    results = VideosSearch(f"https://www.youtube.com/watch?v={vid}", limit=1)
                    res = await results.next()
                    if not res or not res.get("result"):
                        raise Exception("PySearch returned no data.")
                    result = res["result"][0]
                    yturl = result.get("link") or f"https://www.youtube.com/watch?v={result.get('id', '')}"
                    title = result.get("title", "Unknown Title")
                    duration_min = result.get("duration", "0:00")
                    vidid = result.get("id", "")
                    thumbs = result.get("thumbnails", [])
                    thumbnail = thumbs[0]["url"].split("?")[0] if thumbs else config.YOUTUBE_IMG_URL
                else:
                    scrape_res = await self.fast_search(link)
                    if not scrape_res:
                        raise Exception("Scraper failed.")
                    if scrape_res.get("title") and scrape_res["title"] != "Unknown Title":
                        title = scrape_res["title"]
                        duration_min = scrape_res["duration"]
                        vidid = scrape_res["video_id"]
                        yturl = scrape_res["url"]
                        thumbnail = scrape_res["thumbnail"]
                    else:
                        results = VideosSearch(f"https://www.youtube.com/watch?v={scrape_res['video_id']}", limit=1)
                        res = await results.next()
                        if not res or not res.get("result"):
                            raise Exception("PySearch returned no data.")
                        result = res["result"][0]
                        yturl = result.get("link") or f"https://www.youtube.com/watch?v={result.get('id', '')}"
                        title = result.get("title", "Unknown Title")
                        duration_min = result.get("duration", "0:00")
                        vidid = result.get("id", "")
                        thumbs = result.get("thumbnails", [])
                        thumbnail = thumbs[0]["url"].split("?")[0] if thumbs else config.YOUTUBE_IMG_URL

                if not is_safe_url(yturl):
                    raise Exception("Unsafe URL returned.")

                duration_sec = 0 if str(duration_min) == "None" else int(time_to_seconds(duration_min))
                await set_search_cache(link, {
                    "title": title, "duration": duration_min,
                    "video_id": vidid, "thumbnail": thumbnail, "url": yturl,
                })
                _inc("search_success")
                return title, duration_min, duration_sec, thumbnail, vidid

            except Exception as e:
                last_error = e
                if attempt < SEARCH_RETRIES - 1:
                    await asyncio.sleep(0.5)

        LOGGER.error(f"details() failed for {link}: {last_error}")
        _inc("search_failed")
        raise Exception(f"❌ Failed to fetch track details: {last_error}")

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
        fallback = config.YOUTUBE_IMG_URL
        if not link:
            return fallback
        link = str(link)
        if videoid:
            link = self.base + link
        if not is_safe_url(link):
            return fallback
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
                        return thumbs[0]["url"].split("?")[0] if thumbs else fallback
                else:
                    scrape_res = await self.fast_search(link)
                    if scrape_res and scrape_res.get("thumbnail"):
                        return scrape_res["thumbnail"]
                raise Exception("No result")
            except Exception:
                if attempt < SEARCH_RETRIES - 1:
                    await asyncio.sleep(0.5)
        return fallback

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
        except asyncio.TimeoutError:
            proc.kill()
            return 0, "Timeout: video extraction took too long"
        return (1, stdout.decode().split("\n")[0]) if stdout else (0, stderr.decode())

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
        except asyncio.TimeoutError:
            proc.kill()
            return []
        try:
            out = stdout.decode("utf-8") if stdout else ""
            return [x for x in out.split("\n") if x]
        except Exception:
            return []

    async def track(self, link: str, videoid: Union[bool, str] = None):
        _inc("search_total")
        if not link:
            _inc("search_failed")
            raise Exception("❌ No search query or link provided.")

        link = str(link)
        if videoid:
            link = self.base + link
        if not is_safe_url(link):
            _inc("search_failed")
            raise Exception("❌ Blocked by security check.")

        cached = await get_search_cache(link)
        if cached:
            _inc("search_success")
            return {
                "title": cached["title"],
                "link": cached["url"],
                "vidid": cached["video_id"],
                "duration_min": cached["duration"],
                "thumb": cached["thumbnail"],
            }, cached["video_id"]

        vid = extract_video_id(link)
        last_error = None

        for attempt in range(SEARCH_RETRIES):
            try:
                if vid:
                    results = VideosSearch(f"https://www.youtube.com/watch?v={vid}", limit=1)
                    res = await results.next()
                    if not res or not res.get("result"):
                        raise Exception("Track not found.")
                    result = res["result"][0]
                    yturl = result.get("link") or f"https://www.youtube.com/watch?v={result.get('id', '')}"
                    title = result.get("title", "Unknown Title")
                    duration_min = result.get("duration", "0:00")
                    vidid = result.get("id", "")
                    thumbs = result.get("thumbnails", [])
                    thumbnail = thumbs[0]["url"].split("?")[0] if thumbs else config.YOUTUBE_IMG_URL
                else:
                    scrape_res = await self.fast_search(link)
                    if not scrape_res:
                        raise Exception("Scraper failed.")
                    if scrape_res.get("title") and scrape_res["title"] != "Unknown Title":
                        title = scrape_res["title"]
                        duration_min = scrape_res["duration"]
                        vidid = scrape_res["video_id"]
                        yturl = scrape_res["url"]
                        thumbnail = scrape_res["thumbnail"]
                    else:
                        results = VideosSearch(f"https://www.youtube.com/watch?v={scrape_res['video_id']}", limit=1)
                        res = await results.next()
                        if not res or not res.get("result"):
                            raise Exception("Track not found.")
                        result = res["result"][0]
                        yturl = result.get("link") or f"https://www.youtube.com/watch?v={result.get('id', '')}"
                        title = result.get("title", "Unknown Title")
                        duration_min = result.get("duration", "0:00")
                        vidid = result.get("id", "")
                        thumbs = result.get("thumbnails", [])
                        thumbnail = thumbs[0]["url"].split("?")[0] if thumbs else config.YOUTUBE_IMG_URL

                if not is_safe_url(yturl):
                    raise Exception("Unsafe URL returned.")

                await set_search_cache(link, {
                    "title": title, "duration": duration_min,
                    "video_id": vidid, "thumbnail": thumbnail, "url": yturl,
                })
                _inc("search_success")
                return {
                    "title": title, "link": yturl,
                    "vidid": vidid, "duration_min": duration_min, "thumb": thumbnail,
                }, vidid

            except Exception as e:
                last_error = e
                if attempt < SEARCH_RETRIES - 1:
                    await asyncio.sleep(0.5)

        LOGGER.error(f"track() failed for {link}: {last_error}")
        _inc("search_failed")
        raise Exception(f"❌ Could not process track after {SEARCH_RETRIES} attempts: {last_error}")

    async def formats(self, link: str, videoid: Union[bool, str] = None):
        if not link:
            return [], ""
        link = str(link)
        if videoid:
            link = self.base + link
        if not is_safe_url(link):
            return [], link
        if "&" in link:
            link = link.split("&")[0]
        ydl_opts = {"quiet": True, "cookiefile": cookie_txt_file()}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            formats_available = []
            r = ydl.extract_info(link, download=False)
            for fmt in r.get("formats", []):
                try:
                    str(fmt["format"])
                except Exception:
                    continue
                if "dash" in str(fmt["format"]).lower():
                    continue
                try:
                    fmt["format"]; fmt["filesize"]; fmt["format_id"]
                    fmt["ext"]; fmt["format_note"]
                except Exception:
                    continue
                formats_available.append({
                    "format": fmt["format"], "filesize": fmt["filesize"],
                    "format_id": fmt["format_id"], "ext": fmt["ext"],
                    "format_note": fmt["format_note"], "yturl": link,
                })
        return formats_available, link

    async def slider(self, link: str, query_type: int, videoid: Union[bool, str] = None):
        _inc("search_total")
        if not link:
            _inc("search_failed")
            raise Exception("❌ Query is empty.")

        link = str(link)
        if videoid:
            link = self.base + link
        if not is_safe_url(link):
            _inc("search_failed")
            raise Exception("❌ Blocked by security check.")

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
                        results = VideosSearch(f"https://www.youtube.com/watch?v={target['video_id']}", limit=1)
                        res = await results.next()
                        if res and res.get("result"):
                            result = res["result"][0]
                            title = result.get("title", "Unknown")
                            duration_min = result.get("duration", "0:00")
                            vidid = result.get("id", target["video_id"])
                            thumbs = result.get("thumbnails", [])
                            thumbnail = thumbs[0]["url"].split("?")[0] if thumbs else config.YOUTUBE_IMG_URL
                            _inc("search_success")
                            return title, duration_min, thumbnail, vidid

                a = VideosSearch(link, limit=10)
                res = await a.next()
                if not res or not res.get("result") or len(res["result"]) <= query_type:
                    raise Exception("Slider data not found.")
                result = res["result"]
                title = result[query_type].get("title", "Unknown")
                duration_min = result[query_type].get("duration", "0:00")
                vidid = result[query_type].get("id", "")
                thumbs = result[query_type].get("thumbnails", [])
                thumbnail = thumbs[0]["url"].split("?")[0] if thumbs else config.YOUTUBE_IMG_URL
                _inc("search_success")
                return title, duration_min, thumbnail, vidid

            except Exception as e:
                last_error = e
                if attempt < SEARCH_RETRIES - 1:
                    await asyncio.sleep(0.5)

        LOGGER.error(f"slider() failed for {link}: {last_error}")
        _inc("search_failed")
        raise Exception(f"❌ Failed to load search options: {last_error}")

    @error_logger(label="YouTube Download")
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
        Download a YouTube track via API-1 (optimized_download) only.
        yt-dlp fallback has been removed — if API-1 fails the download fails.
        """
        _inc("total")
        is_video = bool(video or songvideo)

        if not link:
            _inc("failed")
            _inc("failed_video" if is_video else "failed_audio")
            return None if (songvideo or songaudio) else (None, False)

        link = str(link)
        if videoid:
            link = self.base + link

        if not is_safe_url(link):
            _inc("failed")
            _inc("failed_video" if is_video else "failed_audio")
            return None if (songvideo or songaudio) else (None, False)

        if "&" in link:
            link = link.split("&")[0]

        try:
            optimized_path = await asyncio.wait_for(
                optimized_download(link, is_video), timeout=PROCESS_TIMEOUT
            )
            if optimized_path and os.path.exists(optimized_path):
                _inc("success")
                _inc("success_video" if is_video else "success_audio")
                return optimized_path if (songvideo or songaudio) else (optimized_path, True)
        except asyncio.TimeoutError:
            LOGGER.error(f"API-1 download timed out: {link}")
        except Exception as e:
            LOGGER.error(f"API-1 download error: {e}")

        # API-1 failed — report failure, no yt-dlp fallback
        LOGGER.warning(f"YouTube download failed (API-1 only mode): {link}")
        _inc("failed")
        _inc("failed_video" if is_video else "failed_audio")
        return None if (songvideo or songaudio) else (None, False)
