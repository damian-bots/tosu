import asyncio
import os
from datetime import datetime, timedelta
from typing import Union

from pyrogram import Client
from pyrogram.types import InlineKeyboardMarkup, InputMediaPhoto
from pytgcalls import PyTgCalls, StreamType
from pytgcalls.exceptions import (
    AlreadyJoinedError,
    NoActiveGroupCall,
    TelegramServerError,
)
from pytgcalls.types import Update
from pytgcalls.types.input_stream import AudioPiped, AudioVideoPiped
from pytgcalls.types.input_stream.quality import HighQualityAudio, MediumQualityVideo
from pytgcalls.types.stream import StreamAudioEnded

import config
from AnonXMusic import LOGGER, YouTube, app
from AnonXMusic.utils.error_logger import error_logger


async def _safe_tg_call(coro_fn, *args, retries: int = 4, base_delay: float = 3.0, **kwargs):
    """Retry a Pyrogram coroutine on transient TCP connection errors."""
    for attempt in range(1, retries + 1):
        try:
            return await coro_fn(*args, **kwargs)
        except (OSError, ConnectionResetError, ConnectionError) as e:
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


async def _send_now_playing(
    original_chat_id: int,
    photo: str,
    caption: str,
    button,
    *,
    send_hold_first: bool = True,
    hold_text: str = "⏳ Loading next track...",
) -> object:
    """
    Spotify3-style send-then-edit pattern for Now Playing cards.

    1. Send a plain text "Hold on…" message.
    2. Edit it in-place to a photo card (started-streaming style).
    3. Fall back to send_photo if edit fails.

    Returns the final Message object.
    """
    mystic = None
    if send_hold_first:
        try:
            mystic = await _safe_tg_call(
                app.send_message,
                original_chat_id,
                hold_text,
            )
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
            # edit failed — fall through to send_photo
            try:
                await mystic.delete()
            except Exception:
                pass

    # Fallback: direct send_photo
    return await _safe_tg_call(
        app.send_photo,
        chat_id=original_chat_id,
        photo=photo,
        caption=caption,
        reply_markup=InlineKeyboardMarkup(button),
    )


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
        self.one = PyTgCalls(
            self.userbot1,
            cache_duration=100,
        )
        self.userbot2 = Client(
            name="AnonXAss2",
            api_id=config.API_ID,
            api_hash=config.API_HASH,
            session_string=str(config.STRING2),
        )
        self.two = PyTgCalls(
            self.userbot2,
            cache_duration=100,
        )
        self.userbot3 = Client(
            name="AnonXAss3",
            api_id=config.API_ID,
            api_hash=config.API_HASH,
            session_string=str(config.STRING3),
        )
        self.three = PyTgCalls(
            self.userbot3,
            cache_duration=100,
        )
        self.userbot4 = Client(
            name="AnonXAss4",
            api_id=config.API_ID,
            api_hash=config.API_HASH,
            session_string=str(config.STRING4),
        )
        self.four = PyTgCalls(
            self.userbot4,
            cache_duration=100,
        )
        self.userbot5 = Client(
            name="AnonXAss5",
            api_id=config.API_ID,
            api_hash=config.API_HASH,
            session_string=str(config.STRING5),
        )
        self.five = PyTgCalls(
            self.userbot5,
            cache_duration=100,
        )

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
        except:
            pass

    async def stop_stream_force(self, chat_id: int):
        try:
            if config.STRING1:
                await self.one.leave_group_call(chat_id)
        except:
            pass
        try:
            if config.STRING2:
                await self.two.leave_group_call(chat_id)
        except:
            pass
        try:
            if config.STRING3:
                await self.three.leave_group_call(chat_id)
        except:
            pass
        try:
            if config.STRING4:
                await self.four.leave_group_call(chat_id)
        except:
            pass
        try:
            if config.STRING5:
                await self.five.leave_group_call(chat_id)
        except:
            pass
        try:
            await _clear_(chat_id)
        except:
            pass

    async def speedup_stream(self, chat_id: int, file_path, speed, playing):
        assistant = await group_assistant(self, chat_id)
        if str(speed) != str("1.0"):
            base = os.path.basename(file_path)
            chatdir = os.path.join(os.getcwd(), "playback", str(speed))
            if not os.path.isdir(chatdir):
                os.makedirs(chatdir)
            out = os.path.join(chatdir, base)
            if not os.path.isfile(out):
                if str(speed) == str("0.5"):
                    vs = 2.0
                if str(speed) == str("0.75"):
                    vs = 1.35
                if str(speed) == str("1.5"):
                    vs = 0.68
                if str(speed) == str("2.0"):
                    vs = 0.5
                proc = await asyncio.create_subprocess_shell(
                    cmd=(
                        "ffmpeg "
                        "-i "
                        f"{file_path} "
                        "-filter:v "
                        f"setpts={vs}*PTS "
                        "-filter:a "
                        f"atempo={speed} "
                        f"{out}"
                    ),
                    stdin=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await proc.communicate()
            else:
                pass
        else:
            out = file_path
        dur = await asyncio.get_event_loop().run_in_executor(None, check_duration, out)
        dur = int(dur)
        played, con_seconds = speed_converter(playing[0]["played"], speed)
        duration = seconds_to_min(dur)
        stream = (
            AudioVideoPiped(
                out,
                audio_parameters=HighQualityAudio(),
                video_parameters=MediumQualityVideo(),
                additional_ffmpeg_parameters=f"-ss {played} -to {duration}",
            )
            if playing[0]["streamtype"] == "video"
            else AudioPiped(
                out,
                audio_parameters=HighQualityAudio(),
                additional_ffmpeg_parameters=f"-ss {played} -to {duration}",
            )
        )
        if str(db[chat_id][0]["file"]) == str(file_path):
            await assistant.change_stream(chat_id, stream)
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

    async def force_stop_stream(self, chat_id: int):
        assistant = await group_assistant(self, chat_id)
        try:
            check = db.get(chat_id)
            check.pop(0)
        except:
            pass
        await remove_active_video_chat(chat_id)
        await remove_active_chat(chat_id)
        try:
            await assistant.leave_group_call(chat_id)
        except:
            pass

    async def skip_stream(
        self,
        chat_id: int,
        link: str,
        video: Union[bool, str] = None,
        image: Union[bool, str] = None,
    ):
        assistant = await group_assistant(self, chat_id)
        if video:
            stream = AudioVideoPiped(
                link,
                audio_parameters=HighQualityAudio(),
                video_parameters=MediumQualityVideo(),
            )
        else:
            stream = AudioPiped(link, audio_parameters=HighQualityAudio())
        await assistant.change_stream(
            chat_id,
            stream,
        )

    async def seek_stream(self, chat_id, file_path, to_seek, duration, mode):
        assistant = await group_assistant(self, chat_id)
        stream = (
            AudioVideoPiped(
                file_path,
                audio_parameters=HighQualityAudio(),
                video_parameters=MediumQualityVideo(),
                additional_ffmpeg_parameters=f"-ss {to_seek} -to {duration}",
            )
            if mode == "video"
            else AudioPiped(
                file_path,
                audio_parameters=HighQualityAudio(),
                additional_ffmpeg_parameters=f"-ss {to_seek} -to {duration}",
            )
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
        if video:
            stream = AudioVideoPiped(
                link,
                audio_parameters=HighQualityAudio(),
                video_parameters=MediumQualityVideo(),
            )
        else:
            stream = (
                AudioVideoPiped(
                    link,
                    audio_parameters=HighQualityAudio(),
                    video_parameters=MediumQualityVideo(),
                )
                if video
                else AudioPiped(link, audio_parameters=HighQualityAudio())
            )
        try:
            await assistant.join_group_call(
                chat_id,
                stream,
                stream_type=StreamType().pulse_stream,
            )
        except NoActiveGroupCall:
            raise AssistantErr(_["call_8"])
        except AlreadyJoinedError:
            # Assistant already in the call — try to switch the stream instead.
            try:
                await assistant.change_stream(chat_id, stream)
            except Exception:
                raise AssistantErr(_["call_9"])
        except TelegramServerError:
            raise AssistantErr(_["call_10"])
        except Exception as e:
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

    @error_logger(label="Change Stream")
    async def change_stream(self, client, chat_id):
        check = db.get(chat_id)
        popped = None
        loop = await get_loop(chat_id)
        try:
            if loop == 0:
                popped = check.pop(0)
            else:
                loop = loop - 1
                await set_loop(chat_id, loop)
            await auto_clean(popped)
            if not check:
                await _clear_(chat_id)
                return await client.leave_group_call(chat_id)
        except:
            try:
                await _clear_(chat_id)
                return await client.leave_group_call(chat_id)
            except:
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
            video = True if str(streamtype) == "video" else False

            # ── Spotify3-style: send a "Hold on…" text first, then edit to
            # the Now Playing photo card so each track transition feels smooth.
            hold_text = _["call_7"]  # "Hold on — downloading the next track in queue..."
            button = stream_markup(_, chat_id)

            if "live_" in queued:
                n, link = await YouTube.video(videoid, True)
                if n == 0:
                    return await _safe_tg_call(
                        app.send_message,
                        original_chat_id,
                        text=_["call_6"],
                    )
                if video:
                    stream = AudioVideoPiped(
                        link,
                        audio_parameters=HighQualityAudio(),
                        video_parameters=MediumQualityVideo(),
                    )
                else:
                    stream = AudioPiped(
                        link,
                        audio_parameters=HighQualityAudio(),
                    )
                try:
                    await client.change_stream(chat_id, stream)
                except Exception:
                    return await _safe_tg_call(
                        app.send_message,
                        original_chat_id,
                        text=_["call_6"],
                    )
                img = await get_thumb(videoid)
                caption = _["stream_1"].format(
                    f"https://youtube.com/watch?v={videoid}",
                    title[:23],
                    check[0]["dur"],
                    user,
                )
                run = await _send_now_playing(
                    original_chat_id, img, caption, button,
                    hold_text=hold_text,
                )
                db[chat_id][0]["mystic"] = run
                db[chat_id][0]["markup"] = "tg"

            elif "vid_" in queued:
                # Send "Hold on…" immediately before the potentially slow download
                hold_msg = await _safe_tg_call(
                    app.send_message, original_chat_id, hold_text
                )
                try:
                    file_path, direct = await YouTube.download(
                        videoid,
                        hold_msg,
                        videoid=True,
                        video=True if str(streamtype) == "video" else False,
                    )
                except:
                    file_path = None
                    direct = False
                if not file_path:
                    try:
                        await hold_msg.delete()
                    except Exception:
                        pass
                    remaining = db.get(chat_id)
                    if remaining and len(remaining) > 1:
                        await _safe_tg_call(
                            app.send_message,
                            original_chat_id,
                            _["call_queue_fail"],
                        )
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
                        return await _safe_tg_call(
                            app.send_message,
                            original_chat_id,
                            _["call_queue_end"],
                        )
                if video:
                    stream = AudioVideoPiped(
                        file_path,
                        audio_parameters=HighQualityAudio(),
                        video_parameters=MediumQualityVideo(),
                    )
                else:
                    stream = AudioPiped(
                        file_path,
                        audio_parameters=HighQualityAudio(),
                    )
                try:
                    await client.change_stream(chat_id, stream)
                except:
                    return await _safe_tg_call(
                        app.send_message,
                        original_chat_id,
                        text=_["call_6"],
                    )
                img = await get_thumb(videoid)
                caption = _["stream_1"].format(
                    f"https://youtube.com/watch?v={videoid}",
                    title[:23],
                    check[0]["dur"],
                    user,
                )
                # Edit the hold_msg to the Now Playing card (Spotify3 style)
                try:
                    run = await hold_msg.edit_message_media(
                        media=InputMediaPhoto(media=img, caption=caption),
                        reply_markup=InlineKeyboardMarkup(button),
                    )
                except Exception:
                    try:
                        await hold_msg.delete()
                    except Exception:
                        pass
                    run = await _safe_tg_call(
                        app.send_photo,
                        chat_id=original_chat_id,
                        photo=img,
                        caption=caption,
                        reply_markup=InlineKeyboardMarkup(button),
                    )
                db[chat_id][0]["mystic"] = run
                db[chat_id][0]["markup"] = "stream"

            elif "index_" in queued:
                stream = (
                    AudioVideoPiped(
                        videoid,
                        audio_parameters=HighQualityAudio(),
                        video_parameters=MediumQualityVideo(),
                    )
                    if str(streamtype) == "video"
                    else AudioPiped(videoid, audio_parameters=HighQualityAudio())
                )
                try:
                    await client.change_stream(chat_id, stream)
                except:
                    return await _safe_tg_call(
                        app.send_message,
                        original_chat_id,
                        text=_["call_6"],
                    )
                caption = _["stream_2"].format(user)
                run = await _send_now_playing(
                    original_chat_id,
                    config.STREAM_IMG_URL,
                    caption,
                    button,
                    hold_text=hold_text,
                )
                db[chat_id][0]["mystic"] = run
                db[chat_id][0]["markup"] = "tg"

            else:
                # ── API-2 platform tracks queued as URLs ──────────────────────
                _api2_platforms = {
                    "spotify", "gaana", "jiosaavn", "deezer", "apple",
                    "tidal", "soundcloud_api", "soundcloud",
                }
                _platform_str = (check[0].get("streamtype") or "").lower()
                _is_api2_url = (
                    queued.startswith("http")
                    and _platform_str in _api2_platforms
                )

                if _is_api2_url:
                    # Send the hold message before the potentially slow API download
                    hold_msg = await _safe_tg_call(
                        app.send_message,
                        original_chat_id,
                        hold_text,
                    )
                    try:
                        from AnonXMusic.platforms.Api import ApiPlatform as _ApiPlatform
                        _file_path = await _ApiPlatform().download(queued)
                    except Exception as _exc:
                        LOGGER(__name__).error(
                            f"[change_stream] API-2 download failed for {queued!r}: {_exc}"
                        )
                        _file_path = None

                    if not _file_path or not os.path.isfile(_file_path):
                        try:
                            await hold_msg.edit_text(
                                f"❌ Download failed for <b>{title[:60]}</b> — skipping to next.",
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
                            return await _safe_tg_call(
                                app.send_message,
                                original_chat_id,
                                _["call_queue_end"],
                            )

                    db[chat_id][0]["file"] = _file_path
                    queued = _file_path

                elif not queued.startswith("http") and not os.path.isfile(queued):
                    _platform = check[0].get("streamtype") or "Unknown"
                    return await _safe_tg_call(
                        app.send_message,
                        original_chat_id,
                        _["play_dl_failed"].format(_platform),
                    )

                if video:
                    stream = AudioVideoPiped(
                        queued,
                        audio_parameters=HighQualityAudio(),
                        video_parameters=MediumQualityVideo(),
                    )
                else:
                    stream = AudioPiped(
                        queued,
                        audio_parameters=HighQualityAudio(),
                    )
                try:
                    await client.change_stream(chat_id, stream)
                except:
                    return await _safe_tg_call(
                        app.send_message,
                        original_chat_id,
                        text=_["call_6"],
                    )

                # Build caption and image
                if videoid == "telegram":
                    img = (
                        config.TELEGRAM_AUDIO_URL
                        if str(streamtype) == "audio"
                        else config.TELEGRAM_VIDEO_URL
                    )
                    caption = _["stream_1"].format(
                        config.SUPPORT_CHAT, title[:23], check[0]["dur"], user
                    )
                elif videoid == "soundcloud":
                    img = config.SOUNCLOUD_IMG_URL
                    caption = _["stream_1"].format(
                        config.SUPPORT_CHAT, title[:23], check[0]["dur"], user
                    )
                elif _platform_str in _api2_platforms:
                    _thumb = check[0].get("thumbnail") or ""
                    _link  = check[0].get("file") or ""
                    _img_map = {
                        "spotify":        config.SPOTIFY_PLAYLIST_IMG_URL,
                        "gaana":          config.GAANA_IMG_URL,
                        "jiosaavn":       config.JIOSAAVN_IMG_URL,
                        "deezer":         config.DEEZER_IMG_URL,
                        "apple":          config.APPLE_IMG_URL,
                        "tidal":          config.TIDAL_IMG_URL,
                        "soundcloud_api": config.SOUNCLOUD_IMG_URL,
                        "soundcloud":     config.SOUNCLOUD_IMG_URL,
                    }
                    img = _thumb or _img_map.get(_platform_str, config.PLAYLIST_IMG_URL)
                    caption = _["stream_1"].format(
                        _link or config.SUPPORT_CHAT,
                        title[:23],
                        check[0]["dur"],
                        user,
                    )
                else:
                    img = await get_thumb(videoid)
                    caption = _["stream_1"].format(
                        f"https://youtube.com/watch?v={videoid}",
                        title[:23],
                        check[0]["dur"],
                        user,
                    )

                # Spotify3 send-then-edit for API-2 tracks (hold_msg already sent above)
                if _is_api2_url:
                    try:
                        run = await hold_msg.edit_message_media(
                            media=InputMediaPhoto(media=img, caption=caption),
                            reply_markup=InlineKeyboardMarkup(button),
                        )
                    except Exception:
                        try:
                            await hold_msg.delete()
                        except Exception:
                            pass
                        run = await _safe_tg_call(
                            app.send_photo,
                            chat_id=original_chat_id,
                            photo=img,
                            caption=caption,
                            reply_markup=InlineKeyboardMarkup(button),
                        )
                else:
                    # Non-API2 local files: send-then-edit pattern
                    run = await _send_now_playing(
                        original_chat_id, img, caption, button,
                        hold_text=hold_text,
                    )

                db[chat_id][0]["mystic"] = run
                db[chat_id][0]["markup"] = "stream"

    async def ping(self):
        pings = []
        if config.STRING1:
            pings.append(await self.one.ping)
        if config.STRING2:
            pings.append(await self.two.ping)
        if config.STRING3:
            pings.append(await self.three.ping)
        if config.STRING4:
            pings.append(await self.four.ping)
        if config.STRING5:
            pings.append(await self.five.ping)
        return str(round(sum(pings) / len(pings), 3))

    async def start(self):
        LOGGER(__name__).info("Starting PyTgCalls Client...\n")
        if config.STRING1:
            await self.one.start()
        if config.STRING2:
            await self.two.start()
        if config.STRING3:
            await self.three.start()
        if config.STRING4:
            await self.four.start()
        if config.STRING5:
            await self.five.start()

    async def decorators(self):
        @self.one.on_kicked()
        @self.two.on_kicked()
        @self.three.on_kicked()
        @self.four.on_kicked()
        @self.five.on_kicked()
        @self.one.on_closed_voice_chat()
        @self.two.on_closed_voice_chat()
        @self.three.on_closed_voice_chat()
        @self.four.on_closed_voice_chat()
        @self.five.on_closed_voice_chat()
        @self.one.on_left()
        @self.two.on_left()
        @self.three.on_left()
        @self.four.on_left()
        @self.five.on_left()
        async def stream_services_handler(_, chat_id: int):
            await self.stop_stream(chat_id)

        @self.one.on_stream_end()
        @self.two.on_stream_end()
        @self.three.on_stream_end()
        @self.four.on_stream_end()
        @self.five.on_stream_end()
        @error_logger(label="Stream End Handler")
        async def stream_end_handler1(client, update: Update):
            if not isinstance(update, StreamAudioEnded):
                return
            max_retries = 5
            base_delay = 3
            for attempt in range(1, max_retries + 1):
                try:
                    await self.change_stream(client, update.chat_id)
                    break
                except (OSError, ConnectionResetError, ConnectionError) as e:
                    if attempt < max_retries:
                        wait = base_delay * attempt
                        LOGGER(__name__).warning(
                            f"[stream_end] Connection error on attempt {attempt}/{max_retries} "
                            f"for chat {update.chat_id}: {e}. Retrying in {wait}s..."
                        )
                        await asyncio.sleep(wait)
                    else:
                        LOGGER(__name__).error(
                            f"[stream_end] Gave up after {max_retries} retries "
                            f"for chat {update.chat_id}: {e}"
                        )
                except Exception as e:
                    LOGGER(__name__).error(
                        f"[stream_end] Unexpected error for chat {update.chat_id}: {e}"
                    )
                    break


Anony = Call()
