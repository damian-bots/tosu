# AnonXMusic · core/call.py  (v1.0.4)
# Spotify3-style "send hold text → edit to Now Playing card" for every track.
# Full error logging on every exception.  No "Started Streaming" text ever
# appears as a raw edit of an existing mystic message; every track transition
# follows the same pattern:
#   1. Send plain "Hold on…" text
#   2. Edit it to the photo card  (or send_photo fallback)

import asyncio
import os
import traceback
from datetime import datetime, timedelta
from typing import Union

from pyrogram import Client
from pyrogram.types import InlineKeyboardMarkup, InputMediaPhoto
from pytgcalls import PyTgCalls, StreamType
from pytgcalls.exceptions import AlreadyJoinedError, NoActiveGroupCall, TelegramServerError
from pytgcalls.types import Update
from pytgcalls.types.input_stream import AudioPiped, AudioVideoPiped
from pytgcalls.types.input_stream.quality import HighQualityAudio, MediumQualityVideo
from pytgcalls.types.stream import StreamAudioEnded
from pyrogram.errors import FloodWait

import config
from AnonXMusic import LOGGER, YouTube, app
from AnonXMusic.utils.error_logger import error_logger

_LOG = LOGGER(__name__)

_RETRYABLE_TG_ERRORS = [
    "SERVER_TIMEOUT", "CONNECTION_DEVICE_MODEL_INVALID",
    "AUTH_KEY_UNREGISTERED", "NETWORK_MIGRATE",
]


def _is_frozen(exc: Exception) -> bool:
    msg = str(exc).upper()
    return "FROZEN_METHOD_INVALID" in msg or "METHOD_INVALID" in msg


def _is_retryable_call_error(exc: Exception) -> bool:
    msg = str(exc)
    return any(tag in msg for tag in _RETRYABLE_TG_ERRORS)


async def _notify_owner(text: str) -> None:
    try:
        owner_id = int(config.OWNER_ID)
        await app.send_message(owner_id, text, disable_web_page_preview=True)
    except Exception as e:
        _LOG.warning(f"[notify_owner] failed: {e}")


# ── Safe TG call with retry ───────────────────────────────────────────────────

async def _safe_tg(coro_fn, *args, retries: int = 4, base_delay: float = 3.0, **kwargs):
    for attempt in range(1, retries + 1):
        try:
            return await coro_fn(*args, **kwargs)
        except (OSError, ConnectionResetError, ConnectionError) as e:
            if attempt < retries:
                wait = base_delay * attempt
                _LOG.warning(f"[TG] Connection error attempt {attempt}/{retries}: {e}. Retry in {wait}s")
                await asyncio.sleep(wait)
            else:
                _LOG.error(f"[TG] Gave up after {retries} retries: {e}")
                raise
        except Exception:
            raise


# ── Spotify3-style: send hold → edit to photo card ────────────────────────────

async def _send_now_playing(
    chat_id: int,
    photo: str,
    caption: str,
    button,
    *,
    hold_text: str = "⏳ Loading next track...",
    hold_msg=None,            # pass an already-sent hold message to edit
) -> object:
    """
    Spotify3-style Now Playing card:
      1. If hold_msg supplied → edit it to the photo card.
      2. Otherwise send "Hold on…" text then edit to photo card.
      3. Fall back to send_photo if edit fails.
    Returns the final Message object.
    """
    mystic = hold_msg
    if mystic is None:
        try:
            mystic = await _safe_tg(app.send_message, chat_id, hold_text)
        except Exception:
            mystic = None

    if mystic is not None:
        try:
            run = await mystic.edit_message_media(
                media=InputMediaPhoto(media=photo, caption=caption),
                reply_markup=InlineKeyboardMarkup(button),
            )
            return run
        except Exception:
            try:
                await mystic.delete()
            except Exception:
                pass

    return await _safe_tg(
        app.send_photo,
        chat_id=chat_id,
        photo=photo,
        caption=caption,
        reply_markup=InlineKeyboardMarkup(button),
    )


from AnonXMusic.misc import db
from AnonXMusic.utils.database import (
    add_active_chat,
    add_active_video_chat,
    get_assistant_number,
    get_lang,
    get_loop,
    group_assistant,
    is_autoend,
    music_on,
    remove_active_chat,
    remove_active_video_chat,
    set_loop,
)
from AnonXMusic.utils.exceptions import AssistantErr
from AnonXMusic.utils.formatters import check_duration, seconds_to_min, speed_converter
from AnonXMusic.utils.inline.play import stream_markup
from AnonXMusic.utils.stream.autoclear import auto_clean
from AnonXMusic.utils.thumbnails import get_thumb
from strings import get_string

autoend = {}
counter = {}


async def _clear_(chat_id):
    db[chat_id] = []
    await remove_active_video_chat(chat_id)
    await remove_active_chat(chat_id)


class Call(PyTgCalls):
    def __init__(self):
        def _make_client(name, string_attr):
            return Client(
                name=name,
                api_id=config.API_ID,
                api_hash=config.API_HASH,
                session_string=str(getattr(config, string_attr)),
            )

        self.userbot1 = _make_client("AnonXAss1", "STRING1")
        self.one   = PyTgCalls(self.userbot1,   cache_duration=100)
        self.userbot2 = _make_client("AnonXAss2", "STRING2")
        self.two   = PyTgCalls(self.userbot2,   cache_duration=100)
        self.userbot3 = _make_client("AnonXAss3", "STRING3")
        self.three = PyTgCalls(self.userbot3,   cache_duration=100)
        self.userbot4 = _make_client("AnonXAss4", "STRING4")
        self.four  = PyTgCalls(self.userbot4,   cache_duration=100)
        self.userbot5 = _make_client("AnonXAss5", "STRING5")
        self.five  = PyTgCalls(self.userbot5,   cache_duration=100)

    # ── Basic controls ────────────────────────────────────────────────────────

    async def pause_stream(self, chat_id: int):
        assistant = await group_assistant(self, chat_id)
        await assistant.pause_stream(chat_id)

    async def resume_stream(self, chat_id: int):
        assistant = await group_assistant(self, chat_id)
        await assistant.resume_stream(chat_id)

    async def stop_stream(self, chat_id: int):
        assistant = await group_assistant(self, chat_id)
        try:
            await _clear_(chat_id)
            await assistant.leave_group_call(chat_id)
        except Exception:
            pass

    async def stop_stream_force(self, chat_id: int):
        for attr, string_attr in [
            ("one", "STRING1"), ("two", "STRING2"), ("three", "STRING3"),
            ("four", "STRING4"), ("five", "STRING5"),
        ]:
            try:
                if getattr(config, string_attr):
                    await getattr(self, attr).leave_group_call(chat_id)
            except Exception:
                pass
        try:
            await _clear_(chat_id)
        except Exception:
            pass

    async def speedup_stream(self, chat_id: int, file_path, speed, playing):
        assistant = await group_assistant(self, chat_id)
        if str(speed) != "1.0":
            base = os.path.basename(file_path)
            chatdir = os.path.join(os.getcwd(), "playback", str(speed))
            os.makedirs(chatdir, exist_ok=True)
            out = os.path.join(chatdir, base)
            if not os.path.isfile(out):
                vs_map = {"0.5": 2.0, "0.75": 1.35, "1.5": 0.68, "2.0": 0.5}
                vs = vs_map.get(str(speed), 1.0)
                proc = await asyncio.create_subprocess_shell(
                    cmd=(
                        f"ffmpeg -i {file_path} "
                        f"-filter:v setpts={vs}*PTS "
                        f"-filter:a atempo={speed} {out}"
                    ),
                    stdin=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await proc.communicate()
        else:
            out = file_path

        dur = int(await asyncio.get_event_loop().run_in_executor(None, check_duration, out))
        played, con_seconds = speed_converter(playing[0]["played"], speed)
        duration = seconds_to_min(dur)
        stream_type = playing[0]["streamtype"]
        stream = (
            AudioVideoPiped(out, HighQualityAudio(), MediumQualityVideo(),
                            additional_ffmpeg_parameters=f"-ss {played} -to {duration}")
            if stream_type == "video"
            else AudioPiped(out, HighQualityAudio(),
                            additional_ffmpeg_parameters=f"-ss {played} -to {duration}")
        )
        if str(db[chat_id][0]["file"]) == str(file_path):
            await assistant.change_stream(chat_id, stream)
        else:
            raise AssistantErr("Umm")
        if str(db[chat_id][0]["file"]) == str(file_path):
            exis = playing[0].get("old_dur")
            if not exis:
                db[chat_id][0]["old_dur"]    = db[chat_id][0]["dur"]
                db[chat_id][0]["old_second"] = db[chat_id][0]["seconds"]
            db[chat_id][0]["played"]     = con_seconds
            db[chat_id][0]["dur"]        = duration
            db[chat_id][0]["seconds"]    = dur
            db[chat_id][0]["speed_path"] = out
            db[chat_id][0]["speed"]      = speed

    async def force_stop_stream(self, chat_id: int):
        assistant = await group_assistant(self, chat_id)
        try:
            db.get(chat_id).pop(0)
        except Exception:
            pass
        await remove_active_video_chat(chat_id)
        await remove_active_chat(chat_id)
        try:
            await assistant.leave_group_call(chat_id)
        except Exception:
            pass

    async def skip_stream(
        self, chat_id: int, link: str,
        video: Union[bool, str] = None,
        image: Union[bool, str] = None,
    ):
        assistant = await group_assistant(self, chat_id)
        stream = (
            AudioVideoPiped(link, HighQualityAudio(), MediumQualityVideo())
            if video
            else AudioPiped(link, HighQualityAudio())
        )
        await assistant.change_stream(chat_id, stream)

    async def seek_stream(self, chat_id, file_path, to_seek, duration, mode):
        assistant = await group_assistant(self, chat_id)
        stream = (
            AudioVideoPiped(file_path, HighQualityAudio(), MediumQualityVideo(),
                            additional_ffmpeg_parameters=f"-ss {to_seek} -to {duration}")
            if mode == "video"
            else AudioPiped(file_path, HighQualityAudio(),
                            additional_ffmpeg_parameters=f"-ss {to_seek} -to {duration}")
        )
        await assistant.change_stream(chat_id, stream)

    async def stream_call(self, link):
        assistant = await group_assistant(self, config.LOGGER_ID)
        await assistant.join_group_call(
            config.LOGGER_ID,
            AudioVideoPiped(link),
            stream_type=StreamType().pulse_stream,
        )
        await asyncio.sleep(0.2)
        await assistant.leave_group_call(config.LOGGER_ID)

    # ── join_call ─────────────────────────────────────────────────────────────

    @error_logger(label="Assistant Join Call")
    async def join_call(
        self,
        chat_id: int,
        original_chat_id: int,
        link,
        video: Union[bool, str] = None,
        image: Union[bool, str] = None,
    ):
        assistant = await group_assistant(self, chat_id)
        language = await get_lang(chat_id)
        _ = get_string(language)
        stream = (
            AudioVideoPiped(link, HighQualityAudio(), MediumQualityVideo())
            if video
            else AudioPiped(link, HighQualityAudio())
        )

        max_retries = 5
        retry_delay = 3.0
        last_exc = None

        for attempt in range(1, max_retries + 1):
            try:
                await assistant.join_group_call(chat_id, stream, stream_type=StreamType().pulse_stream)
                last_exc = None
                break

            except FloodWait as fw:
                wait = fw.value + 1
                assis_num = await get_assistant_number(chat_id) or "?"
                _LOG.warning(
                    f"[join_call] FloodWait {wait}s for chat {chat_id} "
                    f"assistant #{assis_num} attempt {attempt}/{max_retries}"
                )
                await _notify_owner(
                    _["owner_warn_flood"].format(chat_id, wait) +
                    f"\n<b>Assistant #:</b> <code>{assis_num}</code>"
                )
                await asyncio.sleep(wait)
                last_exc = fw
                if attempt == max_retries:
                    raise AssistantErr(_["call_flood_wait"].format(wait))

            except Exception as e:
                if _is_frozen(e):
                    assis_num = await get_assistant_number(chat_id) or "?"
                    _LOG.error(
                        f"[join_call] Frozen account detected — Assistant #{assis_num} "
                        f"for chat {chat_id}: {e}"
                    )
                    await _notify_owner(
                        _["owner_warn_frozen"].format(chat_id, str(e)) +
                        f"\n<b>Assistant #:</b> <code>{assis_num}</code>"
                    )
                    raise AssistantErr(_["call_frozen"])

                elif _is_retryable_call_error(e):
                    if attempt < max_retries:
                        wait = retry_delay * attempt
                        _LOG.warning(
                            f"[join_call] Retryable error for {chat_id} "
                            f"attempt {attempt}/{max_retries}: {e}. Retry in {wait}s…"
                        )
                        await asyncio.sleep(wait)
                        last_exc = e
                        continue
                    raise AssistantErr(_["call_retryable_failed"].format(str(e)[:80]))

                elif isinstance(e, NoActiveGroupCall):
                    raise AssistantErr(_["call_8"])
                elif isinstance(e, AlreadyJoinedError):
                    try:
                        await assistant.change_stream(chat_id, stream)
                    except Exception:
                        raise AssistantErr(_["call_9"])
                    break
                elif isinstance(e, TelegramServerError):
                    raise AssistantErr(_["call_10"])
                else:
                    err_str = str(e).lower()
                    if "groupcall" in err_str and ("invalid" in err_str or "id" in err_str):
                        raise AssistantErr(_["call_11"])
                    raise

        await add_active_chat(chat_id)
        await music_on(chat_id)
        if video:
            await add_active_video_chat(chat_id)
        if await is_autoend():
            counter[chat_id] = {}
            users = len(await assistant.get_participants(chat_id))
            if users == 1:
                autoend[chat_id] = datetime.now() + timedelta(minutes=1)

    # ── change_stream (queue advance with Spotify3 hold→edit pattern) ─────────

    @error_logger(label="Change Stream")
    async def change_stream(self, client, chat_id):
        check = db.get(chat_id)
        popped = None
        loop = await get_loop(chat_id)
        try:
            if loop == 0:
                popped = check.pop(0)
            else:
                await set_loop(chat_id, loop - 1)
            await auto_clean(popped)
            if not check:
                await _clear_(chat_id)
                return await client.leave_group_call(chat_id)
        except Exception:
            try:
                await _clear_(chat_id)
                return await client.leave_group_call(chat_id)
            except Exception:
                return

        queued           = check[0]["file"]
        language         = await get_lang(chat_id)
        _                = get_string(language)
        title            = check[0]["title"].title()
        user             = check[0]["by"]
        original_chat_id = check[0]["chat_id"]
        streamtype       = check[0]["streamtype"]
        videoid          = check[0]["vidid"]
        db[chat_id][0]["played"] = 0

        if (exis := check[0].get("old_dur")):
            db[chat_id][0]["dur"]        = exis
            db[chat_id][0]["seconds"]    = check[0]["old_second"]
            db[chat_id][0]["speed_path"] = None
            db[chat_id][0]["speed"]      = 1.0

        video      = str(streamtype) == "video"
        hold_text  = _["call_7"]
        button     = stream_markup(_, chat_id)

        # ── Live stream ───────────────────────────────────────────────────────
        if "live_" in queued:
            n, link = await YouTube.video(videoid, True)
            if n == 0:
                return await _safe_tg(app.send_message, original_chat_id, text=_["call_6"])
            stream = (
                AudioVideoPiped(link, HighQualityAudio(), MediumQualityVideo())
                if video else AudioPiped(link, HighQualityAudio())
            )
            try:
                await client.change_stream(chat_id, stream)
            except Exception as e:
                _LOG.error(f"[ChangeStream] live change_stream error: {e}")
                return await _safe_tg(app.send_message, original_chat_id, text=_["call_6"])

            img     = await get_thumb(videoid)
            caption = _["stream_1"].format(
                f"https://youtube.com/watch?v={videoid}", title[:23], check[0]["dur"], user
            )
            run = await _send_now_playing(original_chat_id, img, caption, button, hold_text=hold_text)
            db[chat_id][0]["mystic"] = run
            db[chat_id][0]["markup"] = "tg"

        # ── YouTube video/audio ───────────────────────────────────────────────
        elif "vid_" in queued:
            hold_msg = await _safe_tg(app.send_message, original_chat_id, hold_text)
            try:
                file_path, direct = await YouTube.download(
                    videoid, hold_msg, videoid=True,
                    video=video,
                )
            except Exception as e:
                _LOG.error(f"[ChangeStream] YouTube download exception: {e}")
                asyncio.ensure_future(self._log_dl_error("YouTube vid_ download", videoid, e))
                file_path = None
                direct    = False

            if not file_path:
                await self._handle_dl_failure(client, chat_id, original_chat_id, hold_msg, title, _)
                return

            stream = (
                AudioVideoPiped(file_path, HighQualityAudio(), MediumQualityVideo())
                if video else AudioPiped(file_path, HighQualityAudio())
            )
            try:
                await client.change_stream(chat_id, stream)
            except Exception as e:
                _LOG.error(f"[ChangeStream] change_stream error after YT download: {e}")
                return await _safe_tg(app.send_message, original_chat_id, text=_["call_6"])

            img     = await get_thumb(videoid)
            caption = _["stream_1"].format(
                f"https://youtube.com/watch?v={videoid}", title[:23], check[0]["dur"], user
            )
            run = await _send_now_playing(
                original_chat_id, img, caption, button,
                hold_msg=hold_msg,
            )
            db[chat_id][0]["mystic"] = run
            db[chat_id][0]["markup"] = "stream"

        # ── Index / direct URL ────────────────────────────────────────────────
        elif "index_" in queued:
            stream = (
                AudioVideoPiped(videoid, HighQualityAudio(), MediumQualityVideo())
                if str(streamtype) == "video"
                else AudioPiped(videoid, HighQualityAudio())
            )
            try:
                await client.change_stream(chat_id, stream)
            except Exception as e:
                _LOG.error(f"[ChangeStream] index change_stream error: {e}")
                return await _safe_tg(app.send_message, original_chat_id, text=_["call_6"])

            caption = _["stream_2"].format(user)
            run = await _send_now_playing(
                original_chat_id, config.STREAM_IMG_URL, caption, button, hold_text=hold_text
            )
            db[chat_id][0]["mystic"] = run
            db[chat_id][0]["markup"] = "tg"

        # ── API-2 / platform / other ──────────────────────────────────────────
        else:
            _API2_PLATFORMS = {
                "spotify", "gaana", "jiosaavn", "deezer", "apple",
                "tidal", "soundcloud_api", "soundcloud",
            }
            _platform_str = (check[0].get("streamtype") or "").lower()
            _is_api2 = queued.startswith("http") and _platform_str in _API2_PLATFORMS

            if _is_api2:
                hold_msg = await _safe_tg(app.send_message, original_chat_id, hold_text)
                try:
                    from AnonXMusic.platforms.Api import ApiPlatform as _ApiPlatform
                    _file_path = await _ApiPlatform().download(queued)
                except Exception as e:
                    _LOG.error(f"[ChangeStream] API-2 download failed for {queued!r}: {e}")
                    asyncio.ensure_future(self._log_dl_error(f"API-2 ({_platform_str})", queued, e))
                    _file_path = None

                if not _file_path or not os.path.isfile(_file_path):
                    await self._handle_dl_failure(client, chat_id, original_chat_id, hold_msg, title, _)
                    return

                db[chat_id][0]["file"] = _file_path
                queued = _file_path

            elif not queued.startswith("http") and not os.path.isfile(queued):
                _platform = check[0].get("streamtype") or "Unknown"
                _LOG.error(f"[ChangeStream] File not found for {_platform}: {queued}")
                return await _safe_tg(app.send_message, original_chat_id, _["play_dl_failed"].format(_platform))

            stream = (
                AudioVideoPiped(queued, HighQualityAudio(), MediumQualityVideo())
                if video else AudioPiped(queued, HighQualityAudio())
            )
            try:
                await client.change_stream(chat_id, stream)
            except Exception as e:
                _LOG.error(f"[ChangeStream] change_stream error: {e}")
                return await _safe_tg(app.send_message, original_chat_id, text=_["call_6"])

            # Build image + caption
            if videoid == "telegram":
                img = (
                    config.TELEGRAM_AUDIO_URL if str(streamtype) == "audio"
                    else config.TELEGRAM_VIDEO_URL
                )
                caption = _["stream_1"].format(config.SUPPORT_CHAT, title[:23], check[0]["dur"], user)
            elif videoid == "soundcloud":
                img     = config.SOUNCLOUD_IMG_URL
                caption = _["stream_1"].format(config.SUPPORT_CHAT, title[:23], check[0]["dur"], user)
            elif _platform_str in _API2_PLATFORMS:
                _img_map = {
                    "spotify": config.SPOTIFY_PLAYLIST_IMG_URL, "gaana": config.GAANA_IMG_URL,
                    "jiosaavn": config.JIOSAAVN_IMG_URL, "deezer": config.DEEZER_IMG_URL,
                    "apple": config.APPLE_IMG_URL, "tidal": config.TIDAL_IMG_URL,
                    "soundcloud_api": config.SOUNCLOUD_IMG_URL, "soundcloud": config.SOUNCLOUD_IMG_URL,
                }
                img     = check[0].get("thumbnail") or _img_map.get(_platform_str, config.PLAYLIST_IMG_URL)
                caption = _["stream_1"].format(
                    check[0].get("file") or config.SUPPORT_CHAT, title[:23], check[0]["dur"], user
                )
            else:
                img     = await get_thumb(videoid)
                caption = _["stream_1"].format(
                    f"https://youtube.com/watch?v={videoid}", title[:23], check[0]["dur"], user
                )

            if _is_api2:
                run = await _send_now_playing(
                    original_chat_id, img, caption, button,
                    hold_msg=hold_msg,
                )
            else:
                run = await _send_now_playing(
                    original_chat_id, img, caption, button, hold_text=hold_text
                )

            db[chat_id][0]["mystic"] = run
            db[chat_id][0]["markup"] = "stream"

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _log_dl_error(self, context: str, identifier: str, exc: Exception) -> None:
        """Send download failure report to ERROR_LOG_ID."""
        try:
            import html as _html
            from pyrogram.enums import ParseMode
            tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            text = (
                f"⚠️ <b>Download Failed</b>\n\n"
                f"📌 <b>Context:</b> <code>{_html.escape(context)}</code>\n"
                f"🔗 <b>Track:</b> <code>{_html.escape(str(identifier)[:200])}</code>\n"
                f"❌ <b>Exception:</b> <code>{_html.escape(type(exc).__name__)}</code>\n"
                f"💬 <b>Message:</b> <code>{_html.escape(str(exc))}</code>\n\n"
                f"📄 <b>Traceback:</b>\n<pre>{_html.escape(tb[:3000])}</pre>"
            )
            await app.send_message(
                chat_id=config.ERROR_LOG_ID, text=text,
                parse_mode=ParseMode.HTML, disable_web_page_preview=True,
            )
        except Exception as e:
            _LOG.warning(f"[call] Could not send dl error to ERROR_LOG_ID: {e}")

    async def _handle_dl_failure(self, client, chat_id, original_chat_id, hold_msg, title, _):
        """Skip or stop when a download fails in change_stream."""
        try:
            await hold_msg.edit_text(
                f"❌ Download failed for <b>{title[:60]}</b> — skipping.",
                parse_mode="html",
            )
        except Exception:
            pass

        remaining = db.get(chat_id)
        if remaining and len(remaining) > 1:
            try:
                remaining.pop(0)
            except Exception:
                pass
            return await self.change_stream(client, chat_id)
        else:
            await _clear_(chat_id)
            try:
                await client.leave_group_call(chat_id)
            except Exception:
                pass
            await _safe_tg(app.send_message, original_chat_id, _["call_queue_end"])

    # ── Ping ──────────────────────────────────────────────────────────────────

    async def ping(self):
        pings = []
        for attr, string_attr in [
            ("one", "STRING1"), ("two", "STRING2"), ("three", "STRING3"),
            ("four", "STRING4"), ("five", "STRING5"),
        ]:
            if getattr(config, string_attr):
                pings.append(await getattr(self, attr).ping)
        return str(round(sum(pings) / len(pings), 3)) if pings else "N/A"

    # ── Start ─────────────────────────────────────────────────────────────────

    async def start(self):
        _LOG.info("Starting PyTgCalls Client(s)...")
        for attr, string_attr in [
            ("one", "STRING1"), ("two", "STRING2"), ("three", "STRING3"),
            ("four", "STRING4"), ("five", "STRING5"),
        ]:
            if getattr(config, string_attr):
                await getattr(self, attr).start()

    # ── Decorators ────────────────────────────────────────────────────────────

    async def decorators(self):
        clients = [
            c for c, s in [
                (self.one, "STRING1"), (self.two, "STRING2"), (self.three, "STRING3"),
                (self.four, "STRING4"), (self.five, "STRING5"),
            ] if getattr(config, s)
        ]

        for c in clients:
            @c.on_kicked()
            @c.on_closed_voice_chat()
            @c.on_left()
            async def stream_services_handler(_, chat_id: int):
                await self.stop_stream(chat_id)

        for c in clients:
            @c.on_stream_end()
            @error_logger(label="Stream End Handler")
            async def stream_end_handler(client_obj, update: Update):
                if not isinstance(update, StreamAudioEnded):
                    return
                max_retries = 5
                for attempt in range(1, max_retries + 1):
                    try:
                        await self.change_stream(client_obj, update.chat_id)
                        break
                    except (OSError, ConnectionResetError, ConnectionError) as e:
                        if attempt < max_retries:
                            wait = 3 * attempt
                            _LOG.warning(
                                f"[StreamEnd] Connection error attempt {attempt}/{max_retries} "
                                f"chat {update.chat_id}: {e}. Retry in {wait}s"
                            )
                            await asyncio.sleep(wait)
                        else:
                            _LOG.error(f"[StreamEnd] Gave up after {max_retries} retries for {update.chat_id}: {e}")
                    except Exception as e:
                        _LOG.error(f"[StreamEnd] Unexpected error for chat {update.chat_id}: {e}")
                        break


Anony = Call()
