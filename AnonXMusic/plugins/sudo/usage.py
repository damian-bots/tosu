from pyrogram import filters
from pyrogram.types import Message

import config
from AnonXMusic import app
from AnonXMusic.misc import SUDOERS
from AnonXMusic.utils.database import get_api_usage

@app.on_message(filters.command("usage") & SUDOERS)
async def api_usage(_, message: Message):
    msg = await message.reply_text("Fetching usage stats...")
    try:
        data = await get_api_usage()
    except Exception as e:
        return await msg.edit_text(f"Failed to fetch usage data: {e}")

    at1 = data["alltime"]["api_1"]
    at2 = data["alltime"]["api_2"]
    d1  = data["daily"]["api_1"]
    d2  = data["daily"]["api_2"]
    m1  = data["monthly"]["api_1"]
    m2  = data["monthly"]["api_2"]

    text = (
        f"<b>API Usage — {app.mention}</b>\n\n"
        f"<b>API-1</b>  <code>{config.API_URL}</code>\n"
        f"  Today    : <code>{d1:,}</code>\n"
        f"  This month: <code>{m1:,}</code>\n"
        f"  All time : <code>{at1:,}</code>\n\n"
        f"<b>API-2</b>  <code>{config.API_URL2}</code>\n"
        f"  Today    : <code>{d2:,}</code>\n"
        f"  This month: <code>{m2:,}</code>\n"
        f"  All time : <code>{at2:,}</code>\n\n"
        f"<b>Combined</b>\n"
        f"  Today    : <code>{d1 + d2:,}</code>\n"
        f"  This month: <code>{m1 + m2:,}</code>\n"
        f"  All time : <code>{at1 + at2:,}</code>"
    )

    await msg.edit_text(text, disable_web_page_preview=True)
