# AnonXMusic · utils/inline/help.py
# Help panel — music-related buttons only.

from typing import Union
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from AnonXMusic import app


def help_pannel(_, START: Union[bool, int] = None):
    # Main Help panel: Back button goes to Home (start_back_helper), NO Close button.
    home_btn = InlineKeyboardButton(text=_["HOME_BUTTON"], callback_data="start_back_helper")

    upl = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(text=_["H_B_1"],  callback_data="help_callback hb1"),   # Admin
            InlineKeyboardButton(text=_["H_B_2"],  callback_data="help_callback hb2"),   # Auth
            InlineKeyboardButton(text=_["H_B_6"],  callback_data="help_callback hb6"),   # C-Play
        ],
        [
            InlineKeyboardButton(text=_["H_B_8"],  callback_data="help_callback hb8"),   # Loop
            InlineKeyboardButton(text=_["H_B_10"], callback_data="help_callback hb10"),  # Ping
            InlineKeyboardButton(text=_["H_B_11"], callback_data="help_callback hb11"),  # Play
        ],
        [
            InlineKeyboardButton(text=_["H_B_12"], callback_data="help_callback hb12"),  # Shuffle
            InlineKeyboardButton(text=_["H_B_13"], callback_data="help_callback hb13"),  # Seek
            InlineKeyboardButton(text=_["H_B_15"], callback_data="help_callback hb15"),  # Speed
        ],
        [home_btn],
    ])
    return upl


def help_back_markup(_):
    """Sub-help sections keep both Back and Close buttons."""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton(text=_["BACK_BUTTON"], callback_data="settings_back_helper"),
        InlineKeyboardButton(text=_["CLOSE_BUTTON"], callback_data="close"),
    ]])


def private_help_panel(_):
    return [[
        InlineKeyboardButton(
            text=_["S_B_4"],
            url=f"https://t.me/{app.username}?start=help",
        ),
    ]]
