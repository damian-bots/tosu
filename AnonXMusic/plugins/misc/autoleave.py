import asyncio
from contextlib import suppress
from datetime import datetime, timedelta

from pyrogram.enums import ChatType
import pytz

import config
from AnonXMusic import app
from AnonXMusic.core.call import Anony, autoend
from AnonXMusic.utils.database import get_client, is_active_chat, is_autoend

IST = pytz.timezone("Asia/Kolkata")

# Only exclude the configured logger, not hardcoded channel IDs
EXCLUDED_CHATS = {config.LOGGER_ID}

# Per-assistant limit to avoid FloodWait
MAX_LEAVE_PER_ASSISTANT = 200
# Delay between leaves (seconds) — prevents FloodWait
LEAVE_DELAY = 1.5
# Hour/minute for daily auto-leave (IST)
_SCHEDULE_HOUR   = 4
_SCHEDULE_MINUTE = 30


def get_next_run_time() -> datetime:
    """Get exact next run time for the scheduled hour in IST."""
    now    = datetime.now(IST)
    target = now.replace(hour=_SCHEDULE_HOUR, minute=_SCHEDULE_MINUTE, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    return target


async def auto_leave():
    """Leave inactive chats on schedule (daily at 4:30 AM IST)."""
    if not config.AUTO_LEAVING_ASSISTANT:
        return

    while True:
        next_run     = get_next_run_time()
        now          = datetime.now(IST)
        wait_seconds = int((next_run - now).total_seconds())

        if wait_seconds > 0:
            await asyncio.sleep(wait_seconds)

        from AnonXMusic.core.userbot import assistants

        for num in assistants:
            try:
                client = await get_client(num)
                left   = 0

                async for dialog in client.get_dialogs():
                    if left >= MAX_LEAVE_PER_ASSISTANT:
                        break
                    if dialog.chat.type not in [
                        ChatType.SUPERGROUP, ChatType.GROUP, ChatType.CHANNEL
                    ]:
                        continue
                    if dialog.chat.id in EXCLUDED_CHATS:
                        continue
                    if await is_active_chat(dialog.chat.id):
                        continue

                    with suppress(Exception):
                        await client.leave_chat(dialog.chat.id)
                        left += 1
                        await asyncio.sleep(LEAVE_DELAY)

            except Exception:
                pass


async def auto_end():
    """Auto end videochats with no listeners."""
    while True:
        await asyncio.sleep(5)

        if not await is_autoend():
            continue

        for chat_id in list(autoend.keys()):
            timer = autoend.get(chat_id)
            if not timer:
                continue
            if datetime.now() <= timer:
                continue
            if not await is_active_chat(chat_id):
                autoend.pop(chat_id, None)
                continue

            autoend.pop(chat_id, None)

            with suppress(Exception):
                await Anony.stop_stream(chat_id)
            with suppress(Exception):
                await app.send_message(
                    chat_id,
                    "» ʙᴏᴛ ᴀᴜᴛᴏᴍᴀᴛɪᴄᴀʟʟʏ ʟᴇғᴛ ᴠɪᴅᴇᴏᴄʜᴀᴛ ʙᴇᴄᴀᴜsᴇ ɴᴏ ᴏɴᴇ ᴡᴀs ʟɪsᴛᴇɴɪɴɢ.",
                )


# Start background tasks safely (avoid creating tasks before event loop is running)
asyncio.ensure_future(auto_leave())
asyncio.ensure_future(auto_end())
