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
    response = await message.reply_text("🏓 Pong!")
    pytgping = await Anony.ping()
    UP, CPU, RAM, DISK = await bot_sys_stats()
    resp = round((datetime.now() - start).total_seconds() * 1000, 3)
    await response.edit_text(
        f"<b>🏓 Pong!</b>  <code>{resp} ms</code>\n\n"
        f"<b>⚙️ Bot:</b> {app.mention}\n\n"
        f"<b>📊 System:</b>\n"
        f"  • Uptime: <code>{UP}</code>\n"
        f"  • CPU: <code>{CPU}</code>\n"
        f"  • RAM: <code>{RAM}</code>\n"
        f"  • Disk: <code>{DISK}</code>\n\n"
        f"<b>📞 PyTgCalls:</b> <code>{pytgping} ms</code>",
        disable_web_page_preview=True,
    )
