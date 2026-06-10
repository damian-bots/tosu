# ╔══════════════════════════════════════════════════════════════════╗
# ║        Copyright © tusar404 — All Rights Reserved               ║
# ║     AnonXMusic · Telegram Music Bot · Powered by PyTgCalls      ║
# ║        Unauthorized copying or distribution is prohibited        ║
# ╚══════════════════════════════════════════════════════════════════╝

import asyncio
import os
from datetime import datetime, timedelta
from typing import Union

from pyrogram import Client
from pyrogram.errors import FloodWait, ChannelsTooMuch
from pyrogram.types import InlineKeyboardMarkup

from ntgcalls import (
    ConnectionError as NTGConnectionError,
    ConnectionNotFound,
    RTMPStreamingUnsupported,
    TelegramServerError,
)
from pytgcalls import PyTgCalls, exceptions, types
from pytgcalls.pytgcalls_session import PyTgCallsSession

import config
from AnonXMusic import LOGGER, YouTube, app
from AnonXMusic.utils.error_logger import error_logger
from AnonXMusic.misc import db
from AnonXMusic.utils.database import (
    add_active_chat,
    add_active_video_chat,
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

_RETRYABLE_TG_ERRORS = (
    "GROUPCALL_ADD_PARTICIPANTS_FAILED",
    "CHANNELS_TOO_MUCH",
)


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
        LOGGER(__name__).warning(f"[notify_owner] failed: {e}")


async def _safe_tg_call(coro_fn, *args, retries: int = 4, base_delay: float = 3.0, **kwargs):
    for attempt in range(1, retries + 1):
        try:
            return await coro_fn(*args, **kwargs)
        except FloodWait as fw:
            wait = fw.value + 1
            LOGGER(__name__).warning(f"[TG] FloodWait {wait}s on attempt {attempt}/{retries}")
            await asyncio.sleep(wait)
            if attempt == retries:
                raise
        except (OSError, ConnectionResetError) as e:
            if attempt < retries:
                wait = base_delay * attempt
                LOGGER(__name__).warning(
                    f"[TG] Connection error attempt {attempt}/{retries}: {e}. Retry in {wait}s"
                )
                await asyncio.sleep(wait)
            else:
                LOGGER(__name__).error(f"[TG] Gave up after {retries} retries: {e}")
                raise
        except Exception:
            raise


def _make_stream(
    link: str,
    video: bool = False,
    seek_time: int = 0,
) -> types.MediaStream:
    """
    Build an optimized MediaStream for low-resource environments.

    Audio: HIGH quality (48 kHz stereo) — best quality/CPU balance.
    Video: SD_480p for small servers to prevent frame-drop lag;
           switch to HD_720p only when VIDEO_QUALITY env is "hd".
    """
    ffmpeg_params = f"-ss {seek_time}" if seek_time > 1 else None

    hd_mode = getattr(config, "VIDEO_QUALITY", "sd").lower() == "hd"
    video_quality = types.VideoQuality.HD_720p if hd_mode else types.VideoQuality.SD_480p

    return types.MediaStream(
        media_path=link,
        audio_parameters=types.AudioQuality.HIGH,
        video_parameters=video_quality,
        audio_flags=types.MediaStream.Flags.REQUIRED,
        video_flags=(
            types.MediaStream.Flags.AUTO_DETECT
            if video
            else types.MediaStream.Flags.IGNORE
        ),
        ffmpeg_parameters=ffmpeg_params,
    )


async def _clear_(chat_id):
    db[chat_id] = []
    await remove_active_video_chat(chat_id)
    await remove_active_chat(chat_id)


class Call(PyTgCalls):
    def __init__(self):
        self.userbot1 = Client(
            name="AnonXAss1",
            api_id=config.API_ID,
            api_hash=config.API_HASH,
            session_string=str(config.STRING1),
        )
        self.one = PyTgCalls(self.userbot1, cache_duration=100)

        self.userbot2 = Client(
            name="AnonXAss2",
            api_id=config.API_ID,
            api_hash=config.API_HASH,
            session_string=str(config.STRING2),
        )
        self.two = PyTgCalls(self.userbot2, cache_duration=100)

        self.userbot3 = Client(
            name="AnonXAss3",
            api_id=config.API_ID,
            api_hash=config.API_HASH,
            session_string=str(config.STRING3),
        )
        self.three = PyTgCalls(self.userbot3, cache_duration=100)

        self.userbot4 = Client(
            name="AnonXAss4",
            api_id=config.API_ID,
            api_hash=config.API_HASH,
            session_string=str(config.STRING4),
        )
        self.four = PyTgCalls(self.userbot4, cache_duration=100)

        self.userbot5 = Client(
            name="AnonXAss5",
            api_id=config.API_ID,
            api_hash=config.API_HASH,
            session_string=str(config.STRING5),
        )
        self.five = PyTgCalls(self.userbot5, cache_duration=100)

    async def pause_stream(self, chat_id: int):
        assistant = await group_assistant(self, chat_id)
        await assistant.pause(chat_id)

    async def resume_stream(self, chat_id: int):
        assistant = await group_assistant(self, chat_id)
        await assistant.resume(chat_id)

    async def stop_stream(self, chat_id: int):
        assistant = await group_assistant(self, chat_id)
        try:
            await _clear_(chat_id)
            await assistant.leave_call(chat_id)
        except Exception:
            pass

    async def stop_stream_force(self, chat_id: int):
        for attr, flag in [
            ("one", config.STRING1),
            ("two", config.STRING2),
            ("three", config.STRING3),
            ("four", config.STRING4),
            ("five", config.STRING5),
        ]:
            if flag:
                try:
                    await getattr(self, attr).leave_call(chat_id)
                except Exception:
                    pass
        try:
            await _clear_(chat_id)
        except Exception:
            pass

    async def force_stop_stream(self, chat_id: int):
        assistant = await group_assistant(self, chat_id)
        try:
            check = db.get(chat_id)
            check.pop(0)
        except Exception:
            pass
        await remove_active_video_chat(chat_id)
        await remove_active_chat(chat_id)
        try:
            await assistant.leave_call(chat_id)
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
                        f"ffmpeg -y -i {file_path} "
                        f"-filter:v setpts={vs}*PTS "
                        f"-filter:a atempo={speed} "
                        f"-threads 1 {out}"
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
        video = playing[0]["streamtype"] == "video"
        stream_obj = _make_stream(out, video=video)

        if str(db[chat_id][0]["file"]) == str(file_path):
            await assistant.play(
                chat_id=chat_id,
                stream=stream_obj,
                config=types.GroupCallConfig(auto_start=False),
            )
        else:
            raise AssistantErr("Umm")

        if str(db[chat_id][0]["file"]) == str(file_path):
            exis = (playing[0]).get("old_dur")
            if not exis:
                db[chat_id][0]["old_dur"] = db[chat_id][0]["dur"]
                db[chat_id][0]["old_second"] = db[chat_id][0]["seconds"]
            db[chat_id][0]["played"] = con_seconds
            db[chat_id][0]["dur"] = duration
            db[chat_id][0]["seconds"] = dur
            db[chat_id][0]["speed_path"] = out
            db[chat_id][0]["speed"] = speed

    async def skip_stream(
        self,
        chat_id: int,
        link: str,
        video: Union[bool, str] = None,
        image: Union[bool, str] = None,
    ):
        assistant = await group_assistant(self, chat_id)
        stream_obj = _make_stream(link, video=bool(video))
        await assistant.play(
            chat_id=chat_id,
            stream=stream_obj,
            config=types.GroupCallConfig(auto_start=False),
        )

    async def seek_stream(self, chat_id, file_path, to_seek, duration, mode):
        assistant = await group_assistant(self, chat_id)
        stream_obj = _make_stream(file_path, video=(mode == "video"), seek_time=int(to_seek))
        await assistant.play(
            chat_id=chat_id,
            stream=stream_obj,
            config=types.GroupCallConfig(auto_start=False),
        )

    async def stream_call(self, link):
        assistant = await group_assistant(self, config.LOGGER_ID)
        stream_obj = _make_stream(link, video=True)
        await assistant.play(
            chat_id=config.LOGGER_ID,
            stream=stream_obj,
            config=types.GroupCallConfig(auto_start=False),
        )
        await asyncio.sleep(0.2)
        await assistant.leave_call(config.LOGGER_ID)

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
        stream_obj = _make_stream(link, video=bool(video))

        max_retries = 5
        retry_delay = 3.0
        last_exc: Exception | None = None

        for attempt in range(1, max_retries + 1):
            try:
                await assistant.play(
                    chat_id=chat_id,
                    stream=stream_obj,
                    config=types.GroupCallConfig(auto_start=False),
                )
                last_exc = None
                break

            except FloodWait as fw:
                wait = fw.value + 1
                LOGGER(__name__).warning(
                    f"[join_call] FloodWait {wait}s for chat {chat_id} "
                    f"attempt {attempt}/{max_retries}"
                )
                await _notify_owner(
                    _["owner_warn_flood"].format(chat_id, wait)
                )
                await asyncio.sleep(wait)
                last_exc = fw
                if attempt == max_retries:
                    raise AssistantErr(_["call_flood_wait"].format(wait))

            except Exception as e:
                if _is_frozen(e):
                    LOGGER(__name__).error(
                        f"[join_call] Frozen account detected for chat {chat_id}: {e}"
                    )
                    await _notify_owner(_["owner_warn_frozen"].format(chat_id, str(e)))
                    raise AssistantErr(_["call_frozen"])

                elif _is_retryable_call_error(e):
                    if attempt < max_retries:
                        wait = retry_delay * attempt
                        LOGGER(__name__).warning(
                            f"[join_call] Retryable error for {chat_id} "
                            f"attempt {attempt}/{max_retries}: {e}. Retry in {wait}s…"
                        )
                        await asyncio.sleep(wait)
                        last_exc = e
                        continue
                    raise AssistantErr(_["call_retryable_failed"].format(str(e)[:80]))

                elif isinstance(e, ChannelsTooMuch):
                    if attempt < max_retries:
                        wait = retry_delay * attempt
                        LOGGER(__name__).warning(
                            f"[join_call] ChannelsTooMuch for {chat_id}, "
                            f"retry {attempt}/{max_retries} in {wait}s…"
                        )
                        await asyncio.sleep(wait)
                        last_exc = e
                        continue
                    raise AssistantErr(_["call_channels_too_much"])

                elif isinstance(e, exceptions.NoActiveGroupCall):
                    if attempt < max_retries:
                        LOGGER(__name__).warning(
                            f"[join_call] NoActiveGroupCall for {chat_id}, "
                            f"retry {attempt}/{max_retries} in {retry_delay}s…"
                        )
                        await asyncio.sleep(retry_delay)
                        last_exc = e
                        continue
                    raise AssistantErr(_["call_8"])

                elif isinstance(e, exceptions.NotInCallError):
                    LOGGER(__name__).warning(
                        f"[join_call] NotInCallError for {chat_id}; will retry once"
                    )
                    if attempt < max_retries:
                        await asyncio.sleep(1)
                        last_exc = e
                        continue
                    raise AssistantErr(_["call_9"])

                elif isinstance(e, (TelegramServerError, NTGConnectionError, ConnectionNotFound)):
                    raise AssistantErr(_["call_10"])

                elif isinstance(e, RTMPStreamingUnsupported):
                    raise AssistantErr(_["call_11"])

                else:
                    raise

        if last_exc:
            raise AssistantErr(_["call_10"])

        await add_active_chat(chat_id)
        await music_on(chat_id)
        if video:
            await add_active_video_chat(chat_id)
        if await is_autoend():
            counter[chat_id] = {}
            users = len(await assistant.get_participants(chat_id))
            if users == 1:
                autoend[chat_id] = datetime.now() + timedelta(minutes=1)

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
                return await client.leave_call(chat_id)
        except Exception:
            try:
                await _clear_(chat_id)
                return await client.leave_call(chat_id)
            except Exception:
                return
        else:
            queued = check[0]["file"]
            language = await get_lang(chat_id)
            _ = get_string(language)
            title = (check[0]["title"]).title()
            user = check[0]["by"]
            original_chat_id = check[0]["chat_id"]
            streamtype = check[0]["streamtype"]
            videoid = check[0]["vidid"]
            db[chat_id][0]["played"] = 0
            exis = (check[0]).get("old_dur")
            if exis:
                db[chat_id][0]["dur"] = exis
                db[chat_id][0]["seconds"] = check[0]["old_second"]
                db[chat_id][0]["speed_path"] = None
                db[chat_id][0]["speed"] = 1.0
            video = (str(streamtype) == "video")

            async def _play(stream_obj):
                await client.play(
                    chat_id=chat_id,
                    stream=stream_obj,
                    config=types.GroupCallConfig(auto_start=False),
                )

            if "live_" in queued:
                n, link = await YouTube.video(videoid, True)
                if n == 0:
                    return await _safe_tg_call(
                        app.send_message, original_chat_id, text=_["call_6"]
                    )
                stream_obj = _make_stream(link, video=video)
                try:
                    await _play(stream_obj)
                except Exception:
                    return await _safe_tg_call(
                        app.send_message, original_chat_id, text=_["call_6"]
                    )
                img = await get_thumb(videoid)
                button = stream_markup(_, chat_id)
                run = await _safe_tg_call(
                    app.send_photo,
                    chat_id=original_chat_id,
                    photo=img,
                    caption=_["stream_1"].format(
                        f"https://youtube.com/watch?v={videoid}",
                        title[:23],
                        check[0]["dur"],
                        user,
                    ),
                    reply_markup=InlineKeyboardMarkup(button),
                )
                db[chat_id][0]["mystic"] = run
                db[chat_id][0]["markup"] = "tg"

            elif "vid_" in queued:
                mystic = await _safe_tg_call(
                    app.send_message, original_chat_id, _["call_7"]
                )
                try:
                    file_path, direct = await YouTube.download(
                        videoid,
                        mystic,
                        videoid=True,
                        video=video,
                    )
                except Exception:
                    return await mystic.edit_text(
                        _["call_6"], disable_web_page_preview=True
                    )
                stream_obj = _make_stream(file_path, video=video)
                try:
                    await _play(stream_obj)
                except Exception:
                    return await _safe_tg_call(
                        app.send_message, original_chat_id, text=_["call_6"]
                    )
                img = await get_thumb(videoid)
                button = stream_markup(_, chat_id)
                await mystic.delete()
                run = await _safe_tg_call(
                    app.send_photo,
                    chat_id=original_chat_id,
                    photo=img,
                    caption=_["stream_1"].format(
                        f"https://youtube.com/watch?v={videoid}",
                        title[:23],
                        check[0]["dur"],
                        user,
                    ),
                    reply_markup=InlineKeyboardMarkup(button),
                )
                db[chat_id][0]["mystic"] = run
                db[chat_id][0]["markup"] = "stream"

            elif "index_" in queued:
                stream_obj = _make_stream(videoid, video=video)
                try:
                    await _play(stream_obj)
                except Exception:
                    return await _safe_tg_call(
                        app.send_message, original_chat_id, text=_["call_6"]
                    )
                button = stream_markup(_, chat_id)
                run = await _safe_tg_call(
                    app.send_photo,
                    chat_id=original_chat_id,
                    photo=config.STREAM_IMG_URL,
                    caption=_["stream_2"].format(user),
                    reply_markup=InlineKeyboardMarkup(button),
                )
                db[chat_id][0]["mystic"] = run
                db[chat_id][0]["markup"] = "tg"

            else:
                if not queued.startswith("http") and not os.path.isfile(queued):
                    mystic = await _safe_tg_call(
                        app.send_message, original_chat_id, _["call_7"]
                    )
                    platform = check[0].get("streamtype") or "Unknown"
                    return await mystic.edit_text(
                        _["play_dl_failed"].format(platform),
                        disable_web_page_preview=True,
                    )
                stream_obj = _make_stream(queued, video=video)
                try:
                    await _play(stream_obj)
                except Exception:
                    return await _safe_tg_call(
                        app.send_message, original_chat_id, text=_["call_6"]
                    )
                if videoid == "telegram":
                    button = stream_markup(_, chat_id)
                    run = await _safe_tg_call(
                        app.send_photo,
                        chat_id=original_chat_id,
                        photo=(
                            config.TELEGRAM_AUDIO_URL
                            if str(streamtype) == "audio"
                            else config.TELEGRAM_VIDEO_URL
                        ),
                        caption=_["stream_1"].format(
                            config.SUPPORT_CHAT, title[:23], check[0]["dur"], user
                        ),
                        reply_markup=InlineKeyboardMarkup(button),
                    )
                    db[chat_id][0]["mystic"] = run
                    db[chat_id][0]["markup"] = "tg"
                elif videoid == "soundcloud":
                    button = stream_markup(_, chat_id)
                    run = await _safe_tg_call(
                        app.send_photo,
                        chat_id=original_chat_id,
                        photo=config.SOUNCLOUD_IMG_URL,
                        caption=_["stream_1"].format(
                            config.SUPPORT_CHAT, title[:23], check[0]["dur"], user
                        ),
                        reply_markup=InlineKeyboardMarkup(button),
                    )
                    db[chat_id][0]["mystic"] = run
                    db[chat_id][0]["markup"] = "tg"
                else:
                    img = await get_thumb(videoid)
                    button = stream_markup(_, chat_id)
                    run = await _safe_tg_call(
                        app.send_photo,
                        chat_id=original_chat_id,
                        photo=img,
                        caption=_["stream_1"].format(
                            f"https://youtube.com/watch?v={videoid}",
                            title[:23],
                            check[0]["dur"],
                            user,
                        ),
                        reply_markup=InlineKeyboardMarkup(button),
                    )
                    db[chat_id][0]["mystic"] = run
                    db[chat_id][0]["markup"] = "stream"

    async def ping(self):
        pings = []
        for attr, flag in [
            ("one", config.STRING1),
            ("two", config.STRING2),
            ("three", config.STRING3),
            ("four", config.STRING4),
            ("five", config.STRING5),
        ]:
            if flag:
                pings.append(getattr(self, attr).ping)
        return str(round(sum(pings) / len(pings), 3)) if pings else "0"

    async def start(self):
        LOGGER(__name__).info("Starting PyTgCalls Client...\n")
        PyTgCallsSession.notice_displayed = True
        for attr, flag in [
            ("one", config.STRING1),
            ("two", config.STRING2),
            ("three", config.STRING3),
            ("four", config.STRING4),
            ("five", config.STRING5),
        ]:
            if flag:
                await getattr(self, attr).start()

    async def decorators(self):
        clients = [
            (self.one, config.STRING1),
            (self.two, config.STRING2),
            (self.three, config.STRING3),
            (self.four, config.STRING4),
            (self.five, config.STRING5),
        ]

        for client, flag in clients:
            if not flag:
                continue

            @client.on_update()
            async def update_handler(_, update: types.Update) -> None:
                if isinstance(update, types.StreamEnded):
                    if update.stream_type == types.StreamEnded.Type.AUDIO:
                        max_retries = 5
                        base_delay = 3.0
                        for attempt in range(1, max_retries + 1):
                            try:
                                await self.change_stream(_, update.chat_id)
                                break
                            except (OSError, ConnectionResetError) as e:
                                if attempt < max_retries:
                                    wait = base_delay * attempt
                                    LOGGER(__name__).warning(
                                        f"[stream_end] Connection error attempt "
                                        f"{attempt}/{max_retries} for chat "
                                        f"{update.chat_id}: {e}. Retry in {wait}s…"
                                    )
                                    await asyncio.sleep(wait)
                                else:
                                    LOGGER(__name__).error(
                                        f"[stream_end] Gave up after {max_retries} "
                                        f"retries for chat {update.chat_id}: {e}"
                                    )
                            except FloodWait as fw:
                                wait = fw.value + 1
                                LOGGER(__name__).warning(
                                    f"[stream_end] FloodWait {wait}s for chat "
                                    f"{update.chat_id}, attempt {attempt}/{max_retries}"
                                )
                                await _notify_owner(
                                    f"⚠️ <b>FloodWait on stream-end</b>\n"
                                    f"Chat: <code>{update.chat_id}</code>\n"
                                    f"Wait: <b>{wait}s</b>"
                                )
                                await asyncio.sleep(wait)
                            except Exception as e:
                                if _is_frozen(e):
                                    LOGGER(__name__).error(
                                        f"[stream_end] Frozen account for chat "
                                        f"{update.chat_id}: {e}"
                                    )
                                    await _notify_owner(
                                        f"🚨 <b>Frozen Account Detected</b>\n"
                                        f"Chat: <code>{update.chat_id}</code>\n"
                                        f"Error: <code>{str(e)[:200]}</code>"
                                    )
                                else:
                                    LOGGER(__name__).error(
                                        f"[stream_end] Unexpected error for chat "
                                        f"{update.chat_id}: {e}"
                                    )
                                break

                elif isinstance(update, types.ChatUpdate):
                    if update.status in [
                        types.ChatUpdate.Status.KICKED,
                        types.ChatUpdate.Status.LEFT_GROUP,
                        types.ChatUpdate.Status.CLOSED_VOICE_CHAT,
                    ]:
                        await self.stop_stream(update.chat_id)


Anony = Call()
