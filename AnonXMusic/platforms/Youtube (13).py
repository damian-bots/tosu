import asyncio
import os
import re
import json
import glob
import random
import logging
import uuid
import aiohttp
import aiofiles
from typing import Union, Optional, Any
from urllib.parse import urlparse, unquote
from pathlib import Path

import yt_dlp
from pyrogram.enums import MessageEntityType
from pyrogram.types import Message
from youtubesearchpython.__future__ import VideosSearch

from AnonXMusic.utils.database import is_on_off
from AnonXMusic.utils.formatters import time_to_seconds

import config  # Imported to access API_KEY and API_URL

def is_safe_url(text: str) -> bool:
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
    if not is_url: return True
    try:
        target_url = text.strip()
        if target_url.lower().startswith("www."):
            target_url = "https://" + target_url
        decoded_url = unquote(target_url)
        if any(char in decoded_url for char in DANGEROUS_CHARS):
            logging.warning(f"🚫 Blocked URL (Dangerous Chars): {text}")
            return False
        p = urlparse(target_url)
        if p.netloc.replace("www.", "") not in ALLOWED_DOMAINS:
            logging.warning(f"🚫 Blocked URL (Invalid Domain): {p.netloc}")
            return False
        return True
    except Exception as e:
        logging.error(f"URL Parsing Error: {e}")
        return False

def extract_safe_id(link: str) -> Optional[str]:
    YOUTUBE_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{11}$")
    try:
        if "v=" in link: vid = link.split("v=")[-1].split("&")[0]
        elif "youtu.be" in link: vid = link.split("/")[-1].split("?")[0]
        else: return None
        if YOUTUBE_ID_RE.match(vid): return vid
    except: pass
    return None

_session: Optional[aiohttp.ClientSession] = None
_session_lock = asyncio.Lock()

async def get_http_session() -> aiohttp.ClientSession:
    global _session
    HARD_TIMEOUT = 80
    if _session and not _session.closed:
        return _session
    async with _session_lock:
        if _session and not _session.closed:
            return _session
        timeout = aiohttp.ClientTimeout(total=HARD_TIMEOUT, sock_connect=10, sock_read=30)
        connector = aiohttp.TCPConnector(limit=100, ttl_dns_cache=300, enable_cleanup_closed=True)
        _session = aiohttp.ClientSession(timeout=timeout, connector=connector)
        return _session

def _looks_like_status_text(s: Optional[str]) -> bool:
    if not s: return False
    low = s.lower()
    return any(x in low for x in ("download started", "background", "jobstatus", "job_id", "processing", "queued"))

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
            if v: return _extract_candidate(v)
    return None

def _normalize_url(candidate: str, api_url: str) -> Optional[str]:
    if not api_url or not candidate: return None
    c = candidate.strip()
    if c.startswith(("http://", "https://")): return c
    if c.startswith("/"):
        if c.startswith(("/root", "/home")): return None
        return f"{api_url.rstrip('/')}{c}"
    return f"{api_url.rstrip('/')}/{c.lstrip('/')}"

async def _download_cdn(url: str, out_path: str) -> bool:
    CHUNK_SIZE = 1024 * 1024
    CDN_RETRIES = 5
    CDN_RETRY_DELAY = 2
    HARD_TIMEOUT = 80
    
    logging.info(f"🔗 Downloading from CDN: {url}")
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(1, CDN_RETRIES + 1):
        try:
            session = await get_http_session()
            async with session.get(url, timeout=HARD_TIMEOUT) as resp:
                if resp.status != 200:
                    if attempt < CDN_RETRIES:
                        await asyncio.sleep(CDN_RETRY_DELAY)
                        continue
                    return False
                async with aiofiles.open(out_path, "wb") as f:
                    async for chunk in resp.content.iter_chunked(CHUNK_SIZE):
                        if not chunk: break
                        await f.write(chunk)
            if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
                return True
        except asyncio.TimeoutError:
            if attempt < CDN_RETRIES: await asyncio.sleep(CDN_RETRY_DELAY)
        except Exception as e:
            logging.error(f"CDN Fail: {e}")
            if attempt < CDN_RETRIES: await asyncio.sleep(CDN_RETRY_DELAY)
    return False

async def v2_download_process(link: str, video: bool, file_id: str, api_key: str, api_url: str) -> Optional[str]:
    V2_DOWNLOAD_CYCLES = 4
    NO_CANDIDATE_WAIT = 4
    JOB_POLL_ATTEMPTS = 10     
    JOB_POLL_INTERVAL = 2.0    
    JOB_POLL_BACKOFF = 1.2
    
    vid = extract_safe_id(link) or link 
    ext = "mp4" if video else "mp3"
    out_path = Path("downloads") / f"{file_id}.{ext}"

    if out_path.exists() and out_path.stat().st_size > 0:
        return str(out_path)

    for cycle in range(1, V2_DOWNLOAD_CYCLES + 1):
        try:
            session = await get_http_session()
            url = f"{api_url.rstrip('/')}/youtube/v2/download"
            params = {"query": vid, "isVideo": str(video).lower(), "api_key": api_key}
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    if cycle < V2_DOWNLOAD_CYCLES: await asyncio.sleep(1); continue
                    return None
                data = await resp.json()

            candidate = _extract_candidate(data)
            if candidate and _looks_like_status_text(candidate):
                candidate = None

            job_id = data.get("job_id")
            if isinstance(data.get("job"), dict):
                 job_id = data.get("job").get("id")

            if job_id and not candidate:
                interval = JOB_POLL_INTERVAL
                for _ in range(JOB_POLL_ATTEMPTS):
                    await asyncio.sleep(interval)
                    status_url = f"{api_url.rstrip('/')}/youtube/jobStatus"
                    try:
                        async with session.get(status_url, params={"job_id": job_id}) as s_resp:
                            if s_resp.status == 200:
                                s_data = await s_resp.json()
                                candidate = _extract_candidate(s_data)
                                if candidate and not _looks_like_status_text(candidate):
                                    break
                    except Exception:
                        pass
                    interval *= JOB_POLL_BACKOFF
            
            if not candidate:
                if cycle < V2_DOWNLOAD_CYCLES: await asyncio.sleep(NO_CANDIDATE_WAIT); continue
                return None

            final_url = _normalize_url(candidate, api_url)
            if not final_url:
                 if cycle < V2_DOWNLOAD_CYCLES: await asyncio.sleep(NO_CANDIDATE_WAIT); continue
                 return None

            if await _download_cdn(final_url, str(out_path)):
                return str(out_path)
        except Exception as e:
            logging.error(f"API Cycle Error: {e}")
            if cycle < V2_DOWNLOAD_CYCLES: await asyncio.sleep(1)
    return None

# ==========================================
# === ORIGINAL ANONX HELPERS ===============
# ==========================================

def cookie_txt_file():
    folder_path = f"{os.getcwd()}/cookies"
    filename = f"{os.getcwd()}/cookies/logs.csv"
    txt_files = glob.glob(os.path.join(folder_path, '*.txt'))
    if not txt_files:
        raise FileNotFoundError("No .txt files found in the specified folder.")
    cookie_txt_file = random.choice(txt_files)
    with open(filename, 'a') as file:
        file.write(f'Choosen File : {cookie_txt_file}\n')
    return f"""cookies/{str(cookie_txt_file).split("/")[-1]}"""

async def check_file_size(link):
    async def get_format_info(link):
        proc = await asyncio.create_subprocess_exec(
            "yt-dlp",
            "--cookies", cookie_txt_file(),
            "-J",
            link,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            print(f'Error:\n{stderr.decode()}')
            return None
        return json.loads(stdout.decode())

    def parse_size(formats):
        total_size = 0
        for format in formats:
            if 'filesize' in format:
                total_size += format['filesize']
        return total_size

    info = await get_format_info(link)
    if info is None:
        return None
    
    formats = info.get('formats', [])
    if not formats:
        print("No formats found.")
        return None
    
    total_size = parse_size(formats)
    return total_size

async def shell_cmd(cmd):
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, errorz = await proc.communicate()
    if errorz:
        if "unavailable videos are hidden" in (errorz.decode("utf-8")).lower():
            return out.decode("utf-8")
        else:
            return errorz.decode("utf-8")
    return out.decode("utf-8")


class YouTubeAPI:
    def __init__(self):
        self.base = "https://www.youtube.com/watch?v="
        self.regex = r"(?:youtube\.com|youtu\.be)"
        self.status = "https://www.youtube.com/oembed?url="
        self.listbase = "https://youtube.com/playlist?list="
        self.reg = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")

    async def exists(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        if not is_safe_url(link): return False
        if re.search(self.regex, link):
            return True
        else:
            return False

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
        if offset in (None,):
            return None
        return text[offset : offset + length]

    async def details(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        if not is_safe_url(link): return None
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

    async def title(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        if not is_safe_url(link): return None
        if "&" in link:
            link = link.split("&")[0]
        results = VideosSearch(link, limit=1)
        for result in (await results.next())["result"]:
            title = result["title"]
        return title

    async def duration(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        if not is_safe_url(link): return None
        if "&" in link:
            link = link.split("&")[0]
        results = VideosSearch(link, limit=1)
        for result in (await results.next())["result"]:
            duration = result["duration"]
        return duration

    async def thumbnail(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        if not is_safe_url(link): return None
        if "&" in link:
            link = link.split("&")[0]
        results = VideosSearch(link, limit=1)
        for result in (await results.next())["result"]:
            thumbnail = result["thumbnails"][0]["url"].split("?")[0]
        return thumbnail

    async def video(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        if not is_safe_url(link): return 0, "Unsafe URL"
        if "&" in link:
            link = link.split("&")[0]
        proc = await asyncio.create_subprocess_exec(
            "yt-dlp",
            "--cookies",cookie_txt_file(),
            "-g",
            "-f",
            "best[height<=?720][width<=?1280]",
            f"{link}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if stdout:
            return 1, stdout.decode().split("\n")[0]
        else:
            return 0, stderr.decode()

    async def playlist(self, link, limit, user_id, videoid: Union[bool, str] = None):
        if videoid:
            link = self.listbase + link
        if not is_safe_url(link): return []
        if "&" in link:
            link = link.split("&")[0]
        playlist = await shell_cmd(
            f"yt-dlp -i --get-id --flat-playlist --cookies {cookie_txt_file()} --playlist-end {limit} --skip-download {link}"
        )
        try:
            result = playlist.split("\n")
            for key in result:
                if key == "":
                    result.remove(key)
        except:
            result = []
        return result

    async def track(self, link: str, videoid: Union[bool, str] = None):
        if videoid:
            link = self.base + link
        if not is_safe_url(link): return None, None
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
        if videoid:
            link = self.base + link
        if not is_safe_url(link): return [], link
        if "&" in link:
            link = link.split("&")[0]
        ytdl_opts = {"quiet": True, "cookiefile" : cookie_txt_file()}
        ydl = yt_dlp.YoutubeDL(ytdl_opts)
        with ydl:
            formats_available = []
            r = ydl.extract_info(link, download=False)
            for format in r["formats"]:
                try:
                    str(format["format"])
                except:
                    continue
                if not "dash" in str(format["format"]).lower():
                    try:
                        format["format"]
                        format["filesize"]
                        format["format_id"]
                        format["ext"]
                        format["format_note"]
                    except:
                        continue
                    formats_available.append(
                        {
                            "format": format["format"],
                            "filesize": format["filesize"],
                            "format_id": format["format_id"],
                            "ext": format["ext"],
                            "format_note": format["format_note"],
                            "yturl": link,
                        }
                    )
        return formats_available, link

    async def slider(
        self,
        link: str,
        query_type: int,
        videoid: Union[bool, str] = None,
    ):
        if videoid:
            link = self.base + link
        if not is_safe_url(link): return None, None, None, None
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
    ) -> Union[str, tuple]:
        if videoid:
            link = self.base + link
            
        if not is_safe_url(link):
            logging.warning(f"Blocked unsafe link from download: {link}")
            if songvideo or songaudio:
                return None
            return None, False

        loop = asyncio.get_running_loop()

        # ==========================================
        # === API FETCH INTEGRATION ================
        # ==========================================
        api_key = config.API_KEY
        api_url = config.API_URL
        
        if api_key and api_url:
            try:
                is_vid = True if (video or songvideo) else False
                file_id = title if title else uuid.uuid4().hex[:10]
                api_path = await v2_download_process(link, is_vid, file_id, api_key, api_url)
                
                if api_path:
                    if songvideo or songaudio:
                        return api_path # Return strictly string for /song commands
                    else:
                        return api_path, True # Return tuple for standard stream requests
            except Exception as e:
                logging.error(f"API Download failed, falling back to local yt-dlp: {e}")
        # ==========================================

        def audio_dl():
            ydl_optssx = {
                "format": "bestaudio/best",
                "outtmpl": "downloads/%(id)s.%(ext)s",
                "geo_bypass": True,
                "nocheckcertificate": True,
                "quiet": True,
                "cookiefile" : cookie_txt_file(),
                "no_warnings": True,
            }
            x = yt_dlp.YoutubeDL(ydl_optssx)
            info = x.extract_info(link, False)
            xyz = os.path.join("downloads", f"{info['id']}.{info['ext']}")
            if os.path.exists(xyz):
                return xyz
            x.download([link])
            return xyz

        def video_dl():
            ydl_optssx = {
                "format": "(bestvideo[height<=?720][width<=?1280][ext=mp4])+(bestaudio[ext=m4a])",
                "outtmpl": "downloads/%(id)s.%(ext)s",
                "geo_bypass": True,
                "nocheckcertificate": True,
                "quiet": True,
                "cookiefile" : cookie_txt_file(),
                "no_warnings": True,
            }
            x = yt_dlp.YoutubeDL(ydl_optssx)
            info = x.extract_info(link, False)
            xyz = os.path.join("downloads", f"{info['id']}.{info['ext']}")
            if os.path.exists(xyz):
                return xyz
            x.download([link])
            return xyz

        def song_video_dl():
            formats = f"{format_id}+140"
            fpath = f"downloads/{title}"
            ydl_optssx = {
                "format": formats,
                "outtmpl": fpath,
                "geo_bypass": True,
                "nocheckcertificate": True,
                "quiet": True,
                "no_warnings": True,
                "cookiefile" : cookie_txt_file(),
                "prefer_ffmpeg": True,
                "merge_output_format": "mp4",
            }
            x = yt_dlp.YoutubeDL(ydl_optssx)
            x.download([link])

        def song_audio_dl():
            fpath = f"downloads/{title}.%(ext)s"
            ydl_optssx = {
                "format": format_id,
                "outtmpl": fpath,
                "geo_bypass": True,
                "nocheckcertificate": True,
                "quiet": True,
                "no_warnings": True,
                "cookiefile" : cookie_txt_file(),
                "prefer_ffmpeg": True,
                "postprocessors": [
                    {
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    }
                ],
            }
            x = yt_dlp.YoutubeDL(ydl_optssx)
            x.download([link])

        if songvideo:
            await loop.run_in_executor(None, song_video_dl)
            fpath = f"downloads/{title}.mp4"
            return fpath
        elif songaudio:
            await loop.run_in_executor(None, song_audio_dl)
            fpath = f"downloads/{title}.mp3"
            return fpath
        elif video:
            if await is_on_off(1):
                direct = True
                downloaded_file = await loop.run_in_executor(None, video_dl)
            else:
                proc = await asyncio.create_subprocess_exec(
                    "yt-dlp",
                    "--cookies",cookie_txt_file(),
                    "-g",
                    "-f",
                    "best[height<=?720][width<=?1280]",
                    f"{link}",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await proc.communicate()
                if stdout:
                    downloaded_file = stdout.decode().split("\n")[0]
                    direct = False
                else:
                   file_size = await check_file_size(link)
                   if not file_size:
                     print("None file Size")
                     return
                   total_size_mb = file_size / (1024 * 1024)
                   if total_size_mb > 250:
                     print(f"File size {total_size_mb:.2f} MB exceeds the 100MB limit.")
                     return None
                   direct = True
                   downloaded_file = await loop.run_in_executor(None, video_dl)
        else:
            direct = True
            downloaded_file = await loop.run_in_executor(None, audio_dl)
            
        return downloaded_file, direct
