import time
import asyncio
import json
import os
import re
from functools import partial

from pyrogram import filters
from pyrogram.enums import ChatMembersFilter, ParseMode
from pyrogram.errors import (
    FloodWait, 
    RPCError, 
    InputUserDeactivated, 
    UserIsBlocked, 
    PeerIdInvalid
)
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

from AnonXMusic import app
from AnonXMusic.misc import SUDOERS
from AnonXMusic.utils.database import (
    get_active_chats,
    get_authuser_names,
    get_client,
    get_served_chats,
    get_served_users,
)
from AnonXMusic.utils.decorators.language import language
from AnonXMusic.utils.formatters import alpha_to_int
from config import adminlist
import config
from AnonXMusic.logging import LOGGER
LOG = LOGGER(__name__)

# Stability Settings
SEMAPHORE = asyncio.Semaphore(5) 
BATCH_SIZE = 50

# Save Files
STATE_FILE = "broadcast_state.json"      
TARGETS_FILE = "broadcast_targets.json"  
FAILED_FILE = "broadcast_failed.json"   

BROADCAST_LOCK = asyncio.Lock()
CANCEL_BROADCAST = False 
FAILED_IDS = set()

def get_readable_time(seconds: int) -> str:
    count = 0
    time_list = []
    time_suffix_list = ["s", "m", "h", "days"]
    while count < 4:
        count += 1
        remainder, result = divmod(seconds, 60) if count < 3 else divmod(seconds, 24)
        if seconds == 0 and remainder == 0: break
        time_list.append(int(result))
        seconds = int(remainder)
    for x in range(len(time_list)):
        time_list[x] = str(time_list[x]) + time_suffix_list[x]
    if len(time_list) == 4: time_list.pop()
    time_list.reverse()
    return ":".join(time_list)

async def write_json_async(filename, data):
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, partial(json_dump_sync, filename, data))

def json_dump_sync(filename, data):
    with open(filename, "w") as f:
        json.dump(data, f, indent=4)

def load_json_sync(filename):
    if os.path.exists(filename):
        try:
            with open(filename, "r") as f:
                return json.load(f)
        except: return None
    return None

def load_failed_list():
    global FAILED_IDS
    data = load_json_sync(FAILED_FILE)
    if data: FAILED_IDS = set(data)

async def save_failed_list():
    await write_json_async(FAILED_FILE, list(FAILED_IDS))

load_failed_list()

async def run_broadcast(state, targets, status_message=None):
    global CANCEL_BROADCAST
    
    if BROADCAST_LOCK.locked():
        return

    async with BROADCAST_LOCK:
        CANCEL_BROADCAST = False
        
        mode = state["mode"]
        is_reply = state.get("is_reply", True)
        content_chat_id = state.get("content_chat")
        content_msg_id = state.get("content_msg")
        text_payload = state.get("text_payload", "")
        
        initiator_id = state.get("initiator")
        start_index = state.get("current_index", 0)
        start_time = state.get("start_time", time.time())
        last_update_time = time.time()
        
        total_users_count = state.get("total_users", 0)
        total_chats_count = state.get("total_chats", 0)
        
        stats = state.get("stats", {"sent_users": 0, "sent_chats": 0, "failed": 0, "skipped": 0})
        sent_users = stats.get("sent_users", 0)
        sent_chats = stats.get("sent_chats", 0)
        failed_count = stats["failed"]
        skipped_count = stats["skipped"]

        content = None
        if is_reply:
            try:
                content = await app.get_messages(content_chat_id, content_msg_id)
                if not content: raise ValueError
            except Exception:
                if status_message: await status_message.edit_text("❌ <b>Error:</b> The original message to be broadcasted was deleted.")
                return

        remaining_targets = targets[start_index:]
        total_targets = len(targets)

        async def _send_one(chat_id):
            if is_reply:
                if mode == "forward":
                    await content.forward(chat_id)
                elif content.text:
                    kwargs = {"reply_markup": content.reply_markup, "parse_mode": ParseMode.HTML}
                    try:
                        await app.send_message(chat_id, content.text.html, disable_web_page_preview=False, **kwargs)
                    except TypeError:
                        from pyrogram.types import LinkPreviewOptions
                        await app.send_message(chat_id, content.text.html, link_preview_options=LinkPreviewOptions(is_disabled=False), **kwargs)
                else:
                    await content.copy(chat_id, reply_markup=content.reply_markup)
            else:
                try:
                    await app.send_message(chat_id, text_payload, disable_web_page_preview=False, parse_mode=ParseMode.HTML)
                except TypeError:
                    from pyrogram.types import LinkPreviewOptions
                    await app.send_message(chat_id, text_payload, link_preview_options=LinkPreviewOptions(is_disabled=False), parse_mode=ParseMode.HTML)

        async def deliver(chat_id):
            nonlocal sent_users, sent_chats, failed_count, skipped_count
            
            if chat_id in FAILED_IDS:
                skipped_count += 1
                return

            for attempt in range(3):
                try:
                    async with SEMAPHORE:
                        await _send_one(chat_id)
                        
                        if str(chat_id).startswith("-"):
                            sent_chats += 1
                        else:
                            sent_users += 1
                            
                        await asyncio.sleep(0.1)
                    break

                except FloodWait as e:
                    if e.value > 60:
                        failed_count += 1
                        break
                    LOG.warning(f"FloodWait {e.value}s in chat {chat_id}")
                    await asyncio.sleep(e.value)

                except (InputUserDeactivated, UserIsBlocked, PeerIdInvalid):
                    FAILED_IDS.add(chat_id) 
                    failed_count += 1
                    break

                except (OSError, ConnectionError, TimeoutError):
                    if attempt < 2:
                        LOG.warning(f"Connection error delivering to {chat_id}, retry {attempt + 1}/3")
                        await asyncio.sleep(2 ** attempt)
                    else:
                        LOG.error(f"Delivery failed after 3 attempts for chat {chat_id}")
                        failed_count += 1

                except Exception:
                    failed_count += 1
                    break

        i = 0
        while i < len(remaining_targets):
            if CANCEL_BROADCAST:
                if status_message: await status_message.edit_text("🛑 <b>Broadcast has been successfully cancelled.</b>")
                if os.path.exists(STATE_FILE): os.remove(STATE_FILE)
                return

            batch = remaining_targets[i : i + BATCH_SIZE]
            
            tasks = [deliver(chat_id) for chat_id in batch]
            await asyncio.gather(*tasks)

            current_real_index = start_index + i + len(batch)
            
            new_state = {
                "mode": mode,
                "is_reply": is_reply,
                "content_chat": content_chat_id,
                "content_msg": content_msg_id,
                "text_payload": text_payload,
                "initiator": initiator_id,
                "start_time": start_time,
                "current_index": current_real_index,
                "total_users": total_users_count,
                "total_chats": total_chats_count,
                "stats": {
                    "sent_users": sent_users,
                    "sent_chats": sent_chats,
                    "failed": failed_count,
                    "skipped": skipped_count
                }
            }

            await write_json_async(STATE_FILE, new_state)
            await save_failed_list()

            if status_message and (time.time() - last_update_time) > 15:
                elapsed = time.time() - start_time
                if elapsed == 0: elapsed = 1
                total_done = sent_users + sent_chats + failed_count + skipped_count
                speed = (total_done - stats.get("skipped", 0)) / elapsed 
                if speed <= 0: speed = 0.1
                
                remaining = total_targets - current_real_index
                eta = get_readable_time(remaining / speed)

                try:
                    await status_message.edit_text(
                        "📢 <b>Broadcast in Progress...</b>\n\n"
                        f"👤 <b>Users Sent:</b> <code>{sent_users}</code>\n"
                        f"👥 <b>Chats Sent:</b> <code>{sent_chats}</code>\n"
                        f"❌ <b>Failed:</b> <code>{failed_count}</code>\n"
                        f"⏩ <b>Skipped:</b> <code>{skipped_count}</code>\n\n"
                        f"📊 <b>Progress:</b> <code>{current_real_index} / {total_targets}</code>\n"
                        f"⚡ <b>Speed:</b> <code>{round(speed, 1)} msg/s</code>\n"
                        f"⏳ <b>ETA:</b> <code>{eta}</code>"
                    )
                    last_update_time = time.time()
                except FloodWait as fw:
                    await asyncio.sleep(fw.value)
                except Exception:
                    pass
            
            i += BATCH_SIZE
            await asyncio.sleep(1.5)

        if os.path.exists(STATE_FILE): os.remove(STATE_FILE)
        if os.path.exists(TARGETS_FILE): os.remove(TARGETS_FILE)
        
        final_text = (
            "✅ <b>Broadcast Completed Successfully!</b> 🎉\n\n"
            "<b>Delivery Statistics:</b>\n"
            f"  👤 <b>Sent to Users:</b> <code>{sent_users}</code>\n"
            f"  👥 <b>Sent to Chats:</b> <code>{sent_chats}</code>\n"
            f"  ❌ <b>Failed:</b> <code>{failed_count}</code>\n"
            f"  🚫 <b>Skipped (Blocked/Invalid):</b> <code>{skipped_count}</code>\n\n"
            "<b>Target Information:</b>\n"
            f"  🎯 <b>Total Processed:</b> <code>{total_targets}</code>\n"
            f"  📝 <b>Database Users:</b> <code>{total_users_count}</code> | <b>Chats:</b> <code>{total_chats_count}</code>\n\n"
            f"⏱ <b>Total Time Taken:</b> <code>{get_readable_time(time.time() - start_time)}</code>"
        )
        
        LOG.info(f"Broadcast finished. Users: {sent_users}, Chats: {sent_chats}, Failed: {failed_count}, Skipped: {skipped_count}")

        if status_message:
            try:
                await status_message.edit_text(final_text)
            except Exception:
                pass
        elif initiator_id: 
            try: await app.send_message(initiator_id, final_text)
            except: pass

@app.on_message(filters.command("broadcast") & SUDOERS)
async def broadcast_command(client, message: Message):
    if BROADCAST_LOCK.locked():
        return await message.reply_text("⚠️ <b>A broadcast is currently already running!</b>")

    if os.path.exists(STATE_FILE) and os.path.exists(TARGETS_FILE):
        if "-new" not in (message.text or "").lower():
            return await message.reply_text(
                "⚠️ <b>Found an unfinished broadcast!</b>\n\n"
                "Use <code>/resume_broadcast</code> to continue where it left off, or <code>/broadcast -new</code> to start a fresh one."
            )

    query = (message.text or "").lower()
    mode = "forward" if "-forward" in query else "copy"

    original_text = message.text or message.caption or ""
    broadcast_text = ""
    
    if original_text.startswith("/"):
        parts = original_text.split(None, 1)
        if len(parts) > 1:
            broadcast_text = parts[1]

    flags_to_remove = ["-all", "-users", "-chats", "-forward", "-copy", "-new"]
    for flag in flags_to_remove:
        broadcast_text = re.sub(rf'(?i)\b{flag}\b', '', broadcast_text)
    
    broadcast_text = broadcast_text.strip()

    if not message.reply_to_message and not broadcast_text:
        return await message.reply_text(
            "⚠️ <b>Please reply to a message OR provide text to broadcast.</b>\n\n"
            "<b>Usage Example:</b> <code>/broadcast -all Hello everyone!</code>"
        )

    msg = await message.reply_text("⏳ <b>Fetching users and chats from the database...</b>")
    LOG.info(f"/broadcast triggered by user: {message.from_user.id}")

    users_list = []
    chats_list = []
    
    if "-all" in query:
        users_list = await get_served_users()
        chats_list = await get_served_chats()
    elif "-chats" in query:
        chats_list = await get_served_chats()
    elif "-users" in query:
        users_list = await get_served_users()
    else:
        return await msg.edit_text(
            "⚠️ <b>Invalid Usage:</b>\n"
            "Please specify a target: <code>/broadcast -all</code>, <code>/broadcast -users</code>, or <code>/broadcast -chats</code>."
        )

    raw_targets = []
    try:
        for u in users_list:
            raw_targets.append(u.get("user_id") if isinstance(u, dict) else u)
        for c in chats_list:
            raw_targets.append(c.get("chat_id") if isinstance(c, dict) else c)
    except Exception as e:
        LOG.error(f"Error extracting IDs: {e}")
        return await msg.edit_text(f"❌ <b>Error extracting IDs:</b> <code>{e}</code>")

    targets = list(set(raw_targets))
    
    if not targets:
        return await msg.edit_text("❌ <b>No targets found in the database.</b>")

    await write_json_async(TARGETS_FILE, targets)

    state = {
        "mode": mode,
        "is_reply": bool(message.reply_to_message),
        "content_chat": message.reply_to_message.chat.id if message.reply_to_message else None,
        "content_msg": message.reply_to_message.id if message.reply_to_message else None,
        "text_payload": broadcast_text,
        "initiator": message.from_user.id,
        "start_time": time.time(),
        "current_index": 0,
        "total_users": len(users_list),
        "total_chats": len(chats_list),
        "stats": {"sent_users": 0, "sent_chats": 0, "failed": 0, "skipped": 0}
    }
    await write_json_async(STATE_FILE, state)

    await run_broadcast(state, targets, msg)

@app.on_message(filters.command("resume_broadcast") & SUDOERS)
async def resume_broadcast(client, message: Message):
    if not (os.path.exists(STATE_FILE) and os.path.exists(TARGETS_FILE)):
        return await message.reply_text("❌ <b>There is no active or paused broadcast to resume.</b>")
    
    msg = await message.reply_text("♻️ <b>Resuming the previous broadcast...</b>")
    LOG.info(f"Broadcast resumed by user: {message.from_user.id}")
    
    state = load_json_sync(STATE_FILE)
    targets = load_json_sync(TARGETS_FILE)
    
    if not state or not targets:
        return await msg.edit_text("❌ <b>Save files are corrupted. Please start a new broadcast.</b>")
        
    await run_broadcast(state, targets, msg)

@app.on_message(filters.command("cancelbroadcast") & SUDOERS)
async def cancel_broadcast_cmd(client, message: Message):
    global CANCEL_BROADCAST
    CANCEL_BROADCAST = True
    LOG.info(f"Broadcast cancelled by user: {message.from_user.id}")
    await message.reply_text("🛑 <b>Stopping the current broadcast immediately...</b>")

@app.on_message(filters.command("clearfailed") & SUDOERS)
async def clear_failed(client, message: Message):
    global FAILED_IDS
    FAILED_IDS.clear()
    if os.path.exists(FAILED_FILE): os.remove(FAILED_FILE)
    LOG.info(f"Failed cache cleared by user: {message.from_user.id}")
    await message.reply_text("✅ <b>Successfully cleared the skipped/failed user cache.</b>")

async def auto_resume_check():
    await asyncio.sleep(5)
    if os.path.exists(STATE_FILE) and os.path.exists(TARGETS_FILE):
        try:
            state = load_json_sync(STATE_FILE)
            targets = load_json_sync(TARGETS_FILE)
            total = len(targets)
            current = state.get("current_index", 0)
            
            text = (
                "⚠️ <b>Broadcast Interrupted!</b>\n\n"
                f"📊 <b>Saved Progress:</b> <code>{current} / {total}</code>\n\n"
                "Would you like to resume it now?"
            )
            buttons = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Resume", callback_data="resume_broadcast"),
                InlineKeyboardButton("❌ Cancel", callback_data="cancel_broadcast")
            ]])
            
            if SUDOERS:
                owner_id = config.OWNER_ID
                await app.send_message(owner_id, text, reply_markup=buttons)
            else:
                LOG.warning("Unfinished broadcast found, but no SUDOERS available to notify.")
        except Exception as e: 
            LOG.error(f"Failed to send auto-resume prompt: {e}")

@app.on_callback_query(filters.regex(r"^(resume_broadcast|cancel_broadcast)$") & filters.user(config.OWNER_ID))
async def broadcast_callback(client, query: CallbackQuery):
    global CANCEL_BROADCAST
    if query.data == "cancel_broadcast":
        CANCEL_BROADCAST = True
        if os.path.exists(STATE_FILE): os.remove(STATE_FILE)
        if os.path.exists(TARGETS_FILE): os.remove(TARGETS_FILE)
        await query.message.edit_text("🛑 <b>Broadcast Successfully Cancelled.</b>")
    else:
        state = load_json_sync(STATE_FILE)
        targets = load_json_sync(TARGETS_FILE)
        if state and targets:
            await query.message.edit_text("♻️ <b>Resuming Broadcast...</b>")
            await run_broadcast(state, targets, query.message)
        else:
            await query.message.edit_text("❌ <b>Broadcast data has expired or is invalid.</b>")


async def auto_clean():
    while not await asyncio.sleep(10):
        try:
            served_chats = await get_active_chats()
            for chat_id in served_chats:
                if chat_id not in adminlist:
                    adminlist[chat_id] = []
                    async for user in app.get_chat_members(
                        chat_id, filter=ChatMembersFilter.ADMINISTRATORS
                    ):
                        if user.privileges.can_manage_video_chats:
                            adminlist[chat_id].append(user.user.id)
                    authusers = await get_authuser_names(chat_id)
                    for user in authusers:
                        user_id = await alpha_to_int(user)
                        adminlist[chat_id].append(user_id)
        except Exception:
            continue


asyncio.create_task(auto_clean())
asyncio.create_task(auto_resume_check())
