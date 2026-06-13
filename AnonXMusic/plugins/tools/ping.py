from datetime import datetime

from pyrogram import filters
from pyrogram.types import Message

from AnonXMusic import app
from AnonXMusic.core.call import Anony
from AnonXMusic.utils import bot_sys_stats
from AnonXMusic.utils.decorators.language import language
from config import BANNED_USERS


@app.on_message(filters.command(["ping", "alive"]) & ~BANNED_USERS)
@language
async def ping_com(client, message: Message, _):
    start = datetime.now()
    response = await message.reply_text(_["ping_1"])
    pytgping = await Anony.ping()
    UP, CPU, RAM, DISK = await bot_sys_stats()
    resp = round((datetime.now() - start).total_seconds() * 1000, 3)
    await response.edit_text(
        _["ping_2"].format(resp, UP, CPU, RAM, DISK, pytgping),
        disable_web_page_preview=True,
    )

