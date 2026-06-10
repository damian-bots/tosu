# ╔══════════════════════════════════════════════════════════════════╗
# ║        Copyright © tusar404 — All Rights Reserved               ║
# ║     AnonXMusic · Telegram Music Bot · Powered by PyTgCalls      ║
# ║        Unauthorized copying or distribution is prohibited        ║
# ╚══════════════════════════════════════════════════════════════════╝

import asyncio
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

MAX_LEAVE_PER_ASSISTANT = 200


def get_next_run_time() -> datetime:
    """Get the exact next run time for 4:30 AM IST."""
    now = datetime.now(IST)
    
    target = now.replace(hour=13, minute=45, second=0, microsecond=0)
    
    if now >= target:
        target += timedelta(days=1)
        
    return target


async def auto_leave():
    """Leave inactive chats exactly at 4:30 AM IST daily."""
    if not config.AUTO_LEAVING_ASSISTANT:
        return
    
    while True:
        next_run = get_next_run_time()
        now = datetime.now(IST)
        wait_seconds = int((next_run - now).total_seconds())
        
        if wait_seconds > 0:
            await asyncio.sleep(wait_seconds)
        
        from AnonXMusic.core.userbot import assistants
        
        for num in assistants:
            try:
                client = await get_client(num)
                left = 0
                
                async for dialog in client.get_dialogs():
                    if left >= MAX_LEAVE_PER_ASSISTANT:
                        break
                        
                    if dialog.chat.type not in [ChatType.SUPERGROUP, ChatType.GROUP, ChatType.CHANNEL]:
                        continue
                    if dialog.chat.id in EXCLUDED_CHATS:
                        continue
                    if await is_active_chat(dialog.chat.id):
                        continue
                        
                    with suppress(Exception):
                        await client.leave_chat(dialog.chat.id)
                        left += 1
                        await asyncio.sleep(1.5)
                        
            except Exception as e:
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


asyncio.create_task(auto_leave())
asyncio.create_task(auto_end())
