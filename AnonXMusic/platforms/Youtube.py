# AnonXMusic/utils/Youtube.py
# Updated: Anonx Optimized Version
# Stable API & DB Download Implementation
# Flow: DB Cache -> V2 API -> yt-dlp Fallback

import asyncio
import os
import re
import time
import uuid
import contextlib
import json
import glob
import random
import logging
from pathlib import Path
from typing import Union, Dict, Optional, Any
from urllib.parse import urlparse

import aiofiles
import aiohttp
from aiohttp import TCPConnector
from motor.motor_asyncio import AsyncIOMotorClient
import yt_dlp
from pyrogram.enums import MessageEntityType
from pyrogram.types import Message
from pyrogram.errors import FloodWait
from youtubesearchpython.__future__ import VideosSearch

from AnonXMusic.utils.database import is_on_off
from AnonXMusic.utils.formatters import time_to_seconds

# ============================
# CONFIGURATION IMPORTS
# ============================
try:
    from config import API_URL
except Exception:
    API_URL = None

try:
    from config import API_KEY
except Exception:
    API_KEY = None

try:
    from config import DB_URI
except Exception:
    DB_URI = None

try:
    from config import MEDIA_CHANNEL_ID
except Exception:
    MEDIA_CHANNEL_ID = None

try:
    from AnonXMusic import app as TG_APP
except Exception:
    TG_APP = None

# ============================
# DB NAME & COLLECTION (SET IN CODE)
# ============================
MEDIA_DB_NAME = "anonx_media"
MEDIA_COLLECTION_NAME = "cached_medias"

# ============================
# DOWNLOAD DIRECTORY
# ============================
DOWNLOAD_DIR = "downloads"

# ============================
# LOGGER SETUP
# ============================
LOGGER = logging.getLogger(__name__)

# ============================
# TUNING CONSTANTS
# ============================
CHUNK_SIZE = 1024 * 1024
V2_HTTP_RETRIES = 5
V2_DOWNLOAD_CYCLES = 5
HARD_RETRY_WAIT = 3
JOB_POLL_ATTEMPTS = 10
JOB_POLL_INTERVAL = 2.0
JOB_POLL_BACKOFF = 1.2
NO_CANDIDATE_WAIT = 4
CDN_RETRIES = 5
CDN_RETRY_DELAY = 2
HARD_TIMEOUT = 120

# ============================
# CIRCUIT BREAKER
# ============================
TG_FLOOD_COOLDOWN = 0.0

# ============================
# DOWNLOAD STATISTICS
# ============================
DOWNLOAD_STATS: Dict[str, int] = {
    "total": 0,
    "success": 0,
    "failed": 0,
    "success_audio": 0,
    "success_video": 0,
    "failed_audio": 0,
    "failed_video": 0,
    "hard_fail_401": 0,
    "hard_fail_403": 0,
    "api_fail_other_4xx": 0,
    "api_fail_5xx": 0,
    "network_fail": 0,
    "timeout_fail": 0,
    "no_candidate": 0,
    "tg_fail": 0,
    "tg_flood_skip": 0,
    "cdn_fail": 0,
    "hard_cycle_retries": 0,
    "media_db_hit": 0,
    "media_db_miss": 0,
    "media_db_fail": 0,
    "ytdlp_fallback": 0,
}


def get_download_stats() -> Dict[str, int]:
    return dict(DOWNLOAD_STATS)


def reset_download_stats() -> None:
    for k in list(DOWNLOAD_STATS.keys()):
        DOWNLOAD_STATS[k] = 0


def _inc(key: str, n: int = 1) -> None:
    DOWNLOAD_STATS[key] = DOWNLOAD_STATS.get(key, 0) + n


# ============================
# GLOBAL SESSION & CLIENTS
# ============================
_session: Optional[aiohttp.ClientSession] = None
_session_lock = asyncio.Lock()
_MONGO_CLIENT: Optional[AsyncIOMotorClient] = None
_inflight: Dict[str, asyncio.Future] = {}
_inflight_lock = asyncio.Lock()


# ============================
# COOKIE FILE HANDLER
# ============================
def cookie_txt_file() -> Optional[str]:
    """Randomly select a cookie file from cookies directory."""
    folder_path = os.path.join(os.getcwd(), "cookies")
    filename = os.path.join(os.getcwd(), "cookies", "logs.csv")
    txt_files = glob.glob(os.path.join(folder_path, '*.txt'))
    
    if not txt_files:
        return None
    
    cookie_file = random.choice(txt_files)
    
    with contextlib.suppress(Exception):
        with open(filename, 'a') as file:
            file.write(f'Choosen File : {cookie_file}\n')
    
    return f"cookies/{os.path.basename(cookie_file)}"


# ============================
# HTTP SESSION MANAGER
# ============================
async def get_http_session() -> aiohttp.ClientSession:
    """Get or create HTTP session with proper timeouts."""
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


# ============================
# MONGODB CONNECTION
# ============================
def _get_media_collection():
    """Get MongoDB collection for media caching."""
    global _MONGO_CLIENT
    
    if not DB_URI:
        return None
    
    if _MONGO_CLIENT is None:
        _MONGO_CLIENT = AsyncIOMotorClient(DB_URI)
    
    db = _MONGO_CLIENT[MEDIA_DB_NAME]
    return db[MEDIA_COLLECTION_NAME]


async def is_media(track_id: str, isVideo: bool = False) -> bool:
    """Check if media exists in database."""
    col = _get_media_collection()
    if col is None:
        return False
    
    with contextlib.suppress(Exception):
        doc = await col.find_one({"track_id": track_id, "isVideo": isVideo}, {"_id": 1})
        return bool(doc)
    return False


async def get_media_id(track_id: str, isVideo: bool = False) -> Optional[int]:
    """Get message ID for media from database."""
    col = _get_media_collection()
    if col is None:
        return None
    
    with contextlib.suppress(Exception):
        doc = await col.find_one({"track_id": track_id, "isVideo": isVideo}, {"message_id": 1})
        if doc and doc.get("message_id"):
            return int(doc.get("message_id"))
    return None


# ============================
# UTILITY FUNCTIONS
# ============================
def _ensure_dir(p: str) -> None:
    """Ensure directory exists."""
    os.makedirs(p, exist_ok=True)


def _resolve_if_dir(download_result: str) -> Optional[str]:
    """Resolve directory to actual file path."""
    if not download_result:
        return None
    
    p = Path(download_result)
    
    if p.exists() and p.is_file():
        return str(p)
    
    if p.exists() and p.is_dir():
        files = [x for x in p.iterdir() if x.is_file()]
        if not files:
            return None
        newest = max(files, key=lambda x: x.stat().st_mtime)
        return str(newest)
    
    return download_result


# ============================
# URL SECURITY FUNCTIONS
# ============================
def is_safe_url(text: str) -> bool:
    """
    Validate URL - only allows YouTube URLs, passes plain text through.
    
    - Returns True for plain text (not a URL)
    - Returns True for YouTube URLs only
    - Returns False for any other URL
    """
    ALLOWED_DOMAINS = {
        "youtube.com", "www.youtube.com", "m.youtube.com", 
        "youtu.be", "music.youtube.com"
    }
    
    if not text:
        return False
    
    is_url = text.strip().lower().startswith(("http:", "https:", "www."))
    
    if not is_url:
        return True
    
    with contextlib.suppress(Exception):
        target_url = text.strip()
        if target_url.lower().startswith("www."):
            target_url = "https://" + target_url
        
        parsed = urlparse(target_url)
        domain = parsed.netloc.replace("www.", "")
        
        return domain in ALLOWED_DOMAINS
    
    return False


def is_safe_youtube_url(url: str) -> bool:
    return is_safe_url(url)


YOUTUBE_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{11}$")
YOUTUBE_ID_IN_URL_RE = re.compile(r"""(?x)(?:v=|\/)([A-Za-z0-9_-]{11})|youtu\.be\/([A-Za-z0-9_-]{11})""")


def extract_video_id(link: str) -> str:
    """Extract and validate YouTube video ID from URL or raw ID."""
    if not link:
        return ""
    
    s = link.strip()
    
    if YOUTUBE_ID_RE.match(s):
        return s
    
    m = YOUTUBE_ID_IN_URL_RE.search(s)
    if m:
        extracted = m.group(1) or m.group(2) or ""
        if YOUTUBE_ID_RE.match(extracted):
            return extracted
    
    candidate = ""
    if "v=" in s:
        candidate = s.split("v=")[-1].split("&")[0]
    else:
        candidate = s.split("/")[-1].split("?")[0]
    
    if YOUTUBE_ID_RE.match(candidate):
        return candidate
    
    return ""


# ============================
# EXCEPTIONS
# ============================
class V2HardAPIError(Exception):
    """Exception for hard API errors (401, 403)."""
    def __init__(self, status: int, body_preview: str = ""):
        super().__init__(f"Hard API error status={status}")
        self.status = status
        self.body_preview = body_preview[:200]


# ============================
# CDN DOWNLOAD
# ============================
async def _download_from_cdn(cdn_url: str, out_path: str) -> Optional[str]:
    """Download file from CDN URL with retries."""
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
                        if not chunk:
                            break
                        await f.write(chunk)
            
            if os.path.exists(out_path):
                return out_path
            return None
            
        except asyncio.TimeoutError:
            _inc("timeout_fail")
            if attempt < CDN_RETRIES:
                await asyncio.sleep(CDN_RETRY_DELAY)
                continue
            return None
            
        except aiohttp.ClientError:
            _inc("network_fail")
            if attempt < CDN_RETRIES:
                await asyncio.sleep(CDN_RETRY_DELAY)
                continue
            return None
            
        except Exception:
            _inc("network_fail")
            return None
    
    return None


# ============================
# MEDIA DB DOWNLOAD
# ============================
async def _download_from_media_db(track_id: str, is_video: bool) -> Optional[str]:
    """Download media from Telegram DB cache."""
    global TG_FLOOD_COOLDOWN
    
    if not track_id or TG_APP is None or not DB_URI or not MEDIA_CHANNEL_ID:
        return None
    
    if time.time() < TG_FLOOD_COOLDOWN:
        _inc("tg_flood_skip")
        return None
    
    try:
        ch_id = int(MEDIA_CHANNEL_ID)
    except Exception:
        return None
    
    ext = "mp4" if is_video else "mp3"
    
    keys_to_try = [
        f"{track_id}.{ext}",
        track_id,
        f"{track_id}_{'v' if is_video else 'a'}",
        f"{track_id}_{'v' if is_video else 'a'}.{ext}",
    ]
    
    msg_id: Optional[int] = None
    
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
    
    out_dir = str(Path(DOWNLOAD_DIR))
    _ensure_dir(out_dir)
    
    final_path = os.path.join(out_dir, f"{track_id}.{ext}")
    tmp_path = final_path + ".temp"
    
    if os.path.exists(final_path) and os.path.getsize(final_path) > 0:
        return final_path
    
    try:
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
            dl_res = await asyncio.wait_for(TG_APP.download_media(msg, file_name=tmp_path), timeout=HARD_TIMEOUT)
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
            with contextlib.suppress(Exception):
                if tmp_path and os.path.exists(tmp_path):
                    os.remove(tmp_path)
            return None
        
        with contextlib.suppress(Exception):
            if fixed != final_path:
                os.replace(fixed, final_path)
        
        if os.path.exists(final_path) and os.path.getsize(final_path) > 0:
            return final_path
        
        _inc("media_db_fail")
        return None
        
    except Exception:
        _inc("media_db_fail")
        return None


# ============================
# V2 API REQUEST
# ============================
def _extract_candidate(obj: Any) -> Optional[str]:
    """Extract download URL candidate from API response."""
    if obj is None:
        return None
    
    if isinstance(obj, str):
        s = obj.strip()
        return s if s else None
    
    if isinstance(obj, list) and obj:
        return _extract_candidate(obj[0])
    
    if isinstance(obj, dict):
        job = obj.get("job")
        if isinstance(job, dict):
            res = job.get("result")
            if isinstance(res, dict):
                for k in ("public_url", "cdnurl", "download_url", "url", "tg_link", "telegram_link", "message_link"):
                    v = res.get(k)
                    if isinstance(v, str) and v.strip():
                        return v.strip()
        
        for k in ("public_url", "cdnurl", "download_url", "url", "tg_link", "telegram_link", "message_link"):
            v = obj.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        
        for wrap in ("result", "results", "data", "items", "payload", "message"):
            v = obj.get(wrap)
            if v:
                c = _extract_candidate(v)
                if c:
                    return c
    
    return None


def _looks_like_status_text(s: Optional[str]) -> bool:
    """Check if response looks like status text instead of URL."""
    if not s:
        return False
    
    low = s.lower()
    return any(x in low for x in ("download started", "background", "jobstatus", "job_id", "processing", "queued"))


def _normalize_candidate_to_url(candidate: str) -> Optional[str]:
    """Normalize candidate to full URL."""
    if not candidate:
        return None
    
    c = candidate.strip()
    
    if c.startswith(("http://", "https://")):
        return c
    
    if c.startswith("/"):
        if c.startswith("/root") or c.startswith("/home"):
            return None
        if API_URL:
            return f"{API_URL.rstrip('/')}{c}"
        return None
    
    if API_URL:
        return f"{API_URL.rstrip('/')}/{c.lstrip('/')}"
    return None


def _guess_ext_from_url(u: str, is_video: bool) -> str:
    """Guess file extension from URL."""
    with contextlib.suppress(Exception):
        path = urlparse(u).path
        ext = os.path.splitext(path)[1].lstrip(".").lower()
        if ext and ext.isalnum() and len(ext) <= 5:
            return ext
    return "mp4" if is_video else "m4a"


async def _v2_request_json(endpoint: str, params: Dict[str, Any]) -> Optional[Any]:
    """Make V2 API request with retries."""
    if not API_URL or not API_KEY:
        return None
    
    base = API_URL.rstrip("/")
    url = f"{base}/{endpoint.lstrip('/')}"
    
    if "api_key" not in params:
        params["api_key"] = API_KEY
    
    for attempt in range(1, V2_HTTP_RETRIES + 1):
        try:
            session = await get_http_session()
            
            async with session.get(url, params=params, headers={"X-API-Key": API_KEY, "Accept": "application/json"}) as resp:
                text = await resp.text()
                
                try:
                    data = await resp.json(content_type=None)
                except Exception:
                    data = None
                
                if 200 <= resp.status < 300:
                    return data
                
                if resp.status in (401, 403):
                    _inc("hard_fail_401" if resp.status == 401 else "hard_fail_403")
                    raise V2HardAPIError(resp.status, text)
                
                if 500 <= resp.status <= 599:
                    _inc("api_fail_5xx")
                else:
                    _inc("api_fail_other_4xx")
                    return None
                    
        except V2HardAPIError:
            raise
            
        except asyncio.TimeoutError:
            _inc("timeout_fail")
            
        except aiohttp.ClientError:
            _inc("network_fail")
            
        except Exception:
            _inc("network_fail")
        
        if attempt < V2_HTTP_RETRIES:
            await asyncio.sleep(1)
    
    return None


async def v2_download(link: str, media_type: str) -> Optional[str]:
    """Download using V2 API with retries."""
    is_video = (media_type == "video")
    vid = extract_video_id(link)
    query = vid or link
    
    for cycle in range(1, V2_DOWNLOAD_CYCLES + 1):
        try:
            resp = await _v2_request_json("youtube/v2/download", {"query": query, "isVideo": str(is_video).lower()})
        except V2HardAPIError:
            _inc("hard_cycle_retries")
            if cycle < V2_DOWNLOAD_CYCLES:
                await asyncio.sleep(HARD_RETRY_WAIT)
                continue
            return None
        
        if not resp:
            if cycle < V2_DOWNLOAD_CYCLES:
                await asyncio.sleep(1)
                continue
            return None
        
        candidate = _extract_candidate(resp)
        if candidate and _looks_like_status_text(candidate):
            candidate = None
        
        job_id = None
        if isinstance(resp, dict):
            job_id = resp.get("job_id") or resp.get("job")
            if isinstance(job_id, dict) and "id" in job_id:
                job_id = job_id.get("id")
        
        if job_id and not candidate:
            interval = JOB_POLL_INTERVAL
            for _ in range(1, JOB_POLL_ATTEMPTS + 1):
                await asyncio.sleep(interval)
                
                try:
                    status = await _v2_request_json("youtube/jobStatus", {"job_id": str(job_id)})
                except V2HardAPIError:
                    _inc("hard_cycle_retries")
                    candidate = None
                    break
                
                candidate = _extract_candidate(status) if status else None
                if candidate and _looks_like_status_text(candidate):
                    candidate = None
                
                if candidate:
                    break
                
                interval *= JOB_POLL_BACKOFF
        
        if not candidate:
            _inc("no_candidate")
            if cycle < V2_DOWNLOAD_CYCLES:
                await asyncio.sleep(NO_CANDIDATE_WAIT)
                continue
            return None
        
        normalized = _normalize_candidate_to_url(candidate)
        if not normalized:
            _inc("no_candidate")
            if cycle < V2_DOWNLOAD_CYCLES:
                await asyncio.sleep(NO_CANDIDATE_WAIT)
                continue
            return None
        
        ext = _guess_ext_from_url(normalized, is_video=is_video)
        base_name = vid if vid else uuid.uuid4().hex[:10]
        out_path = os.path.join(str(Path(DOWNLOAD_DIR)), f"{base_name}.{ext}")
        
        if os.path.exists(out_path):
            return out_path
        
        path = await _download_from_cdn(normalized, out_path)
        
        if not path:
            _inc("cdn_fail")
            if cycle < V2_DOWNLOAD_CYCLES:
                await asyncio.sleep(2)
                continue
        
        return path
    
    return None


# ============================
# DEDUPLICATION
# ============================
async def deduplicate_download(key: str, runner):
    """Deduplicate concurrent download requests."""
    async with _inflight_lock:
        if fut := _inflight.get(key):
            return await fut
        fut = asyncio.get_running_loop().create_future()
        _inflight[key] = fut
    
    try:
        result = await runner()
        if not fut.done():
            fut.set_result(result)
        return result
    except Exception as e:
        if not fut.done():
            fut.set_exception(e)
        return None
    finally:
        async with _inflight_lock:
            if _inflight.get(key) == fut:
                _inflight.pop(key, None)


# ============================
# MAIN DOWNLOAD FUNCTION (Optimized Path)
# ============================
async def media_download(link: str, type: str, title: str = "", video_id: str = None, validate_url: bool = False) -> Optional[str]:
    """
    Main download function with DB caching and API fallback.
    
    Args:
        link: YouTube URL or video ID or plain text query
        type: "audio" or "video"
        title: Optional title for logging
        video_id: Optional pre-extracted video ID
        validate_url: If True, validate URL is YouTube (default: False)
    
    Returns:
        Path to downloaded file or None on failure
    """
    _inc("total")
    
    if not link:
        _inc("failed")
        return None
    
    if validate_url and not is_safe_url(link):
        _inc("failed")
        return None
    
    vid = video_id if video_id else extract_video_id(link)
    dedup_id = vid or link.strip()
    key = f"{type}:{dedup_id}"
    
    async def _cycle():
        is_video = (type == "video")
        is_audio = (type == "audio")
        
        # 1) Try DB cache first
        if vid:
            db_path = await _download_from_media_db(vid, is_video=is_video)
            if db_path and os.path.exists(db_path):
                _inc("success")
                if is_audio:
                    _inc("success_audio")
                else:
                    _inc("success_video")
                LOGGER.info(f"✅ DB-CACHE | {vid}")
                return db_path
        
        # 2) Try V2 API if configured
        if API_URL and API_KEY:
            v2_path = await v2_download(link, media_type=("video" if is_video else "audio"))
            if v2_path and os.path.exists(v2_path):
                _inc("success")
                if is_audio:
                    _inc("success_audio")
                else:
                    _inc("success_video")
                LOGGER.info(f"✅ V2-API | {vid or link}")
                return v2_path
        
        _inc("failed")
        if is_audio:
            _inc("failed_audio")
        else:
            _inc("failed_video")
        
        return None
    
    async def run():
        try:
            return await asyncio.wait_for(_cycle(), timeout=HARD_TIMEOUT)
        except asyncio.TimeoutError:
            _inc("timeout_fail")
            _inc("failed")
            return None
    
    return await deduplicate_download(key, run)


# ============================
# YT-DLP FALLBACK FUNCTIONS
# ============================
async def check_file_size(link: str) -> Optional[int]:
    """Check file size of video before download."""
    async def get_format_info(link: str):
        cookie_file = cookie_txt_file()
        args = ["yt-dlp", "-J", link]
        
        if cookie_file:
            args.extend(["--cookies", cookie_file])
        
        proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await proc.communicate()
        
        if proc.returncode != 0:
            return None
        
        return json.loads(stdout.decode())
    
    def parse_size(formats):
        total_size = 0
        for fmt in formats:
            if 'filesize' in fmt and fmt['filesize']:
                total_size += fmt['filesize']
        return total_size
    
    info = await get_format_info(link)
    if info is None:
        return None
    
    formats = info.get('formats', [])
    if not formats:
        return None
    
    return parse_size(formats)


async def shell_cmd(cmd: str) -> str:
    """Execute shell command asynchronously."""
    proc = await asyncio.create_subprocess_shell(cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, errorz = await proc.communicate()
    
    if errorz:
        if "unavailable videos are hidden" in (errorz.decode("utf-8")).lower():
            return out.decode("utf-8")
        else:
            return errorz.decode("utf-8")
    
    return out.decode("utf-8")


def _ytdlp_audio_dl(link: str, cookie_file: Optional[str]) -> str:
    """Download audio using yt-dlp (sync function for executor)."""
    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": f"{DOWNLOAD_DIR}/%(id)s.%(ext)s",
        "geo_bypass": True,
        "nocheckcertificate": True,
        "quiet": True,
        "no_warnings": True,
    }
    
    if cookie_file:
        ydl_opts["cookiefile"] = cookie_file
    
    x = yt_dlp.YoutubeDL(ydl_opts)
    info = x.extract_info(link, False)
    xyz = os.path.join(DOWNLOAD_DIR, f"{info['id']}.{info['ext']}")
    
    if os.path.exists(xyz):
        return xyz
    
    x.download([link])
    return xyz


def _ytdlp_video_dl(link: str, cookie_file: Optional[str]) -> str:
    """Download video using yt-dlp (sync function for executor)."""
    ydl_opts = {
        "format": "(bestvideo[height<=?720][width<=?1280][ext=mp4])+(bestaudio[ext=m4a])",
        "outtmpl": f"{DOWNLOAD_DIR}/%(id)s.%(ext)s",
        "geo_bypass": True,
        "nocheckcertificate": True,
        "quiet": True,
        "no_warnings": True,
    }
    
    if cookie_file:
        ydl_opts["cookiefile"] = cookie_file
    
    x = yt_dlp.YoutubeDL(ydl_opts)
    info = x.extract_info(link, False)
    xyz = os.path.join(DOWNLOAD_DIR, f"{info['id']}.{info['ext']}")
    
    if os.path.exists(xyz):
        return xyz
    
    x.download([link])
    return xyz


def _ytdlp_song_video_dl(link: str, format_id: str, title: str, cookie_file: Optional[str]) -> str:
    """Download song video using yt-dlp (sync function for executor)."""
    formats = f"{format_id}+140"
    fpath = f"{DOWNLOAD_DIR}/{title}"
    
    ydl_opts = {
        "format": formats,
        "outtmpl": fpath,
        "geo_bypass": True,
        "nocheckcertificate": True,
        "quiet": True,
        "no_warnings": True,
        "prefer_ffmpeg": True,
        "merge_output_format": "mp4",
    }
    
    if cookie_file:
        ydl_opts["cookiefile"] = cookie_file
    
    x = yt_dlp.YoutubeDL(ydl_opts)
    x.download([link])
    return f"{DOWNLOAD_DIR}/{title}.mp4"


def _ytdlp_song_audio_dl(link: str, format_id: str, title: str, cookie_file: Optional[str]) -> str:
    """Download song audio using yt-dlp (sync function for executor)."""
    fpath = f"{DOWNLOAD_DIR}/{title}.%(ext)s"
    
    ydl_opts = {
        "format": format_id,
        "outtmpl": fpath,
        "geo_bypass": True,
        "nocheckcertificate": True,
        "quiet": True,
        "no_warnings": True,
        "prefer_ffmpeg": True,
        "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}],
    }
    
    if cookie_file:
        ydl_opts["cookiefile"] = cookie_file
    
    x = yt_dlp.YoutubeDL(ydl_opts)
    x.download([link])
    return f"{DOWNLOAD_DIR}/{title}.mp3"


# ============================
# YOUTUBE API CLASS
# ============================
class YouTubeAPI:
    """YouTube API class for searching and fetching video info."""
    
    def __init__(self):
        self.base = "https://www.youtube.com/watch?v="
        self.regex = r"(?:youtube\.com|youtu\.be)"
        self.status = "https://www.youtube.com/oembed?url="
        self.listbase = "https://youtube.com/playlist?list="
        self.reg = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
    
    async def exists(self, link: str, videoid: Union[bool, str] = None) -> bool:
        """Check if YouTube link exists."""
        if videoid:
            link = self.base + link
        
        return bool(re.search(self.regex, link))
    
    async def url(self, message_1: Message) -> Union[str, None]:
        """Extract URL from message."""
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
        
        if offset in (None,):
            return None
        
        return text[offset: offset + length]
    
    async def details(self, link: str, videoid: Union[bool, str] = None):
        """Get video details."""
        if videoid:
            link = self.base + link
        
        if "&" in link:
            link = link.split("&")[0]
        
        results = VideosSearch(link, limit=1)
        
        for result in (await results.next())["result"]:
            title = result["title"]
            duration_min = result["duration"]
            thumbnail = result["thumbnails"][0]["url"].split("?")[0]
            vidid = result["id"]
            
            if str(duration_min) == "None":
                duration_sec = 0
            else:
                duration_sec = int(time_to_seconds(duration_min))
        
        return title, duration_min, duration_sec, thumbnail, vidid
    
    async def title(self, link: str, videoid: Union[bool, str] = None) -> str:
        """Get video title."""
        if videoid:
            link = self.base + link
        
        if "&" in link:
            link = link.split("&")[0]
        
        results = VideosSearch(link, limit=1)
        
        for result in (await results.next())["result"]:
            return result["title"]
        
        return ""
    
    async def duration(self, link: str, videoid: Union[bool, str] = None) -> str:
        """Get video duration."""
        if videoid:
            link = self.base + link
        
        if "&" in link:
            link = link.split("&")[0]
        
        results = VideosSearch(link, limit=1)
        
        for result in (await results.next())["result"]:
            return result["duration"]
        
        return ""
    
    async def thumbnail(self, link: str, videoid: Union[bool, str] = None) -> str:
        """Get video thumbnail URL."""
        if videoid:
            link = self.base + link
        
        if "&" in link:
            link = link.split("&")[0]
        
        results = VideosSearch(link, limit=1)
        
        for result in (await results.next())["result"]:
            return result["thumbnails"][0]["url"].split("?")[0]
        
        return ""
    
    async def video(self, link: str, videoid: Union[bool, str] = None):
        """Get direct video URL."""
        if videoid:
            link = self.base + link
        
        if "&" in link:
            link = link.split("&")[0]
        
        cookie_file = cookie_txt_file()
        args = ["yt-dlp", "-g", "-f", "best[height<=?720][width<=?1280]", f"{link}"]
        
        if cookie_file:
            args.extend(["--cookies", cookie_file])
        
        proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await proc.communicate()
        
        if stdout:
            return 1, stdout.decode().split("\n")[0]
        else:
            return 0, stderr.decode()
    
    async def playlist(self, link: str, limit: int, user_id: int, videoid: Union[bool, str] = None):
        """Get playlist video IDs."""
        if videoid:
            link = self.listbase + link
        
        if "&" in link:
            link = link.split("&")[0]
        
        cookie_file = cookie_txt_file()
        cmd = f"yt-dlp -i --get-id --flat-playlist --playlist-end {limit} --skip-download {link}"
        
        if cookie_file:
            cmd = f"yt-dlp -i --get-id --flat-playlist --cookies {cookie_file} --playlist-end {limit} --skip-download {link}"
        
        playlist = await shell_cmd(cmd)
        
        try:
            result = playlist.split("\n")
            result = [key for key in result if key]
        except Exception:
            result = []
        
        return result
    
    async def track(self, link: str, videoid: Union[bool, str] = None):
        """Get track details."""
        if videoid:
            link = self.base + link
        
        if "&" in link:
            link = link.split("&")[0]
        
        results = VideosSearch(link, limit=1)
        
        for result in (await results.next())["result"]:
            title = result["title"]
            duration_min = result["duration"]
            vidid = result["id"]
            yturl = result["link"]
            thumbnail = result["thumbnails"][0]["url"].split("?")[0]
        
        track_details = {
            "title": title,
            "link": yturl,
            "vidid": vidid,
            "duration_min": duration_min,
            "thumb": thumbnail,
        }
        
        return track_details, vidid
    
    async def formats(self, link: str, videoid: Union[bool, str] = None):
        """Get available formats for video."""
        if videoid:
            link = self.base + link
        
        if "&" in link:
            link = link.split("&")[0]
        
        cookie_file = cookie_txt_file()
        ytdl_opts = {"quiet": True}
        
        if cookie_file:
            ytdl_opts["cookiefile"] = cookie_file
        
        ydl = yt_dlp.YoutubeDL(ytdl_opts)
        
        with ydl:
            formats_available = []
            r = ydl.extract_info(link, download=False)
            
            for fmt in r["formats"]:
                try:
                    str(fmt["format"])
                except Exception:
                    continue
                
                if "dash" in str(fmt["format"]).lower():
                    continue
                
                try:
                    fmt["format"]
                    fmt["filesize"]
                    fmt["format_id"]
                    fmt["ext"]
                    fmt["format_note"]
                except Exception:
                    continue
                
                formats_available.append({
                    "format": fmt["format"],
                    "filesize": fmt["filesize"],
                    "format_id": fmt["format_id"],
                    "ext": fmt["ext"],
                    "format_note": fmt["format_note"],
                    "yturl": link,
                })
        
        return formats_available, link
    
    async def slider(self, link: str, query_type: int, videoid: Union[bool, str] = None):
        """Get video info for slider selection."""
        if videoid:
            link = self.base + link
        
        if "&" in link:
            link = link.split("&")[0]
        
        a = VideosSearch(link, limit=10)
        result = (await a.next()).get("result")
        
        title = result[query_type]["title"]
        duration_min = result[query_type]["duration"]
        vidid = result[query_type]["id"]
        thumbnail = result[query_type]["thumbnails"][0]["url"].split("?")[0]
        
        return title, duration_min, thumbnail, vidid
    
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
    ):
        """
        Download video/audio from YouTube.
        
        Flow: DB Cache -> V2 API -> yt-dlp Fallback
        
        Returns:
            Tuple of (file_path, direct) where direct is True if downloaded, False if streaming URL
        """
        if videoid:
            link = self.base + videoid
        
        _ensure_dir(DOWNLOAD_DIR)
        loop = asyncio.get_running_loop()
        cookie_file = cookie_txt_file()
        vid = extract_video_id(link)
        
        # ========================================
        # SPECIAL FORMAT DOWNLOADS (songaudio/songvideo)
        # These use specific format IDs - use yt-dlp directly
        # ========================================
        if songvideo:
            await loop.run_in_executor(None, _ytdlp_song_video_dl, link, format_id, title, cookie_file)
            fpath = f"{DOWNLOAD_DIR}/{title}.mp4"
            return fpath, True
        
        if songaudio:
            await loop.run_in_executor(None, _ytdlp_song_audio_dl, link, format_id, title, cookie_file)
            fpath = f"{DOWNLOAD_DIR}/{title}.mp3"
            return fpath, True
        
        # ========================================
        # NORMAL DOWNLOADS - Try optimized path first
        # ========================================
        is_video = bool(video)
        media_type = "video" if is_video else "audio"
        
        # 1) Try DB Cache + V2 API via media_download
        optimized_path = await media_download(link, media_type, title=title or "", video_id=vid)
        
        if optimized_path and os.path.exists(optimized_path):
            return optimized_path, True
        
        # ========================================
        # 2) FALLBACK: yt-dlp direct download
        # ========================================
        _inc("ytdlp_fallback")
        LOGGER.info(f"⚠️ YT-DLP FALLBACK | {vid or link}")
        
        if video:
            # Check is_on_off for download mode
            if await is_on_off(1):
                # Direct download mode
                downloaded_file = await loop.run_in_executor(None, _ytdlp_video_dl, link, cookie_file)
                return downloaded_file, True
            else:
                # Streaming URL mode
                args = ["yt-dlp", "-g", "-f", "best[height<=?720][width<=?1280]", f"{link}"]
                if cookie_file:
                    args.extend(["--cookies", cookie_file])
                
                proc = await asyncio.create_subprocess_exec(
                    *args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await proc.communicate()
                
                if stdout:
                    downloaded_file = stdout.decode().split("\n")[0]
                    return downloaded_file, False
                else:
                    # If streaming fails, try direct download
                    file_size = await check_file_size(link)
                    if not file_size:
                        return None, False
                    
                    total_size_mb = file_size / (1024 * 1024)
                    if total_size_mb > 250:
                        LOGGER.error(f"File size {total_size_mb:.2f} MB exceeds limit")
                        return None, False
                    
                    downloaded_file = await loop.run_in_executor(None, _ytdlp_video_dl, link, cookie_file)
                    return downloaded_file, True
        
        # Audio download (default)
        downloaded_file = await loop.run_in_executor(None, _ytdlp_audio_dl, link, cookie_file)
        return downloaded_file, True
