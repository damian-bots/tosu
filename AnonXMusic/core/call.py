# Copyright (c) 2025 AnonXMusic
# Adapted to py-tgcalls >= 2.x / ntgcalls API

import asyncio
import os
from datetime import datetime, timedelta
from typing import Union

from pyrogram import Client
from pyrogram.types import InlineKeyboardMarkup

# ── New pytgcalls / ntgcalls imports ────────────────────────────────────────
from ntgcalls import (
    ConnectionError,
    ConnectionNotFound,
    RTMPStreamingUnsupported,
    TelegramServerError,
)
from pytgcalls import PyTgCalls, exceptions, types
from pytgcalls.pytgcalls_session import PyTgCallsSession
# ────────────────────────────────────────────────────────────────────────────

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


# ── Helpers ──────────────────────────────────────────────────────────────────

async def _safe_tg_call(coro_fn, *args, retries: int = 4, base_delay: float = 3.0, **kwargs):
    """Retry a Pyrogram coroutine on transient TCP connection errors."""
    for attempt in range(1, retries + 1):
        try:
            return await coro_fn(*args, **kwargs)
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
    """Build a MediaStream for the new pytgcalls API."""
    ffmpeg_params = f"-ss {seek_time}" if seek_time > 1 else None
    return types.MediaStream(
        media_path=link,
        audio_parameters=types.AudioQuality.HIGH,
        video_parameters=types.VideoQuality.HD_720p,
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


# ── Main Call class ──────────────────────────────────────────────────────────

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

    # ── Stream controls ──────────────────────────────────────────────────────

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

    # ── Speed-up ─────────────────────────────────────────────────────────────

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
        video = playing[0]["streamtype"] == "video"
        stream = _make_stream(out, video=video)

        if str(db[chat_id][0]["file"]) == str(file_path):
            await assistant.play(
                chat_id=chat_id,
                stream=stream,
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

    # ── Skip / seek ──────────────────────────────────────────────────────────

    async def skip_stream(
        self,
        chat_id: int,
        link: str,
        video: Union[bool, str] = None,
        image: Union[bool, str] = None,
    ):
        assistant = await group_assistant(self, chat_id)
        stream = _make_stream(link, video=bool(video))
        await assistant.play(
            chat_id=chat_id,
            stream=stream,
            config=types.GroupCallConfig(auto_start=False),
        )

    async def seek_stream(self, chat_id, file_path, to_seek, duration, mode):
        assistant = await group_assistant(self, chat_id)
        stream = _make_stream(file_path, video=(mode == "video"), seek_time=int(to_seek))
        await assistant.play(
            chat_id=chat_id,
            stream=stream,
            config=types.GroupCallConfig(auto_start=False),
        )

    # ── Logger stream_call (pulse stream) ────────────────────────────────────

    async def stream_call(self, link):
        assistant = await group_assistant(self, config.LOGGER_ID)
        stream = _make_stream(link, video=True)
        await assistant.play(
            chat_id=config.LOGGER_ID,
            stream=stream,
            config=types.GroupCallConfig(auto_start=False),
        )
        await asyncio.sleep(0.2)
        await assistant.leave_call(config.LOGGER_ID)

    # ── Join call with retry ──────────────────────────────────────────────────

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
        stream = _make_stream(link, video=bool(video))

        max_retries = 5
        retry_delay = 2.0
        for attempt in range(1, max_retries + 1):
            try:
                await assistant.play(
                    chat_id=chat_id,
                    stream=stream,
                    config=types.GroupCallConfig(auto_start=False),
                )
                break  # success
            except exceptions.NoActiveGroupCall:
                if attempt < max_retries:
                    LOGGER(__name__).warning(
                        f"[join_call] NoActiveGroupCall for {chat_id}, "
                        f"retry {attempt}/{max_retries} in {retry_delay}s…"
                    )
                    await asyncio.sleep(retry_delay)
                else:
                    raise AssistantErr(_["call_8"])
            except exceptions.AlreadyJoinedError:
                raise AssistantErr(_["call_9"])
            except (TelegramServerError, ConnectionError, ConnectionNotFound):
                raise AssistantErr(_["call_10"])
            except RTMPStreamingUnsupported:
                raise AssistantErr(_["call_11"])

        await add_active_chat(chat_id)
        await music_on(chat_id)
        if video:
            await add_active_video_chat(chat_id)
        if await is_autoend():
            counter[chat_id] = {}
            users = len(await assistant.get_participants(chat_id))
            if users == 1:
                autoend[chat_id] = datetime.now() + timedelta(minutes=1)

    # ── Change stream (queue advance) ────────────────────────────────────────

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

            async def _play(stream):
                await client.play(
                    chat_id=chat_id,
                    stream=stream,
                    config=types.GroupCallConfig(auto_start=False),
                )

            if "live_" in queued:
                n, link = await YouTube.video(videoid, True)
                if n == 0:
                    return await _safe_tg_call(
                        app.send_message, original_chat_id, text=_["call_6"]
                    )
                stream = _make_stream(link, video=video)
                try:
                    await _play(stream)
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
                stream = _make_stream(file_path, video=video)
                try:
                    await _play(stream)
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
                stream = _make_stream(videoid, video=video)
                try:
                    await _play(stream)
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
                # Guard: if the queued file no longer exists on disk, skip gracefully
                if not queued.startswith("http") and not os.path.isfile(queued):
                    mystic = await _safe_tg_call(
                        app.send_message, original_chat_id, _["call_7"]
                    )
                    platform = check[0].get("streamtype") or "Unknown"
                    return await mystic.edit_text(
                        _["play_dl_failed"].format(platform),
                        disable_web_page_preview=True,
                    )
                stream = _make_stream(queued, video=video)
                try:
                    await _play(stream)
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

    # ── Ping ─────────────────────────────────────────────────────────────────

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

    # ── Boot ─────────────────────────────────────────────────────────────────

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

    # ── Decorators (event handlers) ──────────────────────────────────────────

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
                # Stream ended → advance queue
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
                            except Exception as e:
                                LOGGER(__name__).error(
                                    f"[stream_end] Unexpected error for chat "
                                    f"{update.chat_id}: {e}"
                                )
                                break

                # Chat left / kicked / voice chat closed → clean up
                elif isinstance(update, types.ChatUpdate):
                    if update.status in [
                        types.ChatUpdate.Status.KICKED,
                        types.ChatUpdate.Status.LEFT_GROUP,
                        types.ChatUpdate.Status.CLOSED_VOICE_CHAT,
                    ]:
                        await self.stop_stream(update.chat_id)


Anony = Call()
