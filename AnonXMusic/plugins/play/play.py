import random
import string
from urllib.parse import unquote

from pyrogram import filters
from pyrogram.types import InlineKeyboardMarkup, InputMediaPhoto, Message
from pytgcalls.exceptions import NoActiveGroupCall

import config
from AnonXMusic import (
    Apple, Deezer, DirectMedia, Gaana, JioSaavn, Kick, MXPlayer,
    Resso, SoundCloud, Spotify, Telegram, Tidal, Twitch, YouTube, app,
)
from AnonXMusic.core.call import Anony
from AnonXMusic.utils import seconds_to_min, time_to_seconds
from AnonXMusic.utils.channelplay import get_channeplayCB
from AnonXMusic.utils.decorators.language import languageCB
from AnonXMusic.utils.decorators.play import PlayWrapper
from AnonXMusic.utils.formatters import formats
from AnonXMusic.utils.inline import (
    botplaylist_markup,
    livestream_markup,
    playlist_markup,
    slider_markup,
    track_markup,
)
from AnonXMusic.utils.logger import play_logs
from AnonXMusic.utils.stream.stream import stream
from config import BANNED_USERS, lyrical


@app.on_message(
    filters.command(
        [
            "play",
            "vplay",
            "cplay",
            "cvplay",
            "playforce",
            "vplayforce",
            "cplayforce",
            "cvplayforce",
        ]
    )
    & filters.group
    & ~BANNED_USERS
)
@PlayWrapper
async def play_commnd(
    client,
    message: Message,
    _,
    chat_id,
    video,
    channel,
    playmode,
    url,
    fplay,
):
    mystic = await message.reply_text(
        _["play_2"].format(channel) if channel else _["play_1"]
    )
    
    plist_id = None
    slider = None
    plist_type = None
    spotify = None
    user_id = message.from_user.id
    user_name = message.from_user.first_name
    audio_telegram = (
        (message.reply_to_message.audio or message.reply_to_message.voice)
        if message.reply_to_message
        else None
    )
    video_telegram = (
        (message.reply_to_message.video or message.reply_to_message.document)
        if message.reply_to_message
        else None
    )
    
    if audio_telegram:
        if audio_telegram.file_size > 104857600:
            return await mystic.edit_text(_["play_5"])
        duration_min = seconds_to_min(audio_telegram.duration)
        if (audio_telegram.duration) > config.DURATION_LIMIT:
            return await mystic.edit_text(
                _["play_6"].format(config.DURATION_LIMIT_MIN, app.mention)
            )
        file_path = await Telegram.get_filepath(audio=audio_telegram)
        if await Telegram.download(_, message, mystic, file_path):
            message_link = await Telegram.get_link(message)
            file_name = await Telegram.get_filename(audio_telegram, audio=True)
            dur = await Telegram.get_duration(audio_telegram, file_path)
            details = {
                "title": file_name,
                "link": message_link,
                "path": file_path,
                "dur": dur,
            }

            try:
                await stream(
                    _,
                    mystic,
                    user_id,
                    details,
                    chat_id,
                    user_name,
                    message.chat.id,
                    streamtype="telegram",
                    forceplay=fplay,
                )
            except Exception as e:
                ex_type = type(e).__name__
                err = e if ex_type == "AssistantErr" else _["general_2"].format(ex_type)
                return await mystic.edit_text(err)
            return await mystic.delete()
        return
        
    elif video_telegram:
        if message.reply_to_message.document:
            try:
                ext = video_telegram.file_name.split(".")[-1]
                if ext.lower() not in formats:
                    return await mystic.edit_text(
                        _["play_7"].format(f"{' | '.join(formats)}")
                    )
            except:
                return await mystic.edit_text(
                    _["play_7"].format(f"{' | '.join(formats)}")
                )
        if video_telegram.file_size > config.TG_VIDEO_FILESIZE_LIMIT:
            return await mystic.edit_text(_["play_8"])
        file_path = await Telegram.get_filepath(video=video_telegram)
        if await Telegram.download(_, message, mystic, file_path):
            message_link = await Telegram.get_link(message)
            file_name = await Telegram.get_filename(video_telegram)
            dur = await Telegram.get_duration(video_telegram, file_path)
            details = {
                "title": file_name,
                "link": message_link,
                "path": file_path,
                "dur": dur,
            }
            try:
                await stream(
                    _,
                    mystic,
                    user_id,
                    details,
                    chat_id,
                    user_name,
                    message.chat.id,
                    video=True,
                    streamtype="telegram",
                    forceplay=fplay,
                )
            except Exception as e:
                ex_type = type(e).__name__
                err = e if ex_type == "AssistantErr" else _["general_2"].format(ex_type)
                return await mystic.edit_text(err)
            return await mystic.delete()
        return
        
    elif url:
        if await YouTube.exists(url):
            if not config.ENABLE_YOUTUBE:
                return await mystic.edit_text(_["play_disabled"].format("YouTube"))
            if "playlist" in url:
                try:
                    details = await YouTube.playlist(
                        url,
                        config.PLAYLIST_FETCH_LIMIT,
                        message.from_user.id,
                    )
                except:
                    return await mystic.edit_text(_["play_dl_failed"].format("YouTube (Playlist)"))
                streamtype = "playlist"
                plist_type = "yt"
                if "&" in url:
                    plist_id = (url.split("=")[1]).split("&")[0]
                else:
                    plist_id = url.split("=")[1]
                img = config.PLAYLIST_IMG_URL
                cap = _["play_9"]
            else:
                try:
                    details, track_id = await YouTube.track(url)
                except:
                    return await mystic.edit_text(_["play_dl_failed"].format("YouTube"))
                streamtype = "youtube"
                img = details["thumb"]
                cap = _["play_10"].format(
                    details["title"],
                    details["duration_min"],
                )

        elif await Spotify.valid(url):
            if not config.ENABLE_SPOTIFY:
                return await mystic.edit_text(_["play_disabled"].format("Spotify"))
            spotify = True
            if not config.SPOTIFY_CLIENT_ID and not config.SPOTIFY_CLIENT_SECRET:
                return await mystic.edit_text(
                    "» sᴘᴏᴛɪғʏ ɪs ɴᴏᴛ sᴜᴘᴘᴏʀᴛᴇᴅ ʏᴇᴛ.\n\nᴘʟᴇᴀsᴇ ᴛʀʏ ᴀɢᴀɪɴ ʟᴀᴛᴇʀ."
                )
            if "track" in url:
                try:
                    details, track_id = await Spotify.track(url)
                except Exception:
                    return await mystic.edit_text(_["play_3"])
                try:
                    sp_file = await Spotify.download(url)
                    if sp_file:
                        details["file_path"] = sp_file
                        streamtype = "spotify"
                    else:
                        streamtype = "youtube"
                except Exception:
                    streamtype = "youtube"
                img = details.get("thumb") or config.SPOTIFY_PLAYLIST_IMG_URL
                cap = _["play_10"].format(details["title"], details["duration_min"])
            elif "playlist" in url:
                try:
                    details, plist_id = await Spotify.playlist(url)
                except Exception:
                    return await mystic.edit_text(_["play_3"])
                streamtype = "playlist"
                plist_type = "spplay"
                img = config.SPOTIFY_PLAYLIST_IMG_URL
                cap = _["play_11"].format(app.mention, message.from_user.mention)
            elif "album" in url:
                try:
                    details, plist_id = await Spotify.album(url)
                except:
                    return await mystic.edit_text(_["play_3"])
                streamtype = "playlist"
                plist_type = "spalbum"
                img = config.SPOTIFY_ALBUM_IMG_URL
                cap = _["play_11"].format(app.mention, message.from_user.mention)
            elif "artist" in url:
                try:
                    details, plist_id = await Spotify.artist(url)
                except:
                    return await mystic.edit_text(_["play_3"])
                streamtype = "playlist"
                plist_type = "spartist"
                img = config.SPOTIFY_ARTIST_IMG_URL
                cap = _["play_11"].format(message.from_user.first_name)
            else:
                return await mystic.edit_text(_["play_15"])

        elif await Apple.valid(url):
            if not config.ENABLE_APPLE:
                return await mystic.edit_text(_["play_disabled"].format("Apple Music"))
            if not config.API_URL2 or not config.API_KEY2:
                return await mystic.edit_text(
                    "❌ <b>Apple Music requires API_URL2 & API_KEY2.</b>\n\nPlease configure them in your environment."
                )
            if "playlist" in url:
                try:
                    details, plist_id = await Apple.playlist(url)
                except Exception:
                    return await mystic.edit_text(_["play_3"])
                streamtype = "playlist"
                plist_type = "appleplay"
                img = config.APPLE_IMG_URL
                cap = _["play_11"].format(app.mention, message.from_user.mention)
            elif "album" in url:
                try:
                    details, plist_id = await Apple.album(url)
                except Exception:
                    return await mystic.edit_text(_["play_3"])
                streamtype = "playlist"
                plist_type = "applealbum"
                img = config.APPLE_IMG_URL
                cap = _["play_11"].format(app.mention, message.from_user.mention)
            else:
                try:
                    details, track_id = await Apple.track(url)
                except Exception:
                    return await mystic.edit_text(_["play_3"])
                streamtype = "apple"
                img = details.get("thumb") or config.APPLE_IMG_URL
                cap = _["play_10"].format(details["title"], details["duration_min"])
                try:
                    file_path = await Apple.download(url)
                    if not file_path:
                        return await mystic.edit_text(_["play_dl_failed"].format("Apple Music"))
                    details["file_path"] = file_path
                except Exception:
                    return await mystic.edit_text(_["play_dl_failed"].format("Apple Music"))

        elif await Resso.valid(url):
            return await mystic.edit_text("❌ <b>Resso is no longer supported.</b>")

        elif await SoundCloud.valid(url):
            if not config.ENABLE_SOUNDCLOUD:
                return await mystic.edit_text(_["play_disabled"].format("SoundCloud"))
            if not config.API_URL2 or not config.API_KEY2:
                return await mystic.edit_text(
                    "❌ <b>SoundCloud requires API_URL2 & API_KEY2.</b>\n\nPlease configure them."
                )
            try:
                result = await SoundCloud.track(url)
                if result:
                    details, track_id = result
                else:
                    return await mystic.edit_text(_["play_3"])
            except Exception:
                return await mystic.edit_text(_["play_3"])
            streamtype = "soundcloud_api"
            img = details.get("thumb") or config.SOUNCLOUD_IMG_URL
            cap = _["play_10"].format(details["title"], details["duration_min"])
            try:
                file_path = await SoundCloud.download(url)
                if not file_path:
                    return await mystic.edit_text(_["play_dl_failed"].format("SoundCloud"))
                details["file_path"] = file_path
            except Exception:
                return await mystic.edit_text(_["play_dl_failed"].format("SoundCloud"))

        elif await Deezer.valid(url):
            if not config.ENABLE_DEEZER:
                return await mystic.edit_text(_["play_disabled"].format("Deezer"))
            if not config.API_URL2 or not config.API_KEY2:
                return await mystic.edit_text("❌ Deezer requires API_URL2 & API_KEY2.")
            if "track" in url:
                try:
                    details, track_id = await Deezer.track(url)
                except Exception:
                    return await mystic.edit_text(_["play_3"])
                streamtype = "deezer"
                img = details.get("thumb") or config.DEEZER_IMG_URL
                cap = _["play_10"].format(details["title"], details["duration_min"])
                try:
                    file_path = await Deezer.download(url)
                    if not file_path:
                        return await mystic.edit_text(_["play_dl_failed"].format("Deezer"))
                    details["file_path"] = file_path
                except Exception:
                    return await mystic.edit_text(_["play_dl_failed"].format("Deezer"))
            else:
                try:
                    details, plist_id = await Deezer.playlist(url)
                except Exception:
                    return await mystic.edit_text(_["play_3"])
                streamtype = "playlist"
                plist_type = "deezerplay"
                img = config.DEEZER_IMG_URL
                cap = _["play_11"].format(app.mention, message.from_user.mention)

        elif await Gaana.valid(url):
            if not config.ENABLE_GAANA:
                return await mystic.edit_text(_["play_disabled"].format("Gaana"))
            if not config.API_URL2 or not config.API_KEY2:
                return await mystic.edit_text("❌ Gaana requires API_URL2 & API_KEY2.")
            try:
                result = await Gaana.track(url)
                if result:
                    details, track_id = result
                else:
                    return await mystic.edit_text(_["play_3"])
            except Exception:
                return await mystic.edit_text(_["play_3"])
            streamtype = "gaana"
            img = details.get("thumb") or config.GAANA_IMG_URL
            cap = _["play_10"].format(details["title"], details["duration_min"])
            try:
                file_path = await Gaana.download(url)
                if not file_path:
                    return await mystic.edit_text(_["play_dl_failed"].format("Gaana"))
                details["file_path"] = file_path
            except Exception:
                return await mystic.edit_text(_["play_dl_failed"].format("Gaana"))

        elif await Tidal.valid(url):
            if not config.ENABLE_TIDAL:
                return await mystic.edit_text(_["play_disabled"].format("Tidal"))
            if not config.API_URL2 or not config.API_KEY2:
                return await mystic.edit_text("❌ Tidal requires API_URL2 & API_KEY2.")
            if "track" in url:
                try:
                    details, track_id = await Tidal.track(url)
                except Exception:
                    return await mystic.edit_text(_["play_3"])
                streamtype = "tidal"
                img = details.get("thumb") or config.TIDAL_IMG_URL
                cap = _["play_10"].format(details["title"], details["duration_min"])
                try:
                    file_path = await Tidal.download(url)
                    if not file_path:
                        return await mystic.edit_text(_["play_dl_failed"].format("Tidal"))
                    details["file_path"] = file_path
                except Exception:
                    return await mystic.edit_text(_["play_dl_failed"].format("Tidal"))
            else:
                try:
                    details, plist_id = await Tidal.album(url) if "album" in url else await Tidal.playlist(url)
                except Exception:
                    return await mystic.edit_text(_["play_3"])
                streamtype = "playlist"
                plist_type = "tidalplay"
                img = config.TIDAL_IMG_URL
                cap = _["play_11"].format(app.mention, message.from_user.mention)

        elif await JioSaavn.valid(url):
            if not config.ENABLE_JIOSAAVN:
                return await mystic.edit_text(_["play_disabled"].format("JioSaavn"))
            if not config.API_URL2 or not config.API_KEY2:
                return await mystic.edit_text("❌ JioSaavn requires API_URL2 & API_KEY2.")
            if "song" in url:
                try:
                    details, track_id = await JioSaavn.track(url)
                except Exception:
                    return await mystic.edit_text(_["play_3"])
                streamtype = "jiosaavn"
                img = details.get("thumb") or config.JIOSAAVN_IMG_URL
                cap = _["play_10"].format(details["title"], details["duration_min"])
                try:
                    file_path = await JioSaavn.download(url)
                    if not file_path:
                        return await mystic.edit_text(_["play_dl_failed"].format("JioSaavn"))
                    details["file_path"] = file_path
                except Exception:
                    return await mystic.edit_text(_["play_dl_failed"].format("JioSaavn"))
            else:
                try:
                    details, plist_id = await JioSaavn.playlist(url)
                except Exception:
                    return await mystic.edit_text(_["play_3"])
                streamtype = "playlist"
                plist_type = "jiosaavnplay"
                img = config.JIOSAAVN_IMG_URL
                cap = _["play_11"].format(app.mention, message.from_user.mention)

        elif await Twitch.valid(url):
            if not config.ENABLE_TWITCH:
                return await mystic.edit_text(_["play_disabled"].format("Twitch"))
            if not config.API_URL2 or not config.API_KEY2:
                return await mystic.edit_text("❌ Twitch requires API_URL2 & API_KEY2.")
            try:
                result = await Twitch.track(url)
                if result:
                    details, track_id = result
                else:
                    return await mystic.edit_text(_["play_3"])
            except Exception:
                return await mystic.edit_text(_["play_3"])
            file_path = await Twitch.download(url, video=bool(video))
            if not file_path:
                return await mystic.edit_text(_["play_dl_failed"].format("Twitch"))
            details["file_path"] = file_path
            streamtype = "twitch"
            img = details.get("thumb") or config.TWITCH_IMG_URL
            cap = _["play_10"].format(details["title"], details["duration_min"])

        elif await Kick.valid(url):
            if not config.ENABLE_KICK:
                return await mystic.edit_text(_["play_disabled"].format("Kick"))
            if not config.API_URL2 or not config.API_KEY2:
                return await mystic.edit_text("❌ Kick requires API_URL2 & API_KEY2.")
            try:
                result = await Kick.track(url)
                if result:
                    details, track_id = result
                else:
                    return await mystic.edit_text(_["play_3"])
            except Exception:
                return await mystic.edit_text(_["play_3"])
            file_path = await Kick.download(url, video=bool(video))
            if not file_path:
                return await mystic.edit_text(_["play_dl_failed"].format("Kick"))
            details["file_path"] = file_path
            streamtype = "kick"
            img = details.get("thumb") or config.KICK_IMG_URL
            cap = _["play_10"].format(details["title"], details["duration_min"])

        elif await MXPlayer.valid(url):
            if not config.ENABLE_MXPLAYER:
                return await mystic.edit_text(_["play_disabled"].format("MX Player"))
            if not config.API_URL2 or not config.API_KEY2:
                return await mystic.edit_text("❌ MX Player requires API_URL2 & API_KEY2.")
            try:
                result = await MXPlayer.track(url)
                if result:
                    details, track_id = result
                else:
                    return await mystic.edit_text(_["play_3"])
            except Exception:
                return await mystic.edit_text(_["play_3"])
            file_path = await MXPlayer.download(url, video=bool(video))
            if not file_path:
                return await mystic.edit_text(_["play_dl_failed"].format("MX Player"))
            details["file_path"] = file_path
            streamtype = "mxplayer"
            img = details.get("thumb") or config.MXPLAYER_IMG_URL
            cap = _["play_10"].format(details["title"], details["duration_min"])

        elif DirectMedia.is_valid(url):
            # --- Direct HTTP/HTTPS media URL or M3U8/HLS stream ---
            # Uses the existing "index" streamtype which passes raw URLs
            # directly to pytgcalls/ffmpeg without any download step.
            await mystic.edit_text(_["raw_1"])
            dm_info = await DirectMedia.get_info(url)
            if not dm_info:
                return await mystic.edit_text(_["raw_2"])
            # "index" streamtype in stream.py expects `details` to be the
            # raw URL string (passed as `result`), not a dict.
            details = url
            streamtype = "index"
            img = config.STREAM_IMG_URL if hasattr(config, "STREAM_IMG_URL") else config.YOUTUBE_IMG_URL
            cap = _["raw_3"].format(
                dm_info["title"],
                "M3U8 / Live Stream" if dm_info["is_live"] else "Direct Media",
                dm_info["duration_min"] or "Live",
            )
            # For duration-limit check we need a fake details dict — set it
            # after the stream call so we skip the duration check for index URLs.
            track_id = url
        else:
            return await mystic.edit_text(
                "❌ <b>Link Not Supported</b>\n\n"
                "Could not identify this URL as a valid media source.\n"
                "Supported: YouTube, Spotify, Apple Music, Deezer, Tidal, JioSaavn, "
                "SoundCloud, Gaana, Twitch, Kick, MX Player, Telegram files, M3U8/Direct URLs."
            )

    else:
        if len(message.command) < 2:
            buttons = botplaylist_markup(_)
            return await mystic.edit_text(
                _["play_18"],
                reply_markup=InlineKeyboardMarkup(buttons),
            )
        slider = True
        query = message.text.split(None, 1)[1]
        if "-v" in query:
            query = query.replace("-v", "")
        try:
            details, track_id = await YouTube.track(query)
        except:
            return await mystic.edit_text(_["play_dl_failed"].format("YouTube"))
        streamtype = "youtube"

    if str(playmode) == "Direct":
        if not plist_type:
            # For index/direct-link streamtypes details is a raw URL string; skip dict checks.
            if streamtype not in ("index", "direct_link", "live_stream"):
                duration_sec = time_to_seconds(details["duration_min"])
                if duration_sec > 0:
                    if duration_sec > config.DURATION_LIMIT:
                        return await mystic.edit_text(
                            _["play_6"].format(config.DURATION_LIMIT_MIN, app.mention)
                        )
                else:
                    # duration_sec == 0 means no duration info → live stream
                    buttons = livestream_markup(
                        _,
                        track_id,
                        user_id,
                        "v" if video else "a",
                        "c" if channel else "g",
                        "f" if fplay else "d",
                    )
                    return await mystic.edit_text(
                        _["play_13"],
                        reply_markup=InlineKeyboardMarkup(buttons),
                    )
        try:
            await stream(
                _,
                mystic,
                user_id,
                details,
                chat_id,
                user_name,
                message.chat.id,
                video=video,
                streamtype=streamtype,
                spotify=spotify,
                forceplay=fplay,
            )
        except Exception as e:
            ex_type = type(e).__name__
            err = e if ex_type == "AssistantErr" else _["general_2"].format(ex_type)
            return await mystic.edit_text(err)
        await mystic.delete()
        return await play_logs(message, streamtype=streamtype)
    else:
        if plist_type:
            ran_hash = "".join(
                random.choices(string.ascii_uppercase + string.digits, k=10)
            )
            lyrical[ran_hash] = plist_id
            buttons = playlist_markup(
                _,
                ran_hash,
                message.from_user.id,
                plist_type,
                "c" if channel else "g",
                "f" if fplay else "d",
            )
            await mystic.delete()
            await message.reply_photo(
                photo=img,
                caption=cap,
                reply_markup=InlineKeyboardMarkup(buttons),
            )
            return await play_logs(message, streamtype=f"Playlist : {plist_type}")
        else:
            if slider:
                buttons = slider_markup(
                    _,
                    track_id,
                    message.from_user.id,
                    query,
                    0,
                    "c" if channel else "g",
                    "f" if fplay else "d",
                )
                await mystic.delete()
                await message.reply_photo(
                    photo=details["thumb"],
                    caption=_["play_10"].format(
                        details["title"].title(),
                        details["duration_min"],
                    ),
                    reply_markup=InlineKeyboardMarkup(buttons),
                )
                return await play_logs(message, streamtype=f"Searched on Youtube")
            else:
                buttons = track_markup(
                    _,
                    track_id,
                    message.from_user.id,
                    "c" if channel else "g",
                    "f" if fplay else "d",
                )
                await mystic.delete()
                await message.reply_photo(
                    photo=img,
                    caption=cap,
                    reply_markup=InlineKeyboardMarkup(buttons),
                )
                return await play_logs(message, streamtype=f"URL Searched Inline")


@app.on_callback_query(filters.regex("MusicStream") & ~BANNED_USERS)
@languageCB
async def play_music(client, CallbackQuery, _):
    callback_data = CallbackQuery.data.strip()
    callback_request = callback_data.split(None, 1)[1]
    vidid, user_id, mode, cplay, fplay = callback_request.split("|")
    if CallbackQuery.from_user.id != int(user_id):
        try:
            return await CallbackQuery.answer(_["playcb_1"], show_alert=True)
        except:
            return
    try:
        chat_id, channel = await get_channeplayCB(_, cplay, CallbackQuery)
    except:
        return
    user_name = CallbackQuery.from_user.first_name
    try:
        await CallbackQuery.message.delete()
        await CallbackQuery.answer()
    except:
        pass
    mystic = await CallbackQuery.message.reply_text(
        _["play_2"].format(channel) if channel else _["play_1"]
    )
    try:
        details, track_id = await YouTube.track(vidid, True)
    except:
        return await mystic.edit_text(_["play_dl_failed"].format("YouTube"))
    duration_sec = time_to_seconds(details["duration_min"])
    if duration_sec > 0:
        if duration_sec > config.DURATION_LIMIT:
            return await mystic.edit_text(
                _["play_6"].format(config.DURATION_LIMIT_MIN, app.mention)
            )
    else:
        # duration_sec == 0 means no duration info → live stream
        buttons = livestream_markup(
            _,
            track_id,
            CallbackQuery.from_user.id,
            mode,
            "c" if cplay == "c" else "g",
            "f" if fplay else "d",
        )
        return await mystic.edit_text(
            _["play_13"],
            reply_markup=InlineKeyboardMarkup(buttons),
        )
    video = True if mode == "v" else None
    ffplay = True if fplay == "f" else None
    try:
        await stream(
            _,
            mystic,
            CallbackQuery.from_user.id,
            details,
            chat_id,
            user_name,
            CallbackQuery.message.chat.id,
            video,
            streamtype="youtube",
            forceplay=ffplay,
        )
    except Exception as e:
        ex_type = type(e).__name__
        err = e if ex_type == "AssistantErr" else _["general_2"].format(ex_type)
        return await mystic.edit_text(err)
    return await mystic.delete()


@app.on_callback_query(filters.regex("AnonymousAdmin") & ~BANNED_USERS)
async def anonymous_check(client, CallbackQuery):
    try:
        await CallbackQuery.answer(
            "» ʀᴇᴠᴇʀᴛ ʙᴀᴄᴋ ᴛᴏ ᴜsᴇʀ ᴀᴄᴄᴏᴜɴᴛ :\n\nᴏᴘᴇɴ ʏᴏᴜʀ ɢʀᴏᴜᴘ sᴇᴛᴛɪɴɢs.\n-> ᴀᴅᴍɪɴɪsᴛʀᴀᴛᴏʀs\n-> ᴄʟɪᴄᴋ ᴏɴ ʏᴏᴜʀ ɴᴀᴍᴇ\n-> ᴜɴᴄʜᴇᴄᴋ ᴀɴᴏɴʏᴍᴏᴜs ᴀᴅᴍɪɴ ᴘᴇʀᴍɪssɪᴏɴs.",
            show_alert=True,
        )
    except:
        pass


@app.on_callback_query(filters.regex("AnonyPlaylists") & ~BANNED_USERS)
@languageCB
async def play_playlists_command(client, CallbackQuery, _):
    callback_data = CallbackQuery.data.strip()
    callback_request = callback_data.split(None, 1)[1]
    (
        videoid,
        user_id,
        ptype,
        mode,
        cplay,
        fplay,
    ) = callback_request.split("|")
    if CallbackQuery.from_user.id != int(user_id):
        try:
            return await CallbackQuery.answer(_["playcb_1"], show_alert=True)
        except:
            return
    try:
        chat_id, channel = await get_channeplayCB(_, cplay, CallbackQuery)
    except:
        return
    user_name = CallbackQuery.from_user.first_name
    await CallbackQuery.message.delete()
    try:
        await CallbackQuery.answer()
    except:
        pass
    mystic = await CallbackQuery.message.reply_text(
        _["play_2"].format(channel) if channel else _["play_1"]
    )
    videoid = lyrical.get(videoid)
    video = True if mode == "v" else None
    ffplay = True if fplay == "f" else None
    spotify = True
    if ptype == "yt":
        spotify = False
        try:
            result = await YouTube.playlist(
                videoid,
                config.PLAYLIST_FETCH_LIMIT,
                CallbackQuery.from_user.id,
                True,
            )
        except:
            return await mystic.edit_text(_["play_3"])
    elif ptype == "spplay":
        try:
            result, spotify_id = await Spotify.playlist(videoid)
        except:
            return await mystic.edit_text(_["play_3"])
    elif ptype == "spalbum":
        try:
            result, spotify_id = await Spotify.album(videoid)
        except:
            return await mystic.edit_text(_["play_3"])
    elif ptype == "spartist":
        try:
            result, spotify_id = await Spotify.artist(videoid)
        except:
            return await mystic.edit_text(_["play_3"])
    elif ptype in ("apple", "appleplay", "applealbum"):
        try:
            if ptype == "applealbum":
                result, _ = await Apple.album(videoid)
            else:
                result, _ = await Apple.playlist(videoid, True)
        except:
            return await mystic.edit_text(_["play_3"])
    elif ptype in ("deezerplay", "deezeralbum"):
        if not config.API_URL2 or not config.API_KEY2:
            return await mystic.edit_text("❌ Deezer requires API_URL2 & API_KEY2.")
        try:
            if ptype == "deezeralbum":
                result, _ = await Deezer.album(videoid)
            else:
                result, _ = await Deezer.playlist(videoid)
        except:
            return await mystic.edit_text(_["play_3"])
        spotify = False
    elif ptype in ("tidalplay", "tidalalbum"):
        if not config.API_URL2 or not config.API_KEY2:
            return await mystic.edit_text("❌ Tidal requires API_URL2 & API_KEY2.")
        try:
            if ptype == "tidalalbum":
                result, _ = await Tidal.album(videoid)
            else:
                result, _ = await Tidal.playlist(videoid)
        except:
            return await mystic.edit_text(_["play_3"])
        spotify = False
    elif ptype == "jiosaavnplay":
        if not config.API_URL2 or not config.API_KEY2:
            return await mystic.edit_text("❌ JioSaavn requires API_URL2 & API_KEY2.")
        try:
            result, _ = await JioSaavn.playlist(videoid)
        except:
            return await mystic.edit_text(_["play_3"])
        spotify = False
    else:
        return await mystic.edit_text(_["play_3"])
    try:
        await stream(
            _,
            mystic,
            user_id,
            result,
            chat_id,
            user_name,
            CallbackQuery.message.chat.id,
            video,
            streamtype="playlist",
            spotify=spotify,
            forceplay=ffplay,
        )
    except Exception as e:
        ex_type = type(e).__name__
        err = e if ex_type == "AssistantErr" else _["general_2"].format(ex_type)
        return await mystic.edit_text(err)
    return await mystic.delete()


@app.on_callback_query(filters.regex("slider") & ~BANNED_USERS)
@languageCB
async def slider_queries(client, CallbackQuery, _):
    callback_data = CallbackQuery.data.strip()
    callback_request = callback_data.split(None, 1)[1]
    (
        what,
        rtype,
        query,
        user_id,
        cplay,
        fplay,
    ) = callback_request.split("|")
    if CallbackQuery.from_user.id != int(user_id):
        try:
            return await CallbackQuery.answer(_["playcb_1"], show_alert=True)
        except:
            return
    what = str(what)
    rtype = int(rtype)
    if what == "F":
        if rtype == 9:
            query_type = 0
        else:
            query_type = int(rtype + 1)
        try:
            await CallbackQuery.answer(_["playcb_2"])
        except:
            pass
        title, duration_min, thumbnail, vidid = await YouTube.slider(query, query_type)
        buttons = slider_markup(_, vidid, user_id, query, query_type, cplay, fplay)
        med = InputMediaPhoto(
            media=thumbnail,
            caption=_["play_10"].format(
                title.title(),
                duration_min,
            ),
        )
        return await CallbackQuery.edit_message_media(
            media=med, reply_markup=InlineKeyboardMarkup(buttons)
        )
    if what == "B":
        if rtype == 0:
            query_type = 9
        else:
            query_type = int(rtype - 1)
        try:
            await CallbackQuery.answer(_["playcb_2"])
        except:
            pass
        title, duration_min, thumbnail, vidid = await YouTube.slider(query, query_type)
        buttons = slider_markup(_, vidid, user_id, query, query_type, cplay, fplay)
        med = InputMediaPhoto(
            media=thumbnail,
            caption=_["play_10"].format(
                title.title(),
                duration_min,
            ),
        )
        return await CallbackQuery.edit_message_media(
            media=med, reply_markup=InlineKeyboardMarkup(buttons)
        )
