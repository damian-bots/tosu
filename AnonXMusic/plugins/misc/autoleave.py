import asyncio
import random
from datetime import datetime, timedelta
from contextlib import suppress

from pyrogram.enums import ChatType
import pytz

import config
from AnonXMusic import app
from AnonXMusic.core.call import Anony, autoend
from AnonXMusic.utils.database import get_client, is_active_chat, is_autoend

IST = pytz.timezone("Asia/Kolkata")

EXCLUDED_CHATS = {
    config.LOGGER_ID,
    -1001686672798,
    -1001549206010,
}

MAX_LEAVE_PER_RUN = 80


def get_next_run_time() -> datetime:
    """Get random time between 4-6 AM IST tomorrow."""
    now = datetime.now(IST)
    
    random_minutes = random.randint(0, 120)
    
    tomorrow = now + timedelta(days=1)
    target = tomorrow.replace(hour=4, minute=0, second=0, microsecond=0)
    target += timedelta(minutes=random_minutes)
    
    return target


async def auto_leave():
    """Leave inactive chats at random time between 4-6 AM IST daily."""
    if not config.AUTO_LEAVING_ASSISTANT:
        return
    
    while True:
        # Calculate next run time
        next_run = get_next_run_time()
        now = datetime.now(IST)
        wait_seconds = int((next_run - now).total_seconds())
        
        # Wait until scheduled time
        await asyncio.sleep(wait_seconds)
        
        # Get assistants
        from AnonXMusic.core.userbot import assistants
        
        for num in assistants:
            left = 0
            with suppress(Exception):
                client = await get_client(num)
                async for dialog in client.get_dialogs():
                    if dialog.chat.type not in [ChatType.SUPERGROUP, ChatType.GROUP, ChatType.CHANNEL]:
                        continue
                    if dialog.chat.id in EXCLUDED_CHATS:
                        continue
                    if left >= MAX_LEAVE_PER_RUN:
                        break
                    if await is_active_chat(dialog.chat.id):
                        continue
                    with suppress(Exception):
                        await client.leave_chat(dialog.chat.id)
                        left += 1


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


# Start background tasks
asyncio.create_task(auto_leave())
asyncio.create_task(auto_end())
