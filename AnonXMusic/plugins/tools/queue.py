import asyncio
import os

from pyrogram import filters
from pyrogram.errors import FloodWait
from pyrogram.types import CallbackQuery, InputMediaPhoto, Message

import config
from AnonXMusic import app
from AnonXMusic.misc import db
from AnonXMusic.utils import AnonyBin, get_channeplayCB, seconds_to_min
from AnonXMusic.utils.database import get_cmode, is_active_chat, is_music_playing
from AnonXMusic.utils.decorators.language import language, languageCB
from AnonXMusic.utils.inline import queue_back_markup, queue_markup
from config import BANNED_USERS

basic = {}


def get_image(videoid):
    if os.path.isfile(f"cache/{videoid}.png"):
        return f"cache/{videoid}.png"
    else:
        return config.YOUTUBE_IMG_URL


def get_duration(playing):
    file_path = playing[0]["file"]
    if "index_" in file_path or "live_" in file_path:
        return "Unknown"
    duration_seconds = int(playing[0]["seconds"])
    if duration_seconds == 0:
        return "Unknown"
    else:
        return "Inline"


@app.on_message(
    filters.command(["queue", "cqueue", "player", "cplayer", "playing", "cplaying"])
    & filters.group
    & ~BANNED_USERS
)
@language
async def get_queue(client, message: Message, _):
    if message.command[0][0] == "c":
        chat_id = await get_cmode(message.chat.id)
        if chat_id is None:
            return await message.reply_text(_["setting_7"])
        try:
            await app.get_chat(chat_id)
        except:
            return await message.reply_text(_["cplay_4"])
        cplay = True
    else:
        chat_id = message.chat.id
        cplay = False
    if not await is_active_chat(chat_id):
        return await message.reply_text(_["general_5"])
    got = db.get(chat_id)
    if not got:
        return await message.reply_text(_["queue_2"])
    file = got[0]["file"]
    videoid = got[0]["vidid"]
    user = got[0]["by"]
    title = (got[0]["title"]).title()
    typo = (got[0]["streamtype"]).title()
    DUR = get_duration(got)
    if "live_" in file:
        IMAGE = get_image(videoid)
    elif "vid_" in file:
        IMAGE = get_image(videoid)
    elif "index_" in file:
        IMAGE = config.STREAM_IMG_URL
    else:
        if videoid == "telegram":
            IMAGE = (
                config.TELEGRAM_AUDIO_URL
                if typo == "Audio"
                else config.TELEGRAM_VIDEO_URL
            )
        elif videoid == "soundcloud":
            IMAGE = config.SOUNCLOUD_IMG_URL
        else:
            IMAGE = get_image(videoid)
    send = _["queue_6"] if DUR == "Unknown" else _["queue_7"]
    cap = _["queue_8"].format(app.mention, title, typo, user, send)
    upl = (
        queue_markup(_, DUR, "c" if cplay else "g", videoid)
        if DUR == "Unknown"
        else queue_markup(
            _,
            DUR,
            "c" if cplay else "g",
            videoid,
            seconds_to_min(got[0]["played"]),
            got[0]["dur"],
        )
    )
    basic[videoid] = True
    mystic = await message.reply_photo(IMAGE, caption=cap, reply_markup=upl)
    if DUR != "Unknown":
        try:
            while db[chat_id][0]["vidid"] == videoid:
                await asyncio.sleep(5)
                if await is_active_chat(chat_id):
                    if basic[videoid]:
                        if await is_music_playing(chat_id):
                            try:
                                buttons = queue_markup(
                                    _,
                                    DUR,
                                    "c" if cplay else "g",
                                    videoid,
                                    seconds_to_min(db[chat_id][0]["played"]),
                                    db[chat_id][0]["dur"],
                                )
                                await mystic.edit_reply_markup(reply_markup=buttons)
                            except FloodWait:
                                pass
                        else:
                            pass
                    else:
                        break
                else:
                    break
        except:
            return


@app.on_callback_query(filters.regex("GetTimer") & ~BANNED_USERS)
async def quite_timer(client, CallbackQuery: CallbackQuery):
    try:
        await CallbackQuery.answer()
    except:
        pass


@app.on_callback_query(filters.regex("GetQueued") & ~BANNED_USERS)
@languageCB
async def queued_tracks(client, CallbackQuery: CallbackQuery, _):
    callback_data = CallbackQuery.data.strip()
    callback_request = callback_data.split(None, 1)[1]
    what, videoid = callback_request.split("|")
    try:
        chat_id, channel = await get_channeplayCB(_, what, CallbackQuery)
    except:
        return
    if not await is_active_chat(chat_id):
        return await CallbackQuery.answer(_["general_5"], show_alert=True)
    got = db.get(chat_id)
    if not got:
        return await CallbackQuery.answer(_["queue_2"], show_alert=True)
    if len(got) == 1:
        return await CallbackQuery.answer(_["queue_5"], show_alert=True)
    await CallbackQuery.answer()
    basic[videoid] = False
    buttons = queue_back_markup(_, what)
    med = InputMediaPhoto(
        media="https://telegra.ph//file/6f7d35131f69951c74ee5.jpg",
        caption=_["queue_1"],
    )
    await CallbackQuery.edit_message_media(media=med)
    j = 0
    msg = ""
    for x in got:
        j += 1
        if j == 1:
            msg += f'Streaming :\n\n✨ Title : {x["title"]}\nDuration : {x["dur"]}\nBy : {x["by"]}\n\n'
        elif j == 2:
            msg += f'Queued :\n\n✨ Title : {x["title"]}\nDuration : {x["dur"]}\nBy : {x["by"]}\n\n'
        else:
            msg += f'✨ Title : {x["title"]}\nDuration : {x["dur"]}\nBy : {x["by"]}\n\n'
    if "Queued" in msg:
        if len(msg) < 700:
            await asyncio.sleep(1)
            return await CallbackQuery.edit_message_text(msg, reply_markup=buttons)
        if "✨" in msg:
            msg = msg.replace("✨", "")
        link = await AnonyBin(msg)
        med = InputMediaPhoto(media=link, caption=_["queue_3"].format(link))
        await CallbackQuery.edit_message_media(media=med, reply_markup=buttons)
    else:
        await asyncio.sleep(1)
        return await CallbackQuery.edit_message_text(msg, reply_markup=buttons)


@app.on_callback_query(filters.regex("queue_back_timer") & ~BANNED_USERS)
@languageCB
async def queue_back(client, CallbackQuery: CallbackQuery, _):
    callback_data = CallbackQuery.data.strip()
    cplay = callback_data.split(None, 1)[1]
    try:
        chat_id, channel = await get_channeplayCB(_, cplay, CallbackQuery)
    except:
        return
    if not await is_active_chat(chat_id):
        return await CallbackQuery.answer(_["general_5"], show_alert=True)
    got = db.get(chat_id)
    if not got:
        return await CallbackQuery.answer(_["queue_2"], show_alert=True)
    await CallbackQuery.answer(_["set_cb_5"], show_alert=True)
    file = got[0]["file"]
    videoid = got[0]["vidid"]
    user = got[0]["by"]
    title = (got[0]["title"]).title()
    typo = (got[0]["streamtype"]).title()
    DUR = get_duration(got)
    if "live_" in file:
        IMAGE = get_image(videoid)
    elif "vid_" in file:
        IMAGE = get_image(videoid)
    elif "index_" in file:
        IMAGE = config.STREAM_IMG_URL
    else:
        if videoid == "telegram":
            IMAGE = (
                config.TELEGRAM_AUDIO_URL
                if typo == "Audio"
                else config.TELEGRAM_VIDEO_URL
            )
        elif videoid == "soundcloud":
            IMAGE = config.SOUNCLOUD_IMG_URL
        else:
            IMAGE = get_image(videoid)
    send = _["queue_6"] if DUR == "Unknown" else _["queue_7"]
    cap = _["queue_8"].format(app.mention, title, typo, user, send)
    upl = (
        queue_markup(_, DUR, cplay, videoid)
        if DUR == "Unknown"
        else queue_markup(
            _,
            DUR,
            cplay,
            videoid,
            seconds_to_min(got[0]["played"]),
            got[0]["dur"],
        )
    )
    basic[videoid] = True

    med = InputMediaPhoto(media=IMAGE, caption=cap)
    mystic = await CallbackQuery.edit_message_media(media=med, reply_markup=upl)
    if DUR != "Unknown":
        try:
            while db[chat_id][0]["vidid"] == videoid:
                await asyncio.sleep(5)
                if await is_active_chat(chat_id):
                    if basic[videoid]:
                        if await is_music_playing(chat_id):
                            try:
                                buttons = queue_markup(
                                    _,
                                    DUR,
                                    cplay,
                                    videoid,
                                    seconds_to_min(db[chat_id][0]["played"]),
                                    db[chat_id][0]["dur"],
                                )
                                await mystic.edit_reply_markup(reply_markup=buttons)
                            except FloodWait:
                                pass
                        else:
                            pass
                    else:
                        break
                else:
                    break
        except:
            return


# ════════════════════════════════════════════════════════════════════════════
#  /remove <position>  —  Delete a queued track without stopping playback
# ════════════════════════════════════════════════════════════════════════════

@app.on_message(
    filters.command(["remove", "fremove"]) & filters.group & ~BANNED_USERS
)
@language
async def remove_from_queue(client, message: Message, _):
    from AnonXMusic.misc import SUDOERS
    from AnonXMusic.utils.database import get_authuser_names, is_active_chat
    from AnonXMusic.utils.formatters import int_to_alpha
    from AnonXMusic.utils.stream.autoclear import auto_clean
    from config import adminlist

    chat_id = message.chat.id

    if not await is_active_chat(chat_id):
        return await message.reply_text(_["general_5"])

    # ── parse position arg ────────────────────────────────────────────────
    if len(message.command) < 2 or not message.command[1].isnumeric():
        return await message.reply_text(_["remove_1"])

    pos = int(message.command[1])

    # ── admin / auth-user gate ─────────────────────────────────────────────
    if message.from_user.id not in SUDOERS:
        admins = adminlist.get(chat_id, [])
        if message.from_user.id not in admins:
            try:
                auth_users = await get_authuser_names(chat_id)
                token = await int_to_alpha(message.from_user.id)
                is_auth = token in auth_users
            except Exception:
                is_auth = False
            if not is_auth:
                return await message.reply_text(_["remove_5"])

    # ── guard: position 1 = currently playing ─────────────────────────────
    if pos == 1:
        return await message.reply_text(_["remove_2"])

    # ── validate range ─────────────────────────────────────────────────────
    queue = db.get(chat_id)
    if not queue:
        return await message.reply_text(_["queue_2"])

    total = len(queue)
    if pos < 1 or pos > total:
        return await message.reply_text(_["remove_3"].format(pos, total))

    # ── remove it ─────────────────────────────────────────────────────────
    removed = queue.pop(pos - 1)   # list is 0-indexed; pos 1 = index 0
    await auto_clean(removed)

    title    = removed.get("title", "Unknown").title()
    duration = removed.get("dur", "?")
    by       = removed.get("by", "Unknown")

    await message.reply_text(_["remove_4"].format(pos, title, duration, by))


# ════════════════════════════════════════════════════════════════════════════
#  Related tracks  —  callback when user taps a suggestion button
# ════════════════════════════════════════════════════════════════════════════

@app.on_callback_query(filters.regex(r"^RelatedAdd ") & ~BANNED_USERS)
@languageCB
async def related_add_callback(client, CallbackQuery: CallbackQuery, _):
    from AnonXMusic.misc import SUDOERS
    from AnonXMusic.utils.database import (
        get_authuser_names, get_playtype, is_active_chat,
    )
    from AnonXMusic.utils.formatters import int_to_alpha
    from AnonXMusic.utils.stream.queue import put_queue
    from AnonXMusic.utils.inline import stream_markup
    from AnonXMusic.utils.thumbnails import get_thumb
    from AnonXMusic.core.call import Anony
    from AnonXMusic import YouTube
    from config import adminlist
    from pyrogram.types import InlineKeyboardMarkup

    # parse callback data: "RelatedAdd <vid>|<chat_id>"
    try:
        payload          = CallbackQuery.data.strip().split(None, 1)[1]
        vidid, chat_id_s = payload.split("|")
        chat_id          = int(chat_id_s)
    except Exception:
        return await CallbackQuery.answer(_["general_2"].format("Bad data"), show_alert=True)

    user_id   = CallbackQuery.from_user.id
    user_name = CallbackQuery.from_user.mention

    # ── playtype / admin check ────────────────────────────────────────────
    playty = await get_playtype(chat_id)
    if playty != "Everyone":
        if user_id not in SUDOERS:
            admins = adminlist.get(chat_id, [])
            if user_id not in admins:
                try:
                    auth_users = await get_authuser_names(chat_id)
                    token      = await int_to_alpha(user_id)
                    is_auth    = token in auth_users
                except Exception:
                    is_auth = False
                if not is_auth:
                    return await CallbackQuery.answer(_["related_4"], show_alert=True)

    await CallbackQuery.answer(_["related_5"])

    # ── delete the suggestions message right away ─────────────────────────
    try:
        await CallbackQuery.message.delete()
    except Exception:
        pass

    # ── fetch track details ───────────────────────────────────────────────
    try:
        track, _ = await YouTube.track(
            f"https://www.youtube.com/watch?v={vidid}", videoid=False
        )
        title    = track.get("title", "Unknown").title()
        duration = track.get("duration_min", "0:00")
        link     = track.get("link", f"https://www.youtube.com/watch?v={vidid}")
    except Exception:
        return await app.send_message(
            CallbackQuery.message.chat.id, _["related_6"]
        )

    is_active = await is_active_chat(chat_id)
    file_path = f"vid_{vidid}"

    if is_active:
        # ── already streaming — queue it ──────────────────────────────────
        await put_queue(
            chat_id          = chat_id,
            original_chat_id = CallbackQuery.message.chat.id,
            file             = file_path,
            title            = title,
            duration         = duration,
            user             = user_name,
            vidid            = vidid,
            user_id          = user_id,
            stream           = "audio",
        )
        pos = len(db.get(chat_id, []))
        await app.send_message(
            CallbackQuery.message.chat.id,
            _["related_2"].format(title, duration, pos, user_name),
        )
    else:
        # ── nothing playing — start playback ──────────────────────────────
        await put_queue(
            chat_id          = chat_id,
            original_chat_id = CallbackQuery.message.chat.id,
            file             = file_path,
            title            = title,
            duration         = duration,
            user             = user_name,
            vidid            = vidid,
            user_id          = user_id,
            stream           = "audio",
        )
        try:
            await Anony.join_call(
                chat_id          = chat_id,
                original_chat_id = CallbackQuery.message.chat.id,
                link             = file_path,
                video            = False,
            )
            img    = await get_thumb(vidid)
            button = stream_markup(_, chat_id)
            run = await app.send_photo(
                chat_id,
                photo        = img,
                caption      = _["related_3"].format(title, duration, user_name),
                reply_markup = InlineKeyboardMarkup(button),
            )
            db[chat_id][0]["mystic"] = run
            db[chat_id][0]["markup"] = "stream"
        except Exception as e:
            await app.send_message(
                CallbackQuery.message.chat.id,
                _["general_2"].format(str(e)),
            )
