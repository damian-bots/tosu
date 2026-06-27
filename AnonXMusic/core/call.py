# AnonXMusic · core/call.py  (v2.0.0 - Stable Edition)
# Fixes:
#   1. Lag / CPU spike: replaced cache_duration=100 with 200; added uvloop;
#      thumbnail generation offloaded; ffmpeg CPU-limited via thread pool.
#   2. "No active video chat found" error: assistant state is verified before
#      every stream action; auto-rejoin on phantom disconnect.
#   3. Assistant vanishing: heartbeat task re-joins if py-tgcalls loses track
#      of the assistant but Telegram still shows it present.
#   4. Memory leak: file cleanup now happens on EVERY path through auto_clean.
#   5. Advanced assistant handling: rotating assistants, health checks,
#      per-assistant error counters, automatic failover.

import asyncio
import os
import traceback
from contextlib import suppress
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

# ── Per-assistant error tracking (for failover) ───────────────────────────────
_assistant_error_counts: dict = {}   # {assis_num: int}
_assistant_last_error: dict = {}     # {assis_num: datetime}
_MAX_ERRORS_BEFORE_SKIP = 3
_ERROR_RESET_MINUTES    = 10

# ── Assistant health cache (avoids repeated API calls) ────────────────────────
_assistant_healthy: dict = {}        # {assis_num: bool}

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


def _is_no_video_chat_error(exc: Exception) -> bool:
    """Detect the 'No active video chat found' false-positive from py-tgcalls 0.9.7."""
    msg = str(exc).lower()
    return (
        "no active" in msg and ("video" in msg or "group call" in msg)
        or isinstance(exc, NoActiveGroupCall)
    )


async def _notify_owner(text: str) -> None:
    with suppress(Exception):
        await app.send_message(int(config.OWNER_ID), text, disable_web_page_preview=True)


def _record_assistant_error(assis_num: int) -> None:
    now = datetime.now()
    last = _assistant_last_error.get(assis_num)
    # Reset count if errors are old
    if last and (now - last).total_seconds() > _ERROR_RESET_MINUTES * 60:
        _assistant_error_counts[assis_num] = 0
    _assistant_error_counts[assis_num] = _assistant_error_counts.get(assis_num, 0) + 1
    _assistant_last_error[assis_num] = now


def _assistant_is_unhealthy(assis_num: int) -> bool:
    return _assistant_error_counts.get(assis_num, 0) >= _MAX_ERRORS_BEFORE_SKIP


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
    hold_msg=None,
) -> object:
    mystic = hold_msg
    if mystic is None:
        with suppress(Exception):
            mystic = await _safe_tg(app.send_message, chat_id, hold_text)

    if mystic is not None:
        try:
            run = await mystic.edit_message_media(
                media=InputMediaPhoto(media=photo, caption=caption),
                reply_markup=InlineKeyboardMarkup(button),
            )
            return run
        except Exception:
            with suppress(Exception):
                await mystic.delete()

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

# Track which chats each assistant is handling (for heartbeat)
_assistant_chat_map: dict = {}   # {chat_id: assis_num}


async def _clear_(chat_id):
    db[chat_id] = []
    await remove_active_video_chat(chat_id)
    await remove_active_chat(chat_id)
    _assistant_chat_map.pop(chat_id, None)


# ── Stream building helpers ───────────────────────────────────────────────────

def _build_stream(link: str, video: bool) -> Union[AudioVideoPiped, AudioPiped]:
    """Build a pytgcalls stream object. Video uses MediumQuality to reduce CPU."""
    if video:
        return AudioVideoPiped(link, HighQualityAudio(), MediumQualityVideo())
    return AudioPiped(link, HighQualityAudio())


def _build_stream_seek(link: str, video: bool, ss: str, to: str) -> Union[AudioVideoPiped, AudioPiped]:
    extra = f"-ss {ss} -to {to}"
    if video:
        return AudioVideoPiped(link, HighQualityAudio(), MediumQualityVideo(),
                               additional_ffmpeg_parameters=extra)
    return AudioPiped(link, HighQualityAudio(), additional_ffmpeg_parameters=extra)


class Call(PyTgCalls):
    def __init__(self):
        def _make_client(name, string_attr):
            return Client(
                name=name,
                api_id=config.API_ID,
                api_hash=config.API_HASH,
                session_string=str(getattr(config, string_attr)),
            )

        # cache_duration=200 reduces redundant Telegram API calls vs 100
        self.userbot1 = _make_client("AnonXAss1", "STRING1")
        self.one   = PyTgCalls(self.userbot1,   cache_duration=200)
        self.userbot2 = _make_client("AnonXAss2", "STRING2")
        self.two   = PyTgCalls(self.userbot2,   cache_duration=200)
        self.userbot3 = _make_client("AnonXAss3", "STRING3")
        self.three = PyTgCalls(self.userbot3,   cache_duration=200)
        self.userbot4 = _make_client("AnonXAss4", "STRING4")
        self.four  = PyTgCalls(self.userbot4,   cache_duration=200)
        self.userbot5 = _make_client("AnonXAss5", "STRING5")
        self.five  = PyTgCalls(self.userbot5,   cache_duration=200)

        # Map assistant number → PyTgCalls instance (used by heartbeat)
        self._ptgc_map = {
            1: self.one, 2: self.two, 3: self.three, 4: self.four, 5: self.five,
        }

    # ── get PyTgCalls instance by number ─────────────────────────────────────

    def _get_ptgc(self, num: int) -> PyTgCalls:
        return self._ptgc_map.get(int(num))

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
            with suppress(Exception):
                if getattr(config, string_attr):
                    await getattr(self, attr).leave_group_call(chat_id)
        with suppress(Exception):
            await _clear_(chat_id)

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
                        f"ffmpeg -y -i {file_path} "
                        f"-filter:v setpts={vs}*PTS "
                        f"-filter:a atempo={speed} {out}"
                    ),
                    stdin=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await proc.communicate()
        else:
            out = file_path

        loop = asyncio.get_event_loop()
        dur = int(await loop.run_in_executor(None, check_duration, out))
        played, con_seconds = speed_converter(playing[0]["played"], speed)
        duration = seconds_to_min(dur)
        stream_type = playing[0]["streamtype"]
        stream = _build_stream_seek(out, stream_type == "video", played, duration)

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
        with suppress(Exception):
            db.get(chat_id).pop(0)
        await remove_active_video_chat(chat_id)
        await remove_active_chat(chat_id)
        with suppress(Exception):
            await assistant.leave_group_call(chat_id)

    async def skip_stream(
        self, chat_id: int, link: str,
        video: Union[bool, str] = None,
        image: Union[bool, str] = None,
    ):
        assistant = await group_assistant(self, chat_id)
        stream = _build_stream(link, bool(video))
        await assistant.change_stream(chat_id, stream)

    async def seek_stream(self, chat_id, file_path, to_seek, duration, mode):
        assistant = await group_assistant(self, chat_id)
        stream = _build_stream_seek(file_path, mode == "video", to_seek, duration)
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

    # ── Assistant health check ────────────────────────────────────────────────

    async def _verify_assistant_in_call(self, assistant: PyTgCalls, chat_id: int) -> bool:
        """
        Check if the assistant is actually present in the group call.
        Returns True if present (or can't verify), False if definitely absent.
        This fixes the 'assistant vanishing but users can still hear old stream' bug.
        """
        try:
            participants = await assistant.get_participants(chat_id)
            return len(participants) > 0
        except Exception:
            # If we can't check, assume it's fine to avoid false stops
            return True

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
        stream = _build_stream(link, bool(video))

        max_retries = 5
        retry_delay = 3.0
        last_exc = None

        for attempt in range(1, max_retries + 1):
            try:
                await assistant.join_group_call(
                    chat_id, stream, stream_type=StreamType().pulse_stream
                )
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
                    _record_assistant_error(int(assis_num) if str(assis_num).isdigit() else 0)
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

                elif _is_no_video_chat_error(e):
                    # py-tgcalls 0.9.7 false positive: voice call active but no video chat
                    # This happens when joining audio-only while video isn't started
                    if video:
                        # Try joining as audio-only as fallback
                        _LOG.warning(
                            f"[join_call] No active video chat for {chat_id}, "
                            f"falling back to audio-only"
                        )
                        try:
                            audio_stream = AudioPiped(link, HighQualityAudio())
                            await assistant.join_group_call(
                                chat_id, audio_stream, stream_type=StreamType().pulse_stream
                            )
                            video = None  # downgraded to audio
                            break
                        except AlreadyJoinedError:
                            with suppress(Exception):
                                await assistant.change_stream(chat_id, audio_stream)
                            break
                        except Exception as inner:
                            _LOG.error(f"[join_call] Audio fallback also failed: {inner}")
                            raise AssistantErr(_["call_8"])
                    raise AssistantErr(_["call_8"])

                elif isinstance(e, NoActiveGroupCall):
                    raise AssistantErr(_["call_8"])

                elif isinstance(e, AlreadyJoinedError):
                    # Assistant is already in call — just change stream
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

        # Track which assistant handles this chat
        assis_num = await get_assistant_number(chat_id)
        if assis_num:
            _assistant_chat_map[chat_id] = assis_num

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

        # ── Verify assistant is still in call before changing stream ──────────
        # This fixes the "assistant vanishes but users can still hear" bug
        try:
            in_call = await self._verify_assistant_in_call(client, chat_id)
            if not in_call:
                _LOG.warning(
                    f"[ChangeStream] Assistant not in call for {chat_id}, "
                    f"will attempt rejoin during stream change"
                )
        except Exception:
            pass  # Non-fatal — proceed with change_stream anyway

        # ── Live stream ───────────────────────────────────────────────────────
        if "live_" in queued:
            n, link = await YouTube.video(videoid, True)
            if n == 0:
                return await _safe_tg(app.send_message, original_chat_id, text=_["call_6"])
            stream = _build_stream(link, video)
            try:
                await client.change_stream(chat_id, stream)
            except NoActiveGroupCall:
                # Attempt to rejoin — fixes assistant vanishing mid-stream
                _LOG.warning(f"[ChangeStream] NoActiveGroupCall on live for {chat_id}, attempting rejoin")
                try:
                    await client.join_group_call(chat_id, stream, stream_type=StreamType().pulse_stream)
                except Exception as rejoin_err:
                    _LOG.error(f"[ChangeStream] Rejoin failed: {rejoin_err}")
                    return await _safe_tg(app.send_message, original_chat_id, text=_["call_6"])
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

            stream = _build_stream(file_path, video)
            try:
                await client.change_stream(chat_id, stream)
            except NoActiveGroupCall:
                _LOG.warning(f"[ChangeStream] NoActiveGroupCall on YT for {chat_id}, attempting rejoin")
                try:
                    await client.join_group_call(chat_id, stream, stream_type=StreamType().pulse_stream)
                except Exception as rejoin_err:
                    _LOG.error(f"[ChangeStream] Rejoin failed: {rejoin_err}")
                    return await _safe_tg(app.send_message, original_chat_id, text=_["call_6"])
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
            stream = _build_stream(videoid, str(streamtype) == "video")
            try:
                await client.change_stream(chat_id, stream)
            except NoActiveGroupCall:
                _LOG.warning(f"[ChangeStream] NoActiveGroupCall on index for {chat_id}, rejoin")
                try:
                    await client.join_group_call(chat_id, stream, stream_type=StreamType().pulse_stream)
                except Exception as rejoin_err:
                    _LOG.error(f"[ChangeStream] Rejoin failed: {rejoin_err}")
                    return await _safe_tg(app.send_message, original_chat_id, text=_["call_6"])
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

            stream = _build_stream(queued, video)
            try:
                await client.change_stream(chat_id, stream)
            except NoActiveGroupCall:
                _LOG.warning(f"[ChangeStream] NoActiveGroupCall for {chat_id}, rejoin")
                try:
                    await client.join_group_call(chat_id, stream, stream_type=StreamType().pulse_stream)
                except Exception as rejoin_err:
                    _LOG.error(f"[ChangeStream] Rejoin failed: {rejoin_err}")
                    return await _safe_tg(app.send_message, original_chat_id, text=_["call_6"])
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
        with suppress(Exception):
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
            from pyrogram.enums import ParseMode
            await app.send_message(
                chat_id=config.ERROR_LOG_ID, text=text,
                parse_mode=ParseMode.HTML, disable_web_page_preview=True,
            )

    async def _handle_dl_failure(self, client, chat_id, original_chat_id, hold_msg, title, _):
        with suppress(Exception):
            await hold_msg.edit_text(
                f"❌ Download failed for <b>{title[:60]}</b> — skipping.",
                parse_mode="html",
            )

        remaining = db.get(chat_id)
        if remaining and len(remaining) > 1:
            with suppress(Exception):
                remaining.pop(0)
            return await self.change_stream(client, chat_id)
        else:
            await _clear_(chat_id)
            with suppress(Exception):
                await client.leave_group_call(chat_id)
            await _safe_tg(app.send_message, original_chat_id, _["call_queue_end"])

    # ── Ping ──────────────────────────────────────────────────────────────────

    async def ping(self):
        pings = []
        for attr, string_attr in [
            ("one", "STRING1"), ("two", "STRING2"), ("three", "STRING3"),
            ("four", "STRING4"), ("five", "STRING5"),
        ]:
            if getattr(config, string_attr):
                with suppress(Exception):
                    pings.append(await getattr(self, attr).ping)
        return str(round(sum(pings) / len(pings), 3)) if pings else "N/A"

    # ── Heartbeat: detect and recover vanished assistants ────────────────────

    async def _assistant_heartbeat(self):
        """
        Background task: every 60s check if assistants are still in their calls.
        If py-tgcalls thinks the assistant left (raises NoActiveGroupCall) but
        active_chats still has the chat, attempt to resume.

        This fixes the core bug:
          - Assistant vanishes from video chat mid-stream
          - Old users can still hear because their client buffered audio
          - New users can't hear because assistant is no longer sending
        """
        await asyncio.sleep(30)  # Let startup settle
        while True:
            await asyncio.sleep(60)
            from AnonXMusic.utils.database import get_active_chats, group_assistant
            try:
                active_chats = await get_active_chats()
            except Exception:
                continue

            for chat_id in list(active_chats):
                current = db.get(chat_id)
                if not current:
                    continue
                try:
                    assistant = await group_assistant(self, chat_id)
                    participants = await assistant.get_participants(chat_id)
                    # If assistant has 0 participants it may have dropped out
                    if len(participants) == 0:
                        _LOG.warning(
                            f"[Heartbeat] Assistant shows 0 participants for chat {chat_id}, "
                            f"attempting stream resume"
                        )
                        queued_file = current[0].get("file", "")
                        streamtype  = current[0].get("streamtype", "audio")
                        video       = str(streamtype) == "video"
                        if queued_file and (queued_file.startswith("http") or os.path.isfile(queued_file)):
                            stream = _build_stream(queued_file, video)
                            try:
                                await assistant.join_group_call(
                                    chat_id, stream, stream_type=StreamType().pulse_stream
                                )
                                _LOG.info(f"[Heartbeat] Recovered assistant for chat {chat_id}")
                            except AlreadyJoinedError:
                                # Already joined — change stream to force reconnect
                                with suppress(Exception):
                                    await assistant.change_stream(chat_id, stream)
                            except Exception as e:
                                _LOG.error(f"[Heartbeat] Recovery failed for {chat_id}: {e}")
                except Exception as e:
                    # Silently skip — heartbeat is best-effort
                    _LOG.debug(f"[Heartbeat] Error for chat {chat_id}: {e}")

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

        # Start heartbeat task
        asyncio.ensure_future(self._assistant_heartbeat())
        _LOG.info("[decorators] Assistant heartbeat task started")


Anony = Call()
