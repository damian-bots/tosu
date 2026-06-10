# ╔══════════════════════════════════════════════════════════════════╗
# ║        Copyright © tusar404 — All Rights Reserved               ║
# ║     AnonXMusic · Telegram Music Bot · Powered by PyTgCalls      ║
# ║        Unauthorized copying or distribution is prohibited        ║
# ╚══════════════════════════════════════════════════════════════════╝

import inspect
from typing import Union

from pyrogram import filters, types
from pyrogram.types import InlineKeyboardMarkup, Message

from AnonXMusic import app
from AnonXMusic.utils import help_pannel
from AnonXMusic.utils.database import get_lang
from AnonXMusic.utils.decorators.language import LanguageStart, languageCB
from AnonXMusic.utils.inline.help import help_back_markup, private_help_panel
from config import BANNED_USERS, START_IMG_URL, SUPPORT_CHAT
from strings import get_string, helpers

_EFFECT_SUPPORTED = "effect_id" in inspect.signature(
    __import__("pyrogram").types.Message.reply_photo
).parameters


@app.on_message(filters.command(["help"]) & filters.private & ~BANNED_USERS)
@app.on_callback_query(filters.regex("settings_back_helper") & ~BANNED_USERS)
async def helper_private(
    client: app, update: Union[types.Message, types.CallbackQuery]
):
    is_cb = isinstance(update, types.CallbackQuery)
    if is_cb:
        try:
            await update.answer()
        except Exception:
            pass
        chat_id  = update.message.chat.id
        language = await get_lang(chat_id)
        _        = get_string(language)
        keyboard = help_pannel(_, True)
        await update.edit_message_text(_["help_1"].format(SUPPORT_CHAT), reply_markup=keyboard)
    else:
        try:
            await update.delete()
        except Exception:
            pass
        language = await get_lang(update.chat.id)
        _        = get_string(language)
        keyboard = help_pannel(_)
        kwargs   = dict(
            photo=START_IMG_URL,
            caption=_["help_1"].format(SUPPORT_CHAT),
            reply_markup=keyboard,
        )
        if _EFFECT_SUPPORTED:
            kwargs["effect_id"] = 5159385139981059251
        await update.reply_photo(**kwargs)


@app.on_message(filters.command(["help"]) & filters.group & ~BANNED_USERS)
@LanguageStart
async def help_com_group(client, message: Message, _):
    keyboard = private_help_panel(_)
    await message.reply_text(_["help_2"], reply_markup=InlineKeyboardMarkup(keyboard))


@app.on_callback_query(filters.regex("help_callback") & ~BANNED_USERS)
@languageCB
async def helper_cb(client, CallbackQuery, _):
    cb_data = CallbackQuery.data.strip()
    cb      = cb_data.split(None, 1)[1]
    keyboard = help_back_markup(_)

    _pages = {
        "hb1":  helpers.HELP_1,   # Admin
        "hb2":  helpers.HELP_2,   # Auth
        "hb6":  helpers.HELP_6,   # Channel Play
        "hb8":  helpers.HELP_8,   # Loop
        "hb10": helpers.HELP_10,  # Ping & Stats
        "hb11": helpers.HELP_11,  # Play
        "hb12": helpers.HELP_12,  # Shuffle
        "hb13": helpers.HELP_13,  # Seek
        "hb15": helpers.HELP_15,  # Speed
    }

    text = _pages.get(cb)
    if text:
        await CallbackQuery.edit_message_text(text, reply_markup=keyboard)
    else:
        await CallbackQuery.answer("Section not available.", show_alert=True)
