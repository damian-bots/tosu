from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from pyrogram.enums import ButtonStyle

import config
from AnonXMusic import app


def start_panel(_):
    buttons = [
        [
            InlineKeyboardButton(
                text=_["S_B_1"],
                url=f"https://t.me/{app.username}?startgroup=true",
            ),
            InlineKeyboardButton(text=_["S_B_9"], url=config.SUPPORT_CHAT),
        ],
    ]
    return buttons


def private_panel(_):
    buttons = [
        [
            InlineKeyboardButton(
                text=_["S_B_3"],
                url=f"https://t.me/{app.username}?startgroup=true",
                style=ButtonStyle.PRIMARY,
            )
        ],
        [InlineKeyboardButton(text=_["S_B_4"], callback_data="settings_back_helper")],
        [
            InlineKeyboardButton(text=_["S_B_9"], url=config.SUPPORT_CHAT),
            InlineKeyboardButton(text=_["S_B_6"], url=config.SUPPORT_CHANNEL),
        ],
        [
            InlineKeyboardButton(text=_["S_B_7"], callback_data="setup_guide_helper"),
        ],
    ]
    return buttons


def guide_back_markup(_):
    buttons = [
        [
            InlineKeyboardButton(text=_["BACK_BUTTON"], callback_data="start_back_helper"),
            InlineKeyboardButton(text=_["CLOSE_BUTTON"], callback_data="close"),
        ],
    ]
    return InlineKeyboardMarkup(buttons)


def welcome_panel(_):
    """Shown when bot is added to a group.
    - Setup Guide button
    - Language button (opens full language panel, Close only — no Back)
    """
    buttons = [
        [
            InlineKeyboardButton(text=_["S_B_7"], callback_data="setup_guide_helper"),
            InlineKeyboardButton(text=_["S_B_LANG"], callback_data="LG"),
        ],
    ]
    return buttons
