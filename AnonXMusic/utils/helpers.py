# ╔══════════════════════════════════════════════════════════════════╗
# ║        Copyright © tusar404 — All Rights Reserved               ║
# ║     AnonXMusic · Telegram Music Bot · Powered by PyTgCalls      ║
# ║        Unauthorized copying or distribution is prohibited        ║
# ╚══════════════════════════════════════════════════════════════════╝

from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import config
from AnonXMusic import YouTube, app
from AnonXMusic.core.call import Anony
from AnonXMusic.misc import SUDOERS, db
from AnonXMusic.utils.database import is_active_chat, is_nonadmin_chat
from AnonXMusic.utils.inline import close_markup, stream_markup
from AnonXMusic.utils.thumbnails import get_thumb
from config import (
    BANNED_USERS,
    SOUNCLOUD_IMG_URL,
    STREAM_IMG_URL,
    SUPPORT_CHAT,
    TELEGRAM_AUDIO_URL,
    TELEGRAM_VIDEO_URL,
    adminlist,
)


def format_stream_error(e, _):
    ex_type = type(e).__name__
    if ex_type == "AssistantErr":
        return e
    return _["general_2"].format(ex_type)


async def check_callback_user(CallbackQuery, user_id, _):
    if CallbackQuery.from_user.id != int(user_id):
        try:
            await CallbackQuery.answer(_["playcb_1"], show_alert=True)
        except:
            pass
        return False
    return True


async def check_callback_admin(CallbackQuery, chat_id, _):
    is_non_admin = await is_nonadmin_chat(CallbackQuery.message.chat.id)
    if not is_non_admin:
        if CallbackQuery.from_user.id not in SUDOERS:
            admins = adminlist.get(CallbackQuery.message.chat.id)
            if not admins:
                await CallbackQuery.answer(_["admin_13"], show_alert=True)
                return False
            else:
                if CallbackQuery.from_user.id not in admins:
                    await CallbackQuery.answer(_["admin_14"], show_alert=True)
                    return False
    return True


def get_anonymous_admin_markup():
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    text="\u02dc\u1d0f\u1d21 \u1d1b\u1d0f \u0262\u026a\u1d21 ?",
                    callback_data="AnonymousAdmin",
                ),
            ]
        ]
    )


async def resolve_skip_stream(
    chat_id, check, _, reply_func, reply_photo_func, extra_text=None
):
    queued = check[0]["file"]
    title = (check[0]["title"]).title()
    user = check[0]["by"]
    duration = check[0]["dur"]
    streamtype = check[0]["streamtype"]
    videoid = check[0]["vidid"]
    status = True if str(streamtype) == "video" else None
    db[chat_id][0]["played"] = 0
    exis = (check[0]).get("old_dur")
    if exis:
        db[chat_id][0]["dur"] = exis
        db[chat_id][0]["seconds"] = check[0]["old_second"]
        db[chat_id][0]["speed_path"] = None
        db[chat_id][0]["speed"] = 1.0

    if "live_" in queued:
        n, link = await YouTube.video(videoid, True)
        if n == 0:
            await reply_func(_["admin_7"].format(title))
            return
        try:
            image = await YouTube.thumbnail(videoid, True)
        except:
            image = None
        try:
            await Anony.skip_stream(chat_id, link, video=status, image=image)
        except:
            await reply_func(_["call_6"])
            return
        button = stream_markup(_, chat_id)
        img = await get_thumb(videoid)
        run = await reply_photo_func(
            photo=img,
            caption=_["stream_1"].format(
                f"https://t.me/{app.username}?start=info_{videoid}",
                title[:23],
                duration,
                user,
            ),
            reply_markup=InlineKeyboardMarkup(button),
        )
        db[chat_id][0]["mystic"] = run
        db[chat_id][0]["markup"] = "tg"
    elif "vid_" in queued:
        mystic = await reply_func(
            _["call_7"], disable_web_page_preview=True
        )
        try:
            file_path, direct = await YouTube.download(
                videoid,
                mystic,
                videoid=True,
                video=status,
            )
        except:
            await mystic.edit_text(_["call_6"])
            return
        try:
            image = await YouTube.thumbnail(videoid, True)
        except:
            image = None
        try:
            await Anony.skip_stream(chat_id, file_path, video=status, image=image)
        except:
            await mystic.edit_text(_["call_6"])
            return
        button = stream_markup(_, chat_id)
        img = await get_thumb(videoid)
        run = await reply_photo_func(
            photo=img,
            caption=_["stream_1"].format(
                f"https://t.me/{app.username}?start=info_{videoid}",
                title[:23],
                duration,
                user,
            ),
            reply_markup=InlineKeyboardMarkup(button),
        )
        db[chat_id][0]["mystic"] = run
        db[chat_id][0]["markup"] = "stream"
        await mystic.delete()
    elif "index_" in queued:
        try:
            await Anony.skip_stream(chat_id, videoid, video=status)
        except:
            await reply_func(_["call_6"])
            return
        button = stream_markup(_, chat_id)
        run = await reply_photo_func(
            photo=STREAM_IMG_URL,
            caption=_["stream_2"].format(user),
            reply_markup=InlineKeyboardMarkup(button),
        )
        db[chat_id][0]["mystic"] = run
        db[chat_id][0]["markup"] = "tg"
    else:
        if videoid == "telegram":
            image = None
        elif videoid == "soundcloud":
            image = None
        else:
            try:
                image = await YouTube.thumbnail(videoid, True)
            except:
                image = None
        try:
            await Anony.skip_stream(chat_id, queued, video=status, image=image)
        except:
            await reply_func(_["call_6"])
            return
        if videoid == "telegram":
            button = stream_markup(_, chat_id)
            run = await reply_photo_func(
                photo=TELEGRAM_AUDIO_URL
                if str(streamtype) == "audio"
                else TELEGRAM_VIDEO_URL,
                caption=_["stream_1"].format(
                    SUPPORT_CHAT, title[:23], duration, user
                ),
                reply_markup=InlineKeyboardMarkup(button),
            )
            db[chat_id][0]["mystic"] = run
            db[chat_id][0]["markup"] = "tg"
        elif videoid == "soundcloud":
            button = stream_markup(_, chat_id)
            run = await reply_photo_func(
                photo=SOUNCLOUD_IMG_URL
                if str(streamtype) == "audio"
                else TELEGRAM_VIDEO_URL,
                caption=_["stream_1"].format(
                    SUPPORT_CHAT, title[:23], duration, user
                ),
                reply_markup=InlineKeyboardMarkup(button),
            )
            db[chat_id][0]["mystic"] = run
            db[chat_id][0]["markup"] = "tg"
        else:
            button = stream_markup(_, chat_id)
            img = await get_thumb(videoid)
            run = await reply_photo_func(
                photo=img,
                caption=_["stream_1"].format(
                    f"https://t.me/{app.username}?start=info_{videoid}",
                    title[:23],
                    duration,
                    user,
                ),
                reply_markup=InlineKeyboardMarkup(button),
            )
            db[chat_id][0]["mystic"] = run
            db[chat_id][0]["markup"] = "stream"

    if extra_text:
        await extra_text()
