# ╔══════════════════════════════════════════════════════════════════╗
# ║        Copyright © tusar404 — All Rights Reserved               ║
# ║     AnonXMusic · Telegram Music Bot · Powered by PyTgCalls      ║
# ║        Unauthorized copying or distribution is prohibited        ║
# ╚══════════════════════════════════════════════════════════════════╝

from pyrogram import filters
from pyrogram.enums import ParseMode
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

import config
from AnonXMusic import app

_PRIVACY_TEXT = """
<b>Privacy Policy</b>

{mention} is a Telegram voice chat music bot. This policy explains what data we handle and how.

<b>Data we collect</b>
We store only the minimum required to operate:
- Chat IDs and user IDs, to serve the bot's features (language preferences, admin lists, play queues).
- Play history, limited to what is needed for queue and loop functionality.

We do <b>not</b> store message text, media, voice recordings, or any personal information beyond identifiers.

<b>How data is used</b>
Collected identifiers are used solely to provide bot functionality within Telegram. They are never sold, rented, or shared with any third party.

<b>Data retention</b>
You can remove the bot from a chat at any time. Upon removal, associated chat data is no longer actively used. You may contact support to request deletion of stored data for your chat or account.

<b>Third-party services</b>
The bot uses external APIs to resolve and stream audio (YouTube, Spotify, etc.). Those services have their own privacy policies; we do not control what they collect.

<b>Security</b>
All communication runs over Telegram's encrypted infrastructure. We do not store credentials or payment information.

<b>Contact</b>
For questions or data removal requests, reach us via the support chat below.
"""

@app.on_message(filters.command("privacy"))
async def privacy(_, message: Message):
    text = _PRIVACY_TEXT.format(mention=app.mention).strip()
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("Support", url=config.SUPPORT_CHAT),
    ]])
    await message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
        reply_markup=keyboard,
    )
