import asyncio
import logging
import os
from random import randint
from typing import Union

from pyrogram.types import InlineKeyboardMarkup, InputMediaPhoto

import config
from AnonXMusic import (
    Carbon, YouTube, app,
    Deezer, Gaana, JioSaavn, Kick, MXPlayer, Tidal, Twitch,
    Apple, Spotify, SoundCloud,
)
from AnonXMusic.core.call import Anony
from AnonXMusic.misc import db
from AnonXMusic.utils.database import add_active_video_chat, is_active_chat
from AnonXMusic.utils.error_logger import error_logger
from AnonXMusic.utils.exceptions import AssistantErr
from AnonXMusic.utils.inline import aq_markup, close_markup, stream_markup
from AnonXMusic.utils.pastebin import AnonyBin
from AnonXMusic.utils.stream.queue import put_queue, put_queue_index
from AnonXMusic.utils.thumbnails import get_thumb

LOGGER = logging.getLogger(__name__)


async def _edit_or_send_photo(mystic, app, original_chat_id, photo, caption, button):
    """
    Convert the existing text message (mystic) into a Now Playing photo card.

    Rules (matching TgMusicBot behaviour):
    - First track: edit mystic text → photo via edit_message_media.
      If edit fails (message too old, already a photo, etc.) → send_photo fallback.
    - Subsequent tracks (next in queue): always send a new photo message.
      This is handled by change_stream() in call.py which calls app.send_photo
      directly, not this helper.  This helper is only called from stream.py for
      the *first* track.

    Returns the resulting message object (used as db[chat_id][0]["mystic"]).
    """
    run = None
    # Only attempt to edit if mystic is a text message (not already a photo card).
    # A photo-edited mystic has _photo_edited=True set below; if already set we
    # skip the edit and send fresh — prevents double-editing on repeated calls.
    already_used = getattr(mystic, "_photo_edited", False) if mystic else True
    if mystic is not None and not already_used:
        try:
            run = await mystic.edit_message_media(
                media=InputMediaPhoto(media=photo, caption=caption),
                reply_markup=InlineKeyboardMarkup(button),
            )
            # Mark so we know this mystic was already converted to a photo
            try:
                mystic._photo_edited = True
            except Exception:
                pass
        except Exception:
            run = None
    if run is None:
        run = await app.send_photo(
            original_chat_id,
            photo=photo,
            caption=caption,
            reply_markup=InlineKeyboardMarkup(button),
        )
    return run


@error_logger(label="Stream Handler")
async def stream(
    _,
    mystic,
    user_id,
    result,
    chat_id,
    user_name,
    original_chat_id,
    video: Union[bool, str] = None,
    streamtype: Union[bool, str] = None,
    spotify: Union[bool, str] = None,
    forceplay: Union[bool, str] = None,
):
    if not result:
        return
    if forceplay:
        await Anony.force_stop_stream(chat_id)
    if streamtype == "playlist":
        import time as _time
        import traceback as _tb
        from pyrogram.errors import FloodWait
        from AnonXMusic.utils.formatters import seconds_to_min

        # ── Throttled progress editor ─────────────────────────────────────────
        PROGRESS_INTERVAL = 4.0
        _last_edit_ts: float = 0.0

        async def _progress(text: str) -> None:
            nonlocal _last_edit_ts
            now = _time.monotonic()
            if now - _last_edit_ts < PROGRESS_INTERVAL:
                return
            for _attempt in range(2):
                try:
                    await mystic.edit_text(text, disable_web_page_preview=True)
                    _last_edit_ts = _time.monotonic()
                    return
                except FloodWait as fw:
                    wait = fw.value + 1
                    LOGGER.warning(f"Playlist progress: FloodWait {wait}s — sleeping")
                    await asyncio.sleep(wait)
                    _last_edit_ts = _time.monotonic()
                except Exception:
                    return

        # ── Step 1: Collect raw track list ────────────────────────────────────
        # Tracks from API-2 /api/get_url already carry full metadata:
        #   { title, id, url, thumbnail, duration, platform, _ready=True }
        # These are processed instantly — no YouTube search needed.
        # spotipy dicts have _source="spotipy" and need YT resolve.

        raw_total   = len(result) if result else 0
        valid_tracks = []
        skipped      = 0

        await _progress(
            f"⏳ <b>Processing {raw_total} track(s)…</b>\n"
            f"<i>Resolving metadata, please wait…</i>"
        )

        for idx, raw_track in enumerate(result or []):
            if len(valid_tracks) >= config.PLAYLIST_FETCH_LIMIT:
                break

            if isinstance(raw_track, dict) and raw_track.get("title"):
                t_title        = raw_track.get("title") or "Unknown"
                t_duration_sec = int(raw_track.get("duration") or raw_track.get("duration_sec") or 0)
                t_thumbnail    = raw_track.get("thumbnail") or ""
                t_vidid        = raw_track.get("id") or raw_track.get("vidid") or ""
                t_platform     = (raw_track.get("platform") or "").lower()
                t_direct_url   = raw_track.get("url") or raw_track.get("link") or ""
                t_source       = raw_track.get("_source") or ""
                t_ready        = raw_track.get("_ready", False)  # set by ApiPlatform.playlist()

                # API-2 tracks with full metadata: title, url, platform, duration all present.
                # These come from /api/get_url and are _ready — queue immediately, download on play.
                if t_ready and t_direct_url and t_platform and t_platform not in ("youtube", ""):
                    if t_duration_sec > 0 and t_duration_sec > config.DURATION_LIMIT:
                        skipped += 1
                        continue
                    t_duration_min = seconds_to_min(t_duration_sec) if t_duration_sec > 0 else "0:00"
                    valid_tracks.append({
                        "title":        t_title,
                        "duration_min": t_duration_min,
                        "duration_sec": t_duration_sec,
                        "thumbnail":    t_thumbnail,
                        "vidid":        t_vidid,
                        "platform":     t_platform,
                        "direct_url":   t_direct_url,
                        "file_ref":     t_direct_url,   # use the track URL directly for API download
                    })
                    continue

                # spotipy tracks: no CDN URL — must be resolved via YouTube search
                if t_source == "spotipy" or not t_direct_url or t_platform == "":
                    t_platform   = "spotipy_yt"
                    t_file_ref   = f"yt_search_{t_title}"
                else:
                    if not t_title:
                        skipped += 1
                        continue
                    if t_duration_sec > 0 and t_duration_sec > config.DURATION_LIMIT:
                        skipped += 1
                        continue
                    if t_direct_url and t_platform and t_platform not in ("youtube", ""):
                        t_file_ref = t_direct_url
                    else:
                        t_file_ref = f"vid_{t_vidid}" if t_vidid else t_direct_url

                t_duration_min = seconds_to_min(t_duration_sec) if t_duration_sec > 0 else "0:00"

                valid_tracks.append({
                    "title":        t_title,
                    "duration_min": t_duration_min,
                    "duration_sec": t_duration_sec,
                    "thumbnail":    t_thumbnail,
                    "vidid":        t_vidid,
                    "platform":     t_platform,
                    "direct_url":   t_direct_url,
                    "file_ref":     t_file_ref,
                })

            elif isinstance(raw_track, str) and raw_track.strip():
                valid_tracks.append({
                    "title":        raw_track,
                    "duration_min": "0:00",
                    "duration_sec": 0,
                    "thumbnail":    "",
                    "vidid":        "",
                    "platform":     "yt_search",
                    "direct_url":   "",
                    "file_ref":     f"yt_search_{raw_track}",
                })
            else:
                skipped += 1

        total = len(valid_tracks)
        if total == 0:
            await _progress("❌ <b>No valid tracks found in the playlist.</b>")
            return

        # ── Step 2: Show track list immediately ───────────────────────────────
        track_list_preview = ""
        for i, t in enumerate(valid_tracks[:15], 1):
            track_list_preview += f"{i}. {t['title'][:55]}\n"
        if total > 15:
            track_list_preview += f"… and {total - 15} more"

        try:
            await mystic.edit_text(
                f"🎵 <b>Found {total} song(s)</b>"
                + (f" <i>({skipped} skipped)</i>" if skipped else "")
                + f"\n\n<blockquote expandable>{track_list_preview}</blockquote>\n"
                f"⏳ <i>Queuing tracks…</i>",
                disable_web_page_preview=True,
            )
            _last_edit_ts = _time.monotonic()
        except FloodWait as fw:
            await asyncio.sleep(fw.value + 1)
        except Exception:
            pass

        # ── Step 3: Download & queue one by one ───────────────────────────────
        count  = 0
        failed = 0
        msg    = f"{_['play_19']}\n\n"
        status = True if video else None

        for idx, track_data in enumerate(valid_tracks):
            title        = track_data["title"]
            duration_min = track_data["duration_min"]
            duration_sec = track_data["duration_sec"]
            thumbnail    = track_data["thumbnail"]
            vidid        = track_data["vidid"]
            platform     = track_data["platform"]
            file_ref     = track_data["file_ref"]
            thumb        = thumbnail

            await _progress(
                f"🎵 <b>Playlist — {count}/{total} queued</b>\n\n"
                f"⬇️ Processing <b>{title[:60]}</b>…"
            )

            # ── Tracks that need a YouTube search ─────────────────────────────
            needs_yt_resolve = (
                platform in ("spotipy_yt", "yt_search", "youtube", "")
                or file_ref.startswith("yt_search_")
                or (file_ref.startswith("vid_") and not vidid)
            )

            if needs_yt_resolve:
                search_query = title
                try:
                    (
                        t_title,
                        t_duration_min,
                        t_duration_sec,
                        t_thumb,
                        t_vidid,
                    ) = await YouTube.details(search_query, False if spotify else True)
                except Exception as exc:
                    LOGGER.warning(
                        f"Playlist: YouTube details failed for {title!r}: {exc}"
                    )
                    failed += 1
                    continue

                if str(t_duration_min) == "None" or t_duration_sec > config.DURATION_LIMIT:
                    failed += 1
                    continue

                title        = t_title
                duration_min = t_duration_min
                duration_sec = t_duration_sec
                thumbnail    = t_thumb or thumb
                vidid        = t_vidid
                file_ref     = f"vid_{t_vidid}"
                platform     = "youtube"

            # ── API-2 CDN URLs for non-YouTube platforms ──────────────────────
            # Only download if we need to START playing NOW (chat not active yet).
            # If already active → queue the track URL and download on change_stream.
            elif (
                file_ref.startswith("http")
                and platform
                and platform not in ("youtube", "")
            ):
                # For queueing: store the URL directly — downloaded on play
                # For starting first track: download immediately
                if not await is_active_chat(chat_id):
                    try:
                        if platform == "spotify":
                            resolved = await Spotify.download(file_ref)
                        else:
                            from AnonXMusic.platforms.Api import ApiPlatform
                            resolved = await ApiPlatform().download(file_ref)
                        if resolved:
                            if not os.path.isabs(resolved) and not resolved.startswith("http"):
                                resolved = os.path.join(os.getcwd(), resolved)
                            file_ref = resolved
                        else:
                            LOGGER.warning(
                                f"Playlist: API-2 download None for {title!r} — "
                                f"trying YouTube fallback"
                            )
                            try:
                                (
                                    t_title, t_duration_min, t_duration_sec,
                                    t_thumb, t_vidid,
                                ) = await YouTube.details(title, True)
                                if str(t_duration_min) != "None":
                                    title        = t_title
                                    duration_min = t_duration_min
                                    vidid        = t_vidid
                                    file_ref     = f"vid_{t_vidid}"
                                    platform     = "youtube"
                                else:
                                    failed += 1
                                    continue
                            except Exception:
                                failed += 1
                                continue
                    except Exception as exc:
                        LOGGER.error(
                            f"Playlist: API-2 error for {title!r}:\n"
                            + "".join(_tb.format_exception(type(exc), exc, exc.__traceback__))
                        )
                        failed += 1
                        continue
                # else: already active → keep file_ref as the platform URL,
                #       change_stream will handle downloading it when needed

            # ── Queue or start ────────────────────────────────────────────────
            try:
                if await is_active_chat(chat_id):
                    await put_queue(
                        chat_id,
                        original_chat_id,
                        file_ref,
                        title,
                        duration_min,
                        user_name,
                        vidid,
                        user_id,
                        "video" if video else "audio",
                    )
                    position = len(db.get(chat_id)) - 1
                    count += 1
                    msg += f"{count}. {title[:70]}\n"
                    msg += f"{_['play_20']} {position}\n\n"
                else:
                    if not forceplay:
                        db[chat_id] = []

                    if file_ref.startswith("vid_"):
                        yt_id = file_ref[4:]
                        try:
                            file_path, direct = await YouTube.download(
                                yt_id, mystic, video=status, videoid=True
                            )
                        except Exception as exc:
                            LOGGER.error(
                                f"Playlist: YT download error for {title!r}:\n"
                                + "".join(_tb.format_exception(type(exc), exc, exc.__traceback__))
                            )
                            file_path = None
                            direct    = False
                    else:
                        file_path = file_ref
                        direct    = True

                    if not file_path:
                        raise AssistantErr(_["play_dl_no_audio"])

                    await Anony.join_call(
                        chat_id,
                        original_chat_id,
                        file_path,
                        video=status,
                        image=thumb,
                    )
                    await put_queue(
                        chat_id,
                        original_chat_id,
                        file_path if direct else f"vid_{vidid}",
                        title,
                        duration_min,
                        user_name,
                        vidid,
                        user_id,
                        "video" if video else "audio",
                        forceplay=forceplay,
                    )
                    img = (
                        await get_thumb(vidid)
                        if file_ref.startswith("vid_")
                        else (thumb or config.SPOTIFY_PLAYLIST_IMG_URL)
                    )
                    button  = stream_markup(_, chat_id)
                    caption = _["stream_1"].format(
                        f"https://t.me/{app.username}?start=info_{vidid}",
                        title[:23],
                        duration_min,
                        user_name,
                    )
                    run = await _edit_or_send_photo(mystic, app, original_chat_id, img, caption, button)
                    db[chat_id][0]["mystic"] = run
                    db[chat_id][0]["markup"] = "stream"
                    count += 1
                    msg += f"{count}. {title[:70]}\n"
                    msg += f"{_['play_20']} 0\n\n"

            except AssistantErr:
                raise
            except Exception as exc:
                LOGGER.error(
                    f"Playlist: queue/stream error for {title!r}:\n"
                    + "".join(_tb.format_exception(type(exc), exc, exc.__traceback__))
                )
                failed += 1
                continue

        if count == 0:
            try:
                await mystic.edit_text(
                    "❌ <b>Playlist failed</b>\n\n"
                    f"Could not queue any tracks. {failed} track(s) failed to resolve.",
                    disable_web_page_preview=True,
                )
            except Exception:
                pass
            return
        else:
            link  = await AnonyBin(msg)
            lines = msg.count("\n")
            if lines >= 17:
                car = os.linesep.join(msg.split(os.linesep)[:17])
            else:
                car = msg
            carbon = await Carbon.generate(car, randint(100, 10000000))
            upl    = close_markup(_)
            return await app.send_photo(
                original_chat_id,
                photo=carbon,
                caption=_["play_21"].format(count, link),
                reply_markup=upl,
            )

    elif streamtype == "youtube":
        link         = result["link"]
        vidid        = result["vidid"]
        title        = (result["title"]).title()
        duration_min = result["duration_min"]
        thumbnail    = result["thumb"]
        status       = True if video else None
        try:
            file_path, direct = await YouTube.download(
                vidid, mystic, videoid=True, video=status
            )
        except Exception as exc:
            import traceback as _tb
            LOGGER.error(
                f"YouTube download failed for vidid={vidid!r}:\n"
                + "".join(_tb.format_exception(type(exc), exc, exc.__traceback__))
            )
            file_path = None
            direct    = False
        if not file_path:
            return await mystic.edit_text(
                _["play_dl_no_audio"],
                disable_web_page_preview=True,
            )
        if direct and not os.path.isfile(file_path):
            return await mystic.edit_text(
                _["play_dl_failed"].format("YouTube"),
                disable_web_page_preview=True,
            )
        if await is_active_chat(chat_id):
            await put_queue(
                chat_id,
                original_chat_id,
                file_path if direct else f"vid_{vidid}",
                title,
                duration_min,
                user_name,
                vidid,
                user_id,
                "video" if video else "audio",
            )
            position = len(db.get(chat_id)) - 1
            button   = aq_markup(_, chat_id)
            run_q = await mystic.edit_text(
                text=_["queue_4"].format(position, title[:27], duration_min, user_name),
                reply_markup=InlineKeyboardMarkup(button),
                disable_web_page_preview=True,
            )
        else:
            if not forceplay:
                db[chat_id] = []
            await Anony.join_call(
                chat_id,
                original_chat_id,
                file_path,
                video=status,
                image=thumbnail,
            )
            await put_queue(
                chat_id,
                original_chat_id,
                file_path if direct else f"vid_{vidid}",
                title,
                duration_min,
                user_name,
                vidid,
                user_id,
                "video" if video else "audio",
                forceplay=forceplay,
            )
            img     = await get_thumb(vidid)
            button  = stream_markup(_, chat_id)
            caption = _["stream_1"].format(
                f"https://youtube.com/watch?v={vidid}",
                title[:23],
                duration_min,
                user_name,
            )
            run = await _edit_or_send_photo(mystic, app, original_chat_id, img, caption, button)
            db[chat_id][0]["mystic"] = run
            db[chat_id][0]["markup"] = "stream"

    elif streamtype == "soundcloud":
        file_path    = result["filepath"]
        title        = result["title"]
        duration_min = result["duration_min"]
        if not file_path or not os.path.isfile(file_path):
            return await mystic.edit_text(
                _["play_dl_failed"].format("SoundCloud"),
                disable_web_page_preview=True,
            )
        if await is_active_chat(chat_id):
            await put_queue(
                chat_id,
                original_chat_id,
                file_path,
                title,
                duration_min,
                user_name,
                streamtype,
                user_id,
                "audio",
            )
            position = len(db.get(chat_id)) - 1
            button   = aq_markup(_, chat_id)
            run_q = await mystic.edit_text(
                text=_["queue_4"].format(position, title[:27], duration_min, user_name),
                reply_markup=InlineKeyboardMarkup(button),
                disable_web_page_preview=True,
            )
        else:
            if not forceplay:
                db[chat_id] = []
            await Anony.join_call(chat_id, original_chat_id, file_path, video=None)
            await put_queue(
                chat_id,
                original_chat_id,
                file_path,
                title,
                duration_min,
                user_name,
                streamtype,
                user_id,
                "audio",
                forceplay=forceplay,
            )
            button  = stream_markup(_, chat_id)
            caption = _["stream_1"].format(
                config.SUPPORT_CHAT, title[:23], duration_min, user_name
            )
            run = await _edit_or_send_photo(
                mystic, app, original_chat_id, config.SOUNCLOUD_IMG_URL, caption, button
            )
            db[chat_id][0]["mystic"] = run
            db[chat_id][0]["markup"] = "tg"

    # ── API-2 powered platforms ───────────────────────────────────────────
    elif streamtype in (
        "spotify", "apple", "deezer", "gaana", "tidal",
        "jiosaavn", "soundcloud_api", "twitch", "kick", "mxplayer",
    ):
        file_path    = result.get("file_path") or result.get("filepath") or ""
        title        = (result.get("title") or "Unknown").title()
        duration_min = result.get("duration_min") or "0:00"
        thumbnail    = result.get("thumb") or result.get("thumbnail") or ""
        link         = result.get("link") or ""

        if not file_path or (not file_path.startswith("http") and not os.path.isfile(file_path)):
            return await mystic.edit_text(
                _["play_dl_failed"].format(streamtype.replace("_api", "").title()),
                disable_web_page_preview=True,
            )

        _img_map = {
            "spotify":        config.SPOTIFY_PLAYLIST_IMG_URL,
            "apple":          config.APPLE_IMG_URL,
            "deezer":         config.DEEZER_IMG_URL,
            "gaana":          config.GAANA_IMG_URL,
            "tidal":          config.TIDAL_IMG_URL,
            "jiosaavn":       config.JIOSAAVN_IMG_URL,
            "soundcloud_api": config.SOUNCLOUD_IMG_URL,
            "twitch":         config.TWITCH_IMG_URL,
            "kick":           config.KICK_IMG_URL,
            "mxplayer":       config.MXPLAYER_IMG_URL,
        }
        cover_img        = thumbnail or _img_map.get(streamtype, config.PLAYLIST_IMG_URL)
        is_video_stream  = streamtype in ("twitch", "kick", "mxplayer")
        status           = True if (video or is_video_stream) else None

        if await is_active_chat(chat_id):
            await put_queue(
                chat_id, original_chat_id, file_path, title, duration_min,
                user_name, streamtype, user_id,
                "video" if status else "audio",
                thumbnail=cover_img,
            )
            position = len(db.get(chat_id)) - 1
            button   = aq_markup(_, chat_id)
            run_q = await mystic.edit_text(
                text=_["queue_4"].format(position, title[:27], duration_min, user_name),
                reply_markup=InlineKeyboardMarkup(button),
                disable_web_page_preview=True,
            )
        else:
            if not forceplay:
                db[chat_id] = []
            await Anony.join_call(
                chat_id, original_chat_id, file_path, video=status, image=cover_img
            )
            await put_queue(
                chat_id, original_chat_id, file_path, title, duration_min,
                user_name, streamtype, user_id,
                "video" if status else "audio",
                forceplay=forceplay,
                thumbnail=cover_img,
            )
            button  = stream_markup(_, chat_id)
            caption = _["stream_1"].format(
                link or config.SUPPORT_CHAT, title[:23], duration_min, user_name
            )
            run = await _edit_or_send_photo(mystic, app, original_chat_id, cover_img, caption, button)
            db[chat_id][0]["mystic"] = run
            db[chat_id][0]["markup"] = "tg"

    elif streamtype == "telegram":
        file_path    = result["path"]
        link         = result["link"]
        title        = (result["title"]).title()
        duration_min = result["dur"]
        status       = True if video else None
        if not file_path or not os.path.isfile(file_path):
            return await mystic.edit_text(
                _["play_dl_failed"].format("Telegram"),
                disable_web_page_preview=True,
            )
        if await is_active_chat(chat_id):
            await put_queue(
                chat_id,
                original_chat_id,
                file_path,
                title,
                duration_min,
                user_name,
                streamtype,
                user_id,
                "video" if video else "audio",
            )
            position = len(db.get(chat_id)) - 1
            button   = aq_markup(_, chat_id)
            run_q = await mystic.edit_text(
                text=_["queue_4"].format(position, title[:27], duration_min, user_name),
                reply_markup=InlineKeyboardMarkup(button),
                disable_web_page_preview=True,
            )
        else:
            if not forceplay:
                db[chat_id] = []
            await Anony.join_call(chat_id, original_chat_id, file_path, video=status)
            await put_queue(
                chat_id,
                original_chat_id,
                file_path,
                title,
                duration_min,
                user_name,
                streamtype,
                user_id,
                "video" if video else "audio",
                forceplay=forceplay,
            )
            if video:
                await add_active_video_chat(chat_id)
            button  = stream_markup(_, chat_id)
            photo   = config.TELEGRAM_VIDEO_URL if video else config.TELEGRAM_AUDIO_URL
            caption = _["stream_1"].format(link, title[:23], duration_min, user_name)
            run = await _edit_or_send_photo(mystic, app, original_chat_id, photo, caption, button)
            db[chat_id][0]["mystic"] = run
            db[chat_id][0]["markup"] = "tg"

    elif streamtype == "live":
        link         = result["link"]
        vidid        = result["vidid"]
        title        = (result["title"]).title()
        thumbnail    = result["thumb"]
        duration_min = "Live Track"
        status       = True if video else None
        if await is_active_chat(chat_id):
            await put_queue(
                chat_id,
                original_chat_id,
                f"live_{vidid}",
                title,
                duration_min,
                user_name,
                vidid,
                user_id,
                "video" if video else "audio",
            )
            position = len(db.get(chat_id)) - 1
            button   = aq_markup(_, chat_id)
            run_q = await mystic.edit_text(
                text=_["queue_4"].format(position, title[:27], duration_min, user_name),
                reply_markup=InlineKeyboardMarkup(button),
                disable_web_page_preview=True,
            )
        else:
            if not forceplay:
                db[chat_id] = []
            n, file_path = await YouTube.video(link)
            if n == 0:
                raise AssistantErr(_["str_3"])
            await Anony.join_call(
                chat_id,
                original_chat_id,
                file_path,
                video=status,
                image=thumbnail if thumbnail else None,
            )
            await put_queue(
                chat_id,
                original_chat_id,
                f"live_{vidid}",
                title,
                duration_min,
                user_name,
                vidid,
                user_id,
                "video" if video else "audio",
                forceplay=forceplay,
            )
            img     = await get_thumb(vidid)
            button  = stream_markup(_, chat_id)
            caption = _["stream_1"].format(
                f"https://youtube.com/watch?v={vidid}",
                title[:23],
                duration_min,
                user_name,
            )
            run = await _edit_or_send_photo(mystic, app, original_chat_id, img, caption, button)
            db[chat_id][0]["mystic"] = run
            db[chat_id][0]["markup"] = "tg"

    elif streamtype == "index":
        link         = result
        title        = "ɪɴᴅᴇx ᴏʀ ᴍ3ᴜ8 ʟɪɴᴋ"
        duration_min = "00:00"
        if await is_active_chat(chat_id):
            await put_queue_index(
                chat_id,
                original_chat_id,
                "index_url",
                title,
                duration_min,
                user_name,
                link,
                "video" if video else "audio",
            )
            position = len(db.get(chat_id)) - 1
            button   = aq_markup(_, chat_id)
            await mystic.edit_text(
                text=_["queue_4"].format(position, title[:27], duration_min, user_name),
                reply_markup=InlineKeyboardMarkup(button),
            )
        else:
            if not forceplay:
                db[chat_id] = []
            await Anony.join_call(
                chat_id,
                original_chat_id,
                link,
                video=True if video else None,
            )
            await put_queue_index(
                chat_id,
                original_chat_id,
                "index_url",
                title,
                duration_min,
                user_name,
                link,
                "video" if video else "audio",
                forceplay=forceplay,
            )
            button  = stream_markup(_, chat_id)
            caption = _["stream_2"].format(user_name)
            run = await _edit_or_send_photo(
                mystic, app, original_chat_id, config.STREAM_IMG_URL, caption, button
            )
            db[chat_id][0]["mystic"] = run
            db[chat_id][0]["markup"] = "tg"
