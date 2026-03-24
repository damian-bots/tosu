# AnonXMusic/utils/Youtube.py
# Updated: yt-dlp Toggle + Race Condition Fix for NoAudioSourceFound

import time
import asyncio
import os
import re
import json
import glob
import random
import logging
from typing import Union, Dict, Optional, Any
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

from AnonXMusic.utils.database import is_on_off, get_search_cache, set_search_cache
from AnonXMusic.utils.formatters import time_to_seconds

import config
from AnonXMusic import app as TG_APP
from AnonXMusic import LOGGER as LOG

# ============================
# TOGGLE SETTINGS
# ============================
# Set to False to completely disable yt-dlp. The bot will ONLY use API/DB cache.
YTDLP_ENABLED = True  

# ============================
# DB NAME & COLLECTION
# ============================
MEDIA_DB_NAME = "arcapi"
MEDIA_COLLECTION_NAME = "medias"

DOWNLOAD_DIR = "downloads"
LOGGER = LOG(__name__)

CHUNK_SIZE = 1024 * 1024
V2_HTTP_RETRIES = 5
V2_DOWNLOAD_CYCLES = 5
SEARCH_RETRIES = 4
HARD_RETRY_WAIT = 3
JOB_POLL_ATTEMPTS = 10
JOB_POLL_INTERVAL = 2.0
JOB_POLL_BACKOFF = 1.2
NO_CANDIDATE_WAIT = 4
CDN_RETRIES = 5
CDN_RETRY_DELAY = 2

HARD_TIMEOUT = 120
PROCESS_TIMEOUT = 120 

TG_FLOOD_COOLDOWN = 0.0

# ============================
# DOWNLOAD & SEARCH STATISTICS
# ============================
DOWNLOAD_STATS: Dict[str, int] = {
    "total": 0, "success": 0, "failed": 0, 
    "success_audio": 0, "success_video": 0,
    "failed_audio": 0, "failed_video": 0, 
    "search_total": 0, "search_success": 0, "search_failed": 0,
    "hard_fail_401": 0, "hard_fail_403": 0,
    "api_fail_other_4xx": 0, "api_fail_5xx": 0, "network_fail": 0, "timeout_fail": 0,
    "no_candidate": 0, "tg_fail": 0, "tg_flood_skip": 0, "cdn_fail": 0,
    "hard_cycle_retries": 0, "media_db_hit": 0, "media_db_miss": 0, "media_db_fail": 0,
    "ytdlp_fallback": 0,
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

# ============================
# GLOBAL SESSION & CLIENTS
# ============================
_session: Optional[aiohttp.ClientSession] = None
_session_lock = asyncio.Lock()
_MONGO_CLIENT: Optional[AsyncIOMotorClient] = None

def cookie_txt_file() -> str:
    folder_path = os.path.join(os.getcwd(), "cookies")
    filename = os.path.join(os.getcwd(), "cookies", "logs.csv")
    txt_files = glob.glob(os.path.join(folder_path, '*.txt'))
    if not txt_files: return "cookies/cookies.txt" 
    cookie_file = random.choice(txt_files)
    try:
        with open(filename, 'a') as file: file.write(f'Choosen File : {cookie_file}\n')
    except Exception: pass
    return f"cookies/{os.path.basename(cookie_file)}"

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

def _resolve_if_dir(download_result: str) -> Optional[str]:
    if not download_result: return None
    p = Path(download_result)
    if p.exists() and p.is_file(): return str(p)
    if p.exists() and p.is_dir():
        files = [x for x in p.iterdir() if x.is_file()]
        if not files: return None
        newest = max(files, key=lambda x: x.stat().st_mtime)
        return str(newest)
    return download_result

# ============================
# 🛡 STRICT GLOBAL FIREWALL 🛡
# ============================
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
                LOGGER.warning(f"🚫 BLOCKED MALICIOUS TEXT QUERY: {text}")
                return False
        except: return False
        return True
    
    try:
        target_url = text
        if target_url.lower().startswith("www."):
            target_url = "https://" + target_url
            
        decoded_url = unquote(target_url)
        
        if any(char in decoded_url for char in DANGEROUS_CHARS):
            LOGGER.warning(f"🚫 BLOCKED MALICIOUS INJECTION: {text}")
            return False
            
        parsed = urlparse(target_url)
        domain = parsed.netloc.replace("www.", "")
        
        if domain not in ALLOWED_DOMAINS:
            LOGGER.warning(f"🚫 BLOCKED INVALID DOMAIN: {domain}")
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

async def check_file_size(link):
    if not link or not is_safe_url(str(link)): return None
    async def get_format_info(url):
        proc = await asyncio.create_subprocess_exec("yt-dlp", "-J", url, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        except asyncio.TimeoutError:
            proc.kill()
            return None
        if proc.returncode != 0: return None
        return json.loads(stdout.decode())
        
    def parse_size(formats):
        total_size = sum([f['filesize'] for f in formats if 'filesize' in f])
        return total_size
        
    info = await get_format_info(str(link))
    if info is None: return None
    formats = info.get('formats', [])
    if not formats: return None
    return parse_size(formats)


# ============================
# DOWNLOAD HANDLERS (V2 & CDN)
# ============================
async def _download_from_cdn(cdn_url: str, out_path: str) -> Optional[str]:
    if not cdn_url: return None
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
                        if not chunk: break
                        await f.write(chunk)
            if os.path.exists(out_path): return out_path
            return None
        except asyncio.TimeoutError:
            _inc("timeout_fail")
            if attempt < CDN_RETRIES: await asyncio.sleep(CDN_RETRY_DELAY); continue
            return None
        except Exception:
            _inc("network_fail")
            if attempt < CDN_RETRIES: await asyncio.sleep(CDN_RETRY_DELAY); continue
            return None
    return None

async def _download_from_media_db(track_id: str, is_video: bool) -> Optional[str]:
    global TG_FLOOD_COOLDOWN
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
    tmp_path = final_path + ".temp"
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
        try: dl_res = await asyncio.wait_for(TG_APP.download_media(msg, file_name=tmp_path), timeout=HARD_TIMEOUT)
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
                if tmp_path and os.path.exists(tmp_path): os.remove(tmp_path)
            except: pass
            return None
        try:
            if fixed != final_path: os.replace(fixed, final_path)
        except Exception: final_path = fixed
        if os.path.exists(final_path) and os.path.getsize(final_path) > 0: return final_path
        _inc("media_db_fail")
        return None
    except Exception:
        _inc("media_db_fail")
        return None

def _extract_candidate(obj: Any) -> Optional[str]:
    if obj is None: return None
    if isinstance(obj, str): return obj.strip() if obj.strip() else None
    if isinstance(obj, list) and obj: return _extract_candidate(obj[0])
    if isinstance(obj, dict):
        job = obj.get("job")
        if isinstance(job, dict):
            res = job.get("result")
            if isinstance(res, dict):
                for k in ("public_url", "cdnurl", "download_url", "url"):
                    v = res.get(k)
                    if isinstance(v, str) and v.strip(): return v.strip()
        for k in ("public_url", "cdnurl", "download_url", "url", "tg_link"):
            v = obj.get(k)
            if isinstance(v, str) and v.strip(): return v.strip()
        for wrap in ("result", "results", "data", "items"):
            v = obj.get(wrap)
            if v:
                c = _extract_candidate(v)
                if c: return c
    return None

def _looks_like_status_text(s: Optional[str]) -> bool:
    if not s: return False
    return any(x in s.lower() for x in ("download started", "background", "jobstatus", "job_id", "processing", "queued"))

def _normalize_candidate_to_url(candidate: str, api_url: str) -> Optional[str]:
    if not candidate: return None
    c = candidate.strip()
    if c.startswith(("http://", "https://")): return c
    if c.startswith("/"):
        if c.startswith(("/root", "/home")): return None
        if api_url: return f"{api_url.rstrip('/')}{c}"
        return None
    if api_url: return f"{api_url.rstrip('/')}/{c.lstrip('/')}"
    return None

def _guess_ext_from_url(u: str, is_video: bool) -> str:
    return "mp4" if is_video else "m4a"

async def _v2_request_json(endpoint: str, params: Dict[str, Any]) -> Optional[Any]:
    api_url = getattr(config, "API_URL", None)
    api_key = getattr(config, "API_KEY", None)
    if not api_url or not api_key: return None
    base = api_url.rstrip("/")
    url = f"{base}/{endpoint.lstrip('/')}"
    if "api_key" not in params: params["api_key"] = api_key
    for attempt in range(1, V2_HTTP_RETRIES + 1):
        try:
            session = await get_http_session()
            async with session.get(url, params=params) as resp:
                try: data = await resp.json(content_type=None)
                except Exception: data = None
                if 200 <= resp.status < 300: return data
                if resp.status in (401, 403): 
                    _inc(f"hard_fail_{resp.status}")
                    return None
                if 500 <= resp.status < 600: _inc("api_fail_5xx")
                else: _inc("api_fail_other_4xx")
                return None
        except asyncio.TimeoutError: _inc("timeout_fail")
        except Exception: _inc("network_fail")
        if attempt < V2_HTTP_RETRIES: await asyncio.sleep(1)
    return None

async def v2_download(link: str, media_type: str) -> Optional[str]:
    if not link: return None
    is_video = (media_type == "video")
    vid = extract_video_id(str(link))
    query = vid or str(link)
    api_url = getattr(config, "API_URL", None)
    for cycle in range(1, V2_DOWNLOAD_CYCLES + 1):
        resp = await _v2_request_json("youtube/v2/download", {"query": query, "isVideo": str(is_video).lower()})
        if not resp:
            _inc("hard_cycle_retries")
            if cycle < V2_DOWNLOAD_CYCLES: await asyncio.sleep(1); continue
            return None
        candidate = _extract_candidate(resp)
        if candidate and _looks_like_status_text(candidate): candidate = None
        job_id = None
        if isinstance(resp, dict):
            job_id = resp.get("job_id") or resp.get("job")
            if isinstance(job_id, dict) and "id" in job_id: job_id = job_id.get("id")
        if job_id and not candidate:
            interval = JOB_POLL_INTERVAL
            for _ in range(1, JOB_POLL_ATTEMPTS + 1):
                await asyncio.sleep(interval)
                status = await _v2_request_json("youtube/jobStatus", {"job_id": str(job_id)})
                candidate = _extract_candidate(status) if status else None
                if candidate and not _looks_like_status_text(candidate): break
                interval *= JOB_POLL_BACKOFF
        if not candidate:
            _inc("no_candidate")
            if cycle < V2_DOWNLOAD_CYCLES: await asyncio.sleep(NO_CANDIDATE_WAIT); continue
            return None
        normalized = _normalize_candidate_to_url(candidate, api_url)
        if not normalized:
            _inc("no_candidate")
            if cycle < V2_DOWNLOAD_CYCLES: await asyncio.sleep(NO_CANDIDATE_WAIT); continue
            return None
        ext = _guess_ext_from_url(normalized, is_video=is_video)
        base_name = vid if vid else "audio"
        out_path = os.path.join(DOWNLOAD_DIR, f"{base_name}.{ext}")
        if os.path.exists(out_path): return out_path
        path = await _download_from_cdn(normalized, out_path)
        if not path:
            _inc("cdn_fail")
            if cycle < V2_DOWNLOAD_CYCLES: await asyncio.sleep(2); continue
        return path
    return None

async def optimized_download(link: str, is_video: bool) -> Optional[str]:
    if not link: return None
    vid = extract_video_id(str(link))
    if vid:
        db_path = await _download_from_media_db(vid, is_video=is_video)
        if db_path and os.path.exists(db_path):
            _inc("success")
            if is_video: _inc("success_video")
            else: _inc("success_audio")
            LOGGER.info(f"✅ DB-CACHE | {vid}")
            return db_path
    api_url = getattr(config, "API_URL", None)
    api_key = getattr(config, "API_KEY", None)
    if api_url and api_key:
        v2_path = await v2_download(str(link), media_type=("video" if is_video else "audio"))
        if v2_path and os.path.exists(v2_path):
            _inc("success")
            if is_video: _inc("success_video")
            else: _inc("success_audio")
            LOGGER.info(f"✅ V2-API | {vid or link}")
            return v2_path
    return None

# ============================
# YOUTUBE API CLASS 
# ============================
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
            match = re.search(r'(?:var ytInitialData|window\["ytInitialData"\])\s*=\s*(\{.*?\});', html)
            if match:
                try:
                    data = json.loads(match.group(1))
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
            if fetch_all: return results_list 

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
            raise Exception("❌ Track search query is empty.")
            
        link = str(link)
        if videoid: link = self.base + link
            
        if not is_safe_url(link): 
            _inc("search_failed")
            raise Exception("❌ Blocked by Security Check.")
            
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
                    if not res or not res.get("result"): raise Exception("❌ PySearch returned no data.")
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
                        if not res or not res.get("result"): raise Exception("❌ PySearch returned no data.")
                        result = res["result"][0]
                        yturl = result.get("link") or f"https://www.youtube.com/watch?v={result.get('id', '')}"
                        title = result.get("title", "Unknown Title")
                        duration_min = result.get("duration", "0:00")
                        vidid = result.get("id", "")
                        thumbs = result.get("thumbnails", [])
                        thumbnail = thumbs[0]["url"].split("?")[0] if thumbs else getattr(config, "YOUTUBE_IMG_URL", "")

                if not is_safe_url(yturl): raise Exception("❌ Unsafe URL Returned.")
                
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
        raise Exception(f"❌ Failed to fetch track details after {SEARCH_RETRIES} attempts: {last_error}")

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
        
        proc = await asyncio.create_subprocess_exec(
            "yt-dlp", "--cookies", cookie_txt_file(), "-g", "-f", "best[height<=?720][width<=?1280]", link,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=PROCESS_TIMEOUT)
        except asyncio.TimeoutError:
            proc.kill()
            return 0, "Timeout Exception: Video Extraction Took Too Long"
            
        if stdout: return 1, stdout.decode().split("\n")[0]
        else: return 0, stderr.decode()

    async def playlist(self, link, limit, user_id, videoid: Union[bool, str] = None):
        if not link: return []
        link = str(link)
        if videoid: link = self.listbase + link
        if not is_safe_url(link): return []
        if "&" in link: link = link.split("&")[0]
        
        proc = await asyncio.create_subprocess_exec(
            "yt-dlp", "-i", "--get-id", "--flat-playlist", 
            "--cookies", cookie_txt_file(), 
            "--playlist-end", str(limit), 
            "--skip-download", link,
            stdout=asyncio.subprocess.PIPE, 
            stderr=asyncio.subprocess.PIPE
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=PROCESS_TIMEOUT)
        except asyncio.TimeoutError:
            proc.kill()
            return []
            
        try:
            out = stdout.decode("utf-8") if stdout else ""
            result = out.split("\n")
            for key in result[:]:
                if key == "": result.remove(key)
        except Exception:
            result = []
        return result

    async def track(self, link: str, videoid: Union[bool, str] = None):
        _inc("search_total")
        
        if not link:
            _inc("search_failed")
            raise Exception("❌ No search query or link provided.")
            
        link = str(link)
        if videoid: link = self.base + link
            
        if not is_safe_url(link): 
            _inc("search_failed")
            raise Exception("❌ Blocked by Security Check.")
        
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
                    if not res or not res.get("result"): raise Exception("❌ Track not found or PySearch failed.")
                        
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
                        if not res or not res.get("result"): raise Exception("❌ Track not found.")
                        result = res["result"][0]
                        yturl = result.get("link") or f"https://www.youtube.com/watch?v={result.get('id', '')}"
                        title = result.get("title", "Unknown Title")
                        duration_min = result.get("duration", "0:00")
                        vidid = result.get("id", "")
                        thumbs = result.get("thumbnails", [])
                        thumbnail = thumbs[0]["url"].split("?")[0] if thumbs else getattr(config, "YOUTUBE_IMG_URL", "")
                
                if not is_safe_url(yturl): raise Exception("❌ Unsafe URL Returned from YouTube.")
                
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
        raise Exception(f"❌ Could not process track request after {SEARCH_RETRIES} attempts: {last_error}")

    async def formats(self, link: str, videoid: Union[bool, str] = None):
        if not link: return [], ""
        link = str(link)
        if videoid: link = self.base + link
        if not is_safe_url(link): return [], link
        if "&" in link: link = link.split("&")[0]
        ytdl_opts = {"quiet": True, "cookiefile": cookie_txt_file()}
        ydl = yt_dlp.YoutubeDL(ytdl_opts)
        with ydl:
            formats_available = []
            r = ydl.extract_info(link, download=False)
            for format in r.get("formats", []):
                try: str(format["format"])
                except Exception: continue
                if "dash" in str(format["format"]).lower(): continue
                try:
                    format["format"]; format["filesize"]; format["format_id"]
                    format["ext"]; format["format_note"]
                except Exception: continue
                formats_available.append({
                    "format": format["format"], "filesize": format["filesize"],
                    "format_id": format["format_id"], "ext": format["ext"],
                    "format_note": format["format_note"], "yturl": link,
                })
        return formats_available, link

    async def slider(self, link: str, query_type: int, videoid: Union[bool, str] = None):
        _inc("search_total")
        if not link:
            _inc("search_failed")
            raise Exception("❌ Query is empty.")
            
        link = str(link)
        if videoid: link = self.base + link
            
        if not is_safe_url(link): 
            _inc("search_failed")
            raise Exception("❌ Blocked by Security Check.")
        
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
                    raise Exception("❌ Slider data not found.")
                    
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
        raise Exception(f"❌ Failed to load search options after {SEARCH_RETRIES} attempts: {last_error}")

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
        title_str = str(title) if title else "Unknown_Track"
        
        if not is_safe_url(link):
            _inc("failed")
            if is_video: _inc("failed_video")
            else: _inc("failed_audio")
            
            if songvideo or songaudio: return None
            return None, False

        if "&" in link:
            link = link.split("&")[0]
            
        loop = asyncio.get_running_loop()

        # ============================================
        # 1. OPTIMIZED DOWNLOAD (API & DB CACHE)
        # ============================================
        try:
            optimized_path = await asyncio.wait_for(optimized_download(link, is_video), timeout=PROCESS_TIMEOUT)
            if optimized_path and os.path.exists(optimized_path):
                if songvideo or songaudio:
                    return optimized_path
                # FIX: Always returning False for direct so stream.py queues it safely as 'vid_id'
                # This explicitly prevents 'NoAudioSourceFound' caused by autoclean deleting cached files!
                return optimized_path, False 
        except asyncio.TimeoutError:
            LOGGER.error(f"Optimized Download Timed Out (100s limit): {link}")
        except Exception as e:
            LOGGER.error(f"Optimized Download Failed: {e}")

        # ============================================
        # 2. FALLBACK TO YT-DLP (ONLY IF ENABLED)
        # ============================================
        if not YTDLP_ENABLED:
            LOGGER.warning(f"🚫 yt-dlp fallback is disabled by YTDLP_ENABLED toggle. Failing download for: {link}")
            _inc("failed")
            if is_video: _inc("failed_video")
            else: _inc("failed_audio")
            if songvideo or songaudio: return None
            return None, False

        LOGGER.info(f"⚠️ YT-DLP DL FALLBACK | {link}")
        
        def song_video_dl():
            formats = f"{format_id}+140"
            fpath = f"downloads/{title_str}"
            ydl_optssx = {
                "format": formats, "outtmpl": fpath, "geo_bypass": True,
                "nocheckcertificate": True, "quiet": True, "no_warnings": True,
                "cookiefile": cookie_txt_file(), "prefer_ffmpeg": True,
                "merge_output_format": "mp4",
            }
            x = yt_dlp.YoutubeDL(ydl_optssx)
            x.download([link])

        def song_audio_dl():
            fpath = f"downloads/{title_str}.%(ext)s"
            ydl_optssx = {
                "format": format_id, "outtmpl": fpath, "geo_bypass": True,
                "nocheckcertificate": True, "quiet": True, "no_warnings": True,
                "cookiefile": cookie_txt_file(), "prefer_ffmpeg": True,
                "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}],
            }
            x = yt_dlp.YoutubeDL(ydl_optssx)
            x.download([link])
            
        def audio_dl():
            ydl_optssx = {
                "format": "bestaudio/best", 
                "outtmpl": "downloads/%(id)s.%(ext)s", 
                "geo_bypass": True,
                "nocheckcertificate": True, 
                "quiet": True, 
                "cookiefile": cookie_txt_file(), 
                "no_warnings": True,
            }
            x = yt_dlp.YoutubeDL(ydl_optssx)
            info = x.extract_info(link, download=True)
            xyz = x.prepare_filename(info)
            
            # Fallback extension checker in case yt-dlp converted it
            if not os.path.exists(xyz):
                LOGGER.error(f"Expected audio file not found at {xyz}. Checking other extensions...")
                base, _ = os.path.splitext(xyz)
                for ext in ['.webm', '.m4a', '.mp3', '.ogg']:
                    if os.path.exists(base + ext):
                        return base + ext
            return xyz

        def video_dl():
            ydl_optssx = {
                "format": "(bestvideo[height<=?720][width<=?1280][ext=mp4])+(bestaudio[ext=m4a])",
                "outtmpl": "downloads/%(id)s.%(ext)s", 
                "geo_bypass": True, 
                "nocheckcertificate": True,
                "quiet": True, 
                "cookiefile": cookie_txt_file(), 
                "no_warnings": True,
                "merge_output_format": "mp4", # Force FFmpeg to output mp4
            }
            x = yt_dlp.YoutubeDL(ydl_optssx)
            info = x.extract_info(link, download=True)
            xyz = x.prepare_filename(info)
            
            # Fallback extension checker
            if not os.path.exists(xyz):
                LOGGER.error(f"Expected video file not found at {xyz}. Checking other extensions...")
                base, _ = os.path.splitext(xyz)
                for ext in ['.mp4', '.mkv', '.webm']:
                    if os.path.exists(base + ext):
                        return base + ext
            return xyz
        
        try:
            if songvideo:
                await asyncio.wait_for(loop.run_in_executor(None, song_video_dl), timeout=PROCESS_TIMEOUT)
                _inc("success"); _inc("success_video")
                return f"downloads/{title_str}.mp4"
            
            if songaudio:
                await asyncio.wait_for(loop.run_in_executor(None, song_audio_dl), timeout=PROCESS_TIMEOUT)
                _inc("success"); _inc("success_audio")
                return f"downloads/{title_str}.mp3"
                
            if video:
                if await is_on_off(1):
                    direct = True
                    downloaded_file = await asyncio.wait_for(loop.run_in_executor(None, video_dl), timeout=PROCESS_TIMEOUT)
                else:
                    proc = await asyncio.create_subprocess_exec(
                        "yt-dlp", "--cookies", cookie_txt_file(), "-g", "-f", "best[height<=?720][width<=?1280]", link,
                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                    )
                    try:
                        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=PROCESS_TIMEOUT)
                    except asyncio.TimeoutError:
                        proc.kill()
                        raise Exception("YT-DLP Subprocess Timed Out")
                        
                    if stdout:
                        downloaded_file = stdout.decode().split("\n")[0]
                        direct = False
                    else:
                        file_size = await check_file_size(link)
                        if not file_size: 
                            _inc("failed"); _inc("failed_video")
                            return None, False
                        total_size_mb = file_size / (1024 * 1024)
                        if total_size_mb > 250:
                            LOGGER.error(f"File size {total_size_mb:.2f} MB exceeds limit")
                            _inc("failed"); _inc("failed_video")
                            return None, False
                        direct = True
                        downloaded_file = await asyncio.wait_for(loop.run_in_executor(None, video_dl), timeout=PROCESS_TIMEOUT)
                        
                _inc("success"); _inc("success_video")
            else:
                direct = True
                downloaded_file = await asyncio.wait_for(loop.run_in_executor(None, audio_dl), timeout=PROCESS_TIMEOUT)
                _inc("success"); _inc("success_audio")
            
            if direct and isinstance(downloaded_file, str) and not os.path.exists(downloaded_file):
                LOGGER.error(f"CRITICAL: Final downloaded file missing before sending to PyTgCalls: {downloaded_file}")
                
            return downloaded_file, direct
            
        except asyncio.TimeoutError:
            LOGGER.error(f"YT-DLP Download Timed Out (100s limit): {link}")
            _inc("failed")
            if is_video: _inc("failed_video")
            else: _inc("failed_audio")
            if songvideo or songaudio: return None
            return None, False
            
        except Exception as e:
            LOGGER.error(f"YT-DLP fallback also failed: {e}")
            _inc("failed")
            if is_video: _inc("failed_video")
            else: _inc("failed_audio")
            if songvideo or songaudio: return None
            return None, False
