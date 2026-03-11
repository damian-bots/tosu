# AnonXMusic/utils/Youtube.py
# Updated: Secured against Remote Code Execution (RCE) / Command Injection

import time
import asyncio
import os
import re
import json
import glob
import random
import logging
import shlex  # <--- Added for shell security
from typing import Union, Dict, Optional, Any
from pathlib import Path
from urllib.parse import urlparse, unquote  # <--- Added unquote for security

import aiofiles
import aiohttp
from aiohttp import TCPConnector
from motor.motor_asyncio import AsyncIOMotorClient
import yt_dlp
from pyrogram.enums import MessageEntityType
from pyrogram.types import Message
from youtubesearchpython.__future__ import VideosSearch

from AnonXMusic.utils.database import is_on_off
from AnonXMusic.utils.formatters import time_to_seconds

import config
from AnonXMusic import app as TG_APP

# ============================
# DB NAME & COLLECTION
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
    "total": 0, "success": 0, "failed": 0, "success_audio": 0, "success_video": 0,
    "failed_audio": 0, "failed_video": 0, "hard_fail_401": 0, "hard_fail_403": 0,
    "api_fail_other_4xx": 0, "api_fail_5xx": 0, "network_fail": 0, "timeout_fail": 0,
    "no_candidate": 0, "tg_fail": 0, "tg_flood_skip": 0, "cdn_fail": 0,
    "hard_cycle_retries": 0, "media_db_hit": 0, "media_db_miss": 0, "media_db_fail": 0,
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

# ============================
# COOKIE FILE HANDLER
# ============================
def cookie_txt_file() -> str:
    folder_path = os.path.join(os.getcwd(), "cookies")
    filename = os.path.join(os.getcwd(), "cookies", "logs.csv")
    txt_files = glob.glob(os.path.join(folder_path, '*.txt'))
    
    if not txt_files:
        return "cookies/cookies.txt" 
    
    cookie_file = random.choice(txt_files)
    try:
        with open(filename, 'a') as file:
            file.write(f'Choosen File : {cookie_file}\n')
    except Exception:
        pass
    return f"cookies/{os.path.basename(cookie_file)}"

# ============================
# HTTP SESSION MANAGER
# ============================
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

# ============================
# MONGODB CONNECTION
# ============================
def _get_media_collection():
    global _MONGO_CLIENT
    db_uri = getattr(config, "MONGO_DB_URI", getattr(config, "DB_URI", None))
    if not db_uri:
        return None
    
    if _MONGO_CLIENT is None:
        _MONGO_CLIENT = AsyncIOMotorClient(db_uri)
    
    db = _MONGO_CLIENT[MEDIA_DB_NAME]
    return db[MEDIA_COLLECTION_NAME]

async def is_media(track_id: str, isVideo: bool = False) -> bool:
    col = _get_media_collection()
    if col is None: return False
    try:
        doc = await col.find_one({"track_id": track_id, "isVideo": isVideo}, {"_id": 1})
        return bool(doc)
    except Exception:
        return False

async def get_media_id(track_id: str, isVideo: bool = False) -> Optional[int]:
    col = _get_media_collection()
    if col is None: return None
    try:
        doc = await col.find_one({"track_id": track_id, "isVideo": isVideo}, {"message_id": 1})
        if doc and doc.get("message_id"):
            return int(doc.get("message_id"))
    except Exception:
        pass
    return None

# ============================
# UTILITY FUNCTIONS
# ============================
def _ensure_dir(p: str) -> None:
    os.makedirs(p, exist_ok=True)

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
# URL SECURITY FUNCTIONS
# ============================
def is_safe_url(text: str) -> bool:
    """
    STRICT URL VALIDATION
    Blocks all shell injection vectors and unsafe domains.
    """
    DANGEROUS_CHARS = [
        ";", "|", "$", "`", "\n", "\r", 
        "&", "(", ")", "<", ">", "{", "}", 
        "\\", "'", '"'
    ]
    ALLOWED_DOMAINS = {
        "youtube.com", "www.youtube.com", "m.youtube.com", 
        "youtu.be", "music.youtube.com"
    }
    
    if not text: return False
    is_url = text.strip().lower().startswith(("http:", "https:", "www."))
    
    # Allow normal text queries to pass to ytsearch
    if not is_url: return True
    
    try:
        target_url = text.strip()
        if target_url.lower().startswith("www."):
            target_url = "https://" + target_url
            
        # Decode the URL first to catch hidden payloads like %7B ( { )
        decoded_url = unquote(target_url)
        
        # Check for dangerous shell characters
        if any(char in decoded_url for char in DANGEROUS_CHARS):
            LOGGER.warning(f"🚫 Blocked URL (Dangerous Chars Detected): {text}")
            return False
            
        # Validate Domain
        parsed = urlparse(target_url)
        domain = parsed.netloc.replace("www.", "")
        
        if domain not in ALLOWED_DOMAINS:
            LOGGER.warning(f"🚫 Blocked URL (Invalid Domain): {domain}")
            return False
            
        return True
    except Exception as e:
        LOGGER.error(f"URL Validation Error: {e}")
        return False

YOUTUBE_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{11}$")
YOUTUBE_ID_IN_URL_RE = re.compile(r"""(?x)(?:v=|\/)([A-Za-z0-9_-]{11})|youtu\.be\/([A-Za-z0-9_-]{11})""")

def extract_video_id(link: str) -> str:
    if not link: return ""
    s = link.strip()
    if YOUTUBE_ID_RE.match(s): return s
    m = YOUTUBE_ID_IN_URL_RE.search(s)
    if m:
        extracted = m.group(1) or m.group(2) or ""
        if YOUTUBE_ID_RE.match(extracted): return extracted
    candidate = ""
    if "v=" in s:
        candidate = s.split("v=")[-1].split("&")[0]
    else:
        candidate = s.split("/")[-1].split("?")[0]
    if YOUTUBE_ID_RE.match(candidate):
        return candidate
    return ""

# ============================
# CDN DOWNLOAD
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

# ============================
# MEDIA DB DOWNLOAD
# ============================
async def _download_from_media_db(track_id: str, is_video: bool) -> Optional[str]:
    global TG_FLOOD_COOLDOWN
    db_uri = getattr(config, "MONGO_DB_URI", getattr(config, "DB_URI", None))
    ch_id_str = getattr(config, "MEDIA_CHANNEL_ID", None)
    
    if not track_id or TG_APP is None or not db_uri or not ch_id_str:
        return None
    
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
    
    if os.path.exists(final_path) and os.path.getsize(final_path) > 0:
        return final_path
    
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
            try:
                if tmp_path and os.path.exists(tmp_path): os.remove(tmp_path)
            except: pass
            return None
        try:
            if fixed != final_path: os.replace(fixed, final_path)
        except Exception:
            final_path = fixed
        
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
    if obj is None: return None
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
                if resp.status in (401, 403): return None
                return None
        except Exception:
            pass
        if attempt < V2_HTTP_RETRIES: await asyncio.sleep(1)
    return None

async def v2_download(link: str, media_type: str) -> Optional[str]:
    is_video = (media_type == "video")
    vid = extract_video_id(link)
    query = vid or link
    api_url = getattr(config, "API_URL", None)
    
    for cycle in range(1, V2_DOWNLOAD_CYCLES + 1):
        resp = await _v2_request_json("youtube/v2/download", {"query": query, "isVideo": str(is_video).lower()})
        if not resp:
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
            if cycle < V2_DOWNLOAD_CYCLES: await asyncio.sleep(NO_CANDIDATE_WAIT); continue
            return None
        
        normalized = _normalize_candidate_to_url(candidate, api_url)
        if not normalized:
            if cycle < V2_DOWNLOAD_CYCLES: await asyncio.sleep(NO_CANDIDATE_WAIT); continue
            return None
        
        ext = _guess_ext_from_url(normalized, is_video=is_video)
        base_name = vid if vid else "audio"
        out_path = os.path.join(DOWNLOAD_DIR, f"{base_name}.{ext}")
        
        if os.path.exists(out_path): return out_path
        path = await _download_from_cdn(normalized, out_path)
        if not path:
            if cycle < V2_DOWNLOAD_CYCLES: await asyncio.sleep(2); continue
        return path
    return None

# ============================
# OPTIMIZED DOWNLOAD (DB + API)
# ============================
async def optimized_download(link: str, is_video: bool) -> Optional[str]:
    vid = extract_video_id(link)
    
    # 1) Try DB cache
    if vid:
        db_path = await _download_from_media_db(vid, is_video=is_video)
        if db_path and os.path.exists(db_path):
            _inc("success")
            LOGGER.info(f"✅ DB-CACHE | {vid}")
            return db_path
    
    # 2) Try V2 API
    api_url = getattr(config, "API_URL", None)
    api_key = getattr(config, "API_KEY", None)
    if api_url and api_key:
        v2_path = await v2_download(link, media_type=("video" if is_video else "audio"))
        if v2_path and os.path.exists(v2_path):
            _inc("success")
            LOGGER.info(f"✅ V2-API | {vid or link}")
            return v2_path
    
    return None

# ============================
# CHECK FILE SIZE
# ============================
async def check_file_size(link):
    async def get_format_info(link):
        proc = await asyncio.create_subprocess_exec(
            "yt-dlp", "-J", link,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0: return None
        return json.loads(stdout.decode())

    def parse_size(formats):
        total_size = 0
        for format in formats:
            if 'filesize' in format: total_size += format['filesize']
        return total_size

    info = await get_format_info(link)
    if info is None: return None
    formats = info.get('formats', [])
    if not formats: return None
    return parse_size(formats)

async def shell_cmd(cmd):
    proc = await asyncio.create_subprocess_shell(
        cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    out, errorz = await proc.communicate()
    if errorz:
        if "unavailable videos are hidden" in (errorz.decode("utf-8")).lower(): return out.decode("utf-8")
        else: return errorz.decode("utf-8")
    return out.decode("utf-8")

# ============================
# YOUTUBE API CLASS
# ============================
class YouTubeAPI:
    def __init__(self):
        self.base = "https://www.youtube.com/watch?v="
        self.regex = r"(?:youtube\.com|youtu\.be)"
        self.listbase = "https://youtube.com/playlist?list="

    async def exists(self, link: str, videoid: Union[bool, str] = None):
        if videoid: link = self.base + link
        if re.search(self.regex, link): return True
        return False

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
        if videoid: link = self.base + link
        if "&" in link: link = link.split("&")[0]
        results = VideosSearch(link, limit=1)
        for result in (await results.next())["result"]:
            title = result["title"]
            duration_min = result["duration"]
            thumbnail = result["thumbnails"][0]["url"].split("?")[0]
            vidid = result["id"]
            if str(duration_min) == "None": duration_sec = 0
            else: duration_sec = int(time_to_seconds(duration_min))
        return title, duration_min, duration_sec, thumbnail, vidid

    async def title(self, link: str, videoid: Union[bool, str] = None) -> str:
        if videoid: link = self.base + link
        if "&" in link: link = link.split("&")[0]
        results = VideosSearch(link, limit=1)
        for result in (await results.next())["result"]: return result["title"]
        return ""

    async def duration(self, link: str, videoid: Union[bool, str] = None) -> str:
        if videoid: link = self.base + link
        if "&" in link: link = link.split("&")[0]
        results = VideosSearch(link, limit=1)
        for result in (await results.next())["result"]: return result["duration"]
        return ""

    async def thumbnail(self, link: str, videoid: Union[bool, str] = None) -> str:
        if videoid: link = self.base + link
        if "&" in link: link = link.split("&")[0]
        results = VideosSearch(link, limit=1)
        for result in (await results.next())["result"]:
            return result["thumbnails"][0]["url"].split("?")[0]
        return ""

    async def video(self, link: str, videoid: Union[bool, str] = None):
        if videoid: link = self.base + link
        if "&" in link: link = link.split("&")[0]
        proc = await asyncio.create_subprocess_exec(
            "yt-dlp", "--cookies", cookie_txt_file(), "-g", "-f", "best[height<=?720][width<=?1280]", f"{link}",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if stdout: return 1, stdout.decode().split("\n")[0]
        else: return 0, stderr.decode()

    async def playlist(self, link, limit, user_id, videoid: Union[bool, str] = None):
        if videoid: link = self.listbase + link
        if "&" in link: link = link.split("&")[0]
        
        # SECURE THE COMMAND BY QUOTING ARGUMENTS
        safe_link = shlex.quote(link)
        safe_cookie = shlex.quote(cookie_txt_file())
        
        playlist = await shell_cmd(
            f"yt-dlp -i --get-id --flat-playlist --cookies {safe_cookie} --playlist-end {limit} --skip-download {safe_link}"
        )
        try:
            result = playlist.split("\n")
            for key in result[:]:
                if key == "": result.remove(key)
        except Exception:
            result = []
        return result

    async def track(self, link: str, videoid: Union[bool, str] = None):
        if videoid: link = self.base + link
        if "&" in link: link = link.split("&")[0]
        results = VideosSearch(link, limit=1)
        for result in (await results.next())["result"]:
            title = result["title"]
            duration_min = result["duration"]
            vidid = result["id"]
            yturl = result["link"]
            thumbnail = result["thumbnails"][0]["url"].split("?")[0]
        track_details = {"title": title, "link": yturl, "vidid": vidid, "duration_min": duration_min, "thumb": thumbnail}
        return track_details, vidid

    async def formats(self, link: str, videoid: Union[bool, str] = None):
        if videoid: link = self.base + link
        if "&" in link: link = link.split("&")[0]
        ytdl_opts = {"quiet": True, "cookiefile": cookie_txt_file()}
        ydl = yt_dlp.YoutubeDL(ytdl_opts)
        with ydl:
            formats_available = []
            r = ydl.extract_info(link, download=False)
            for format in r["formats"]:
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
        if videoid: link = self.base + link
        if "&" in link: link = link.split("&")[0]
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
    ) -> Union[str, tuple]:
        """
        Download video/audio from YouTube.
        Flow: DB Cache -> V2 API -> yt-dlp Fallback
        """
        if videoid:
            link = self.base + link
        
        if not is_safe_url(link):
            if songvideo or songaudio: return None
            return None, False

        if "&" in link:
            link = link.split("&")[0]
            
        loop = asyncio.get_running_loop()

        # ============================================
        # 1. OPTIMIZED DOWNLOAD (API & DB CACHE)
        # ============================================
        is_video = bool(video or songvideo)
        
        try:
            optimized_path = await optimized_download(link, is_video)
            if optimized_path and os.path.exists(optimized_path):
                if songvideo or songaudio:
                    return optimized_path
                return optimized_path, True
        except Exception as e:
            LOGGER.error(f"Optimized Download Failed: {e}")

        # ============================================
        # 2. FALLBACK TO YT-DLP 
        # ============================================
        _inc("ytdlp_fallback")
        LOGGER.info(f"⚠️ YT-DLP FALLBACK | {link}")
        
        def song_video_dl():
            formats = f"{format_id}+140"
            fpath = f"downloads/{title}"
            ydl_optssx = {
                "format": formats, "outtmpl": fpath, "geo_bypass": True,
                "nocheckcertificate": True, "quiet": True, "no_warnings": True,
                "cookiefile": cookie_txt_file(), "prefer_ffmpeg": True,
                "merge_output_format": "mp4",
            }
            x = yt_dlp.YoutubeDL(ydl_optssx)
            x.download([link])

        def song_audio_dl():
            fpath = f"downloads/{title}.%(ext)s"
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
                "format": "bestaudio/best", "outtmpl": "downloads/%(id)s.%(ext)s", "geo_bypass": True,
                "nocheckcertificate": True, "quiet": True, "cookiefile": cookie_txt_file(), "no_warnings": True,
            }
            x = yt_dlp.YoutubeDL(ydl_optssx)
            info = x.extract_info(link, False)
            xyz = os.path.join("downloads", f"{info['id']}.{info['ext']}")
            if os.path.exists(xyz): return xyz
            x.download([link])
            return xyz

        def video_dl():
            ydl_optssx = {
                "format": "(bestvideo[height<=?720][width<=?1280][ext=mp4])+(bestaudio[ext=m4a])",
                "outtmpl": "downloads/%(id)s.%(ext)s", "geo_bypass": True, "nocheckcertificate": True,
                "quiet": True, "cookiefile": cookie_txt_file(), "no_warnings": True,
            }
            x = yt_dlp.YoutubeDL(ydl_optssx)
            info = x.extract_info(link, False)
            xyz = os.path.join("downloads", f"{info['id']}.{info['ext']}")
            if os.path.exists(xyz): return xyz
            x.download([link])
            return xyz
        
        if songvideo:
            await loop.run_in_executor(None, song_video_dl)
            return f"downloads/{title}.mp4"
        
        if songaudio:
            await loop.run_in_executor(None, song_audio_dl)
            return f"downloads/{title}.mp3"
            
        if video:
            if await is_on_off(1):
                direct = True
                downloaded_file = await loop.run_in_executor(None, video_dl)
            else:
                proc = await asyncio.create_subprocess_exec(
                    "yt-dlp", "--cookies", cookie_txt_file(), "-g", "-f", "best[height<=?720][width<=?1280]", f"{link}",
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await proc.communicate()
                if stdout:
                    downloaded_file = stdout.decode().split("\n")[0]
                    direct = False
                else:
                    file_size = await check_file_size(link)
                    if not file_size: return None, False
                    total_size_mb = file_size / (1024 * 1024)
                    if total_size_mb > 250:
                        LOGGER.error(f"File size {total_size_mb:.2f} MB exceeds limit")
                        return None, False
                    direct = True
                    downloaded_file = await loop.run_in_executor(None, video_dl)
        else:
            direct = True
            downloaded_file = await loop.run_in_executor(None, audio_dl)
        
        return downloaded_file, direct
