import os
import asyncio
from typing import Optional
from datetime import datetime

from pyrogram import filters, errors, types
from pyrogram.types import Message

from config import LOGGER_ID
from AnonXMusic import app
from AnonXMusic.misc import SUDOERS
from AnonXMusic.utils.database import update_bot_stats, get_bot_stats, get_served_chats

# Import the new Chart Generator
from AnonXMusic.utils.chart import generate_stats_image

BOT_INFO: Optional[types.User] = None
BOT_ID: Optional[int] = None
LEFT_ID = -1003499984720

async def _ensure_bot_info() -> None:
    global BOT_INFO, BOT_ID
    if BOT_INFO is None:
        try:
            BOT_INFO = await app.get_me()
            BOT_ID = BOT_INFO.id
        except Exception as e:
            print(f"Failed to get bot info: {e}")

async def safe_send_message(chat_id, text, reply_markup=None, max_retries: int = 3):
    for attempt in range(max_retries):
        try:
            return await app.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=reply_markup
            )
        except errors.FloodWait as e:
            await asyncio.sleep(e.value + 1)
        except Exception as e:
            if attempt == max_retries - 1:
                print(f"Failed to send message after {max_retries} attempts: {e}")
                raise
            await asyncio.sleep(1)


@app.on_message(filters.command("data") & SUDOERS)
async def chat_stats_command(_, message: Message):
    msg = await message.reply_text("🎨 Generating Statistics Chart...")
    
    # 1. Fetch Data
    try:
        stats = await get_bot_stats()
        total_chats = len(await get_served_chats())
    except Exception as e:
        return await msg.edit_text(f"❌ Failed to fetch database statistics: {e}")

    # 2. Try to generate and send the Image Chart
    try:
        loop = asyncio.get_running_loop()
        image_path = await loop.run_in_executor(None, generate_stats_image, stats, total_chats)
        
        await message.reply_photo(
            photo=image_path,
            caption=f"**Bot Growth Statistics**\nTotal Active Chats: `{total_chats}`"
        )
        await msg.delete()
        
        if os.path.exists(image_path):
            os.remove(image_path)
            
    # 3. Fallback: If image generation fails, send the Text Table
    except Exception as e:
        def fmt(data):
            j = data.get("joined", 0)
            l = data.get("left", 0)
            net = j - l
            sign = "+" if net > 0 else ""
            return str(j), str(l), f"{sign}{net}"

        d_j, d_l, d_n = fmt(stats["daily"])
        w_j, w_l, w_n = fmt(stats["weekly"])
        m_j, m_l, m_n = fmt(stats["monthly"])
        y_j, y_l, y_n = fmt(stats["yearly"])
        
        chart = f"Bot Growth Statistics\n"
        chart += f"Total Active Chats: {total_chats}\n\n"
        
        chart += f"+-----------+--------+------+------------+\n"
        chart += f"| Period    | Joined | Left | Net Growth |\n"
        chart += f"+-----------+--------+------+------------+\n"
        chart += f"| Daily     | {d_j:<6} | {d_l:<4} | {d_n:<10} |\n"
        chart += f"| Weekly    | {w_j:<6} | {w_l:<4} | {w_n:<10} |\n"
        chart += f"| Monthly   | {m_j:<6} | {m_l:<4} | {m_n:<10} |\n"
        chart += f"| Yearly    | {y_j:<6} | {y_l:<4} | {y_n:<10} |\n"
        
        if stats.get("last_yearly", {}).get("joined", 0) > 0 or stats.get("last_yearly", {}).get("left", 0) > 0:
            ly_j, ly_l, ly_n = fmt(stats["last_yearly"])
            chart += f"| Prev Year | {ly_j:<6} | {ly_l:<4} | {ly_n:<10} |\n"
            
        chart += f"+-----------+--------+------+------------+"

        await msg.edit_text(
            f"⚠️ **Notice:** Image generation failed (`{e}`). Falling back to text chart.\n\n<code>{chart}</code>"
        )


@app.on_message(filters.new_chat_members)
async def join_watcher(_, message: Message):
    try:
        await _ensure_bot_info()
        if BOT_INFO is None or BOT_ID is None:
            return

        chat = message.chat
        
        for member in message.new_chat_members:
            if member.id == BOT_ID:
                await update_bot_stats("joined")

                count_str = "?"
                try:
                    count_str = await app.get_chat_members_count(chat.id)
                except Exception:
                    pass

                adder = message.from_user.mention if message.from_user else "Unknown"
                date_str = datetime.now().strftime("%d %b %Y | %I:%M %p")

                text = (
                    "Bot Added \n\n"
                    f"Chat: {chat.title}\n"
                    f"ID: {chat.id}\n"
                    f"Username: @{chat.username if chat.username else 'Private'}\n"
                    f"Members: {count_str}\n"
                    f"Added By: {adder}\n"
                    f"Date: {date_str}"
                )
                await safe_send_message(LOGGER_ID, text)

    except Exception as e:
        print(f"Error in join_watcher: {e}")


@app.on_message(filters.left_chat_member)
async def on_left_chat_member(_, message: Message):
    try:
        await _ensure_bot_info()
        if BOT_INFO is None or BOT_ID is None:
            return

        if message.left_chat_member and message.left_chat_member.id == BOT_ID:
            await update_bot_stats("left")

            chat = message.chat
            actor = message.from_user
            
            if actor and actor.id == BOT_ID:
                reason = "Reason: Auto-left or self-invoked."
                remover_line = f"Actor: @{BOT_INFO.username}"
            else:
                reason = "Reason: Kicked/Removed manually."
                remover = actor.mention if actor else "Unknown User"
                remover_line = f"Removed By: {remover}"

            date_str = datetime.now().strftime("%d %b %Y | %I:%M %p")

            text = (
                "Bot Left Group\n\n"
                f"Chat: {chat.title}\n"
                f"ID: {chat.id}\n"
                f"{remover_line}\n"
                f"{reason}\n"
                f"Date: {date_str}"
            )
            await safe_send_message(LEFT_ID, text)

    except Exception as e:
        print(f"Error in on_left_chat_member: {e}")
