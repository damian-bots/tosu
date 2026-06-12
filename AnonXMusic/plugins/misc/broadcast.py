# AnonXMusic · plugins/misc/broadcast.py  (v1.0.4)
# Stable broadcast with:
#   - asyncio.Semaphore concurrency limit
#   - Batch processing with state persistence (resume after restart)
#   - Full error logging per delivery failure
#   - Auto-resume prompt to owner on bot restart
#   - cancel / resume / clearfailed commands

import asyncio
import json
import os
import re
import time
import traceback
from functools import partial

from pyrogram import filters
from pyrogram.enums import ParseMode
from pyrogram.errors import (
    FloodWait,
    InputUserDeactivated,
    PeerIdInvalid,
    RPCError,
    UserIsBlocked,
)
from pyrogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

import config
from AnonXMusic import app
from AnonXMusic.logging import LOGGER
from AnonXMusic.misc import SUDOERS
from AnonXMusic.utils.database import (
    get_served_chats,
    get_served_users,
)

LOG = LOGGER(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
SEMAPHORE  = asyncio.Semaphore(5)
BATCH_SIZE = 50

STATE_FILE   = "broadcast_state.json"
TARGETS_FILE = "broadcast_targets.json"
FAILED_FILE  = "broadcast_failed.json"

BROADCAST_LOCK    = asyncio.Lock()
CANCEL_BROADCAST  = False
FAILED_IDS: set   = set()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _readable_time(seconds: int) -> str:
    parts = []
    for unit, div in [("d", 86400), ("h", 3600), ("m", 60), ("s", 1)]:
        val, seconds = divmod(seconds, div)
        if val:
            parts.append(f"{val}{unit}")
    return ":".join(parts) if parts else "0s"


async def _write_json(filename, data):
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, partial(_json_dump, filename, data))


def _json_dump(filename, data):
    with open(filename, "w") as f:
        json.dump(data, f, indent=4)


def _load_json(filename):
    if os.path.exists(filename):
        try:
            with open(filename) as f:
                return json.load(f)
        except Exception:
            pass
    return None


def _load_failed():
    global FAILED_IDS
    data = _load_json(FAILED_FILE)
    if data:
        FAILED_IDS = set(data)


async def _save_failed():
    await _write_json(FAILED_FILE, list(FAILED_IDS))


_load_failed()


# ── Core broadcast runner ─────────────────────────────────────────────────────

async def run_broadcast(state: dict, targets: list, status_msg=None):
    global CANCEL_BROADCAST

    if BROADCAST_LOCK.locked():
        return

    async with BROADCAST_LOCK:
        CANCEL_BROADCAST = False

        mode            = state["mode"]
        is_reply        = state.get("is_reply", True)
        content_chat    = state.get("content_chat")
        content_msg_id  = state.get("content_msg")
        text_payload    = state.get("text_payload", "")
        initiator_id    = state.get("initiator")
        start_idx       = state.get("current_index", 0)
        start_time      = state.get("start_time", time.time())
        last_update     = time.time()

        stats = state.get("stats", {"sent_users": 0, "sent_chats": 0, "failed": 0, "skipped": 0})
        sent_users    = stats["sent_users"]
        sent_chats    = stats["sent_chats"]
        failed_count  = stats["failed"]
        skipped_count = stats["skipped"]

        content = None
        if is_reply:
            try:
                content = await app.get_messages(content_chat, content_msg_id)
                if not content:
                    raise ValueError("Message deleted")
            except Exception as e:
                LOG.error(f"[Broadcast] Cannot fetch source message: {e}")
                if status_msg:
                    await status_msg.edit_text("❌ <b>Source message was deleted.</b>")
                return

        remaining     = targets[start_idx:]
        total_targets = len(targets)

        async def _send_to(chat_id):
            nonlocal sent_users, sent_chats, failed_count, skipped_count

            if chat_id in FAILED_IDS:
                skipped_count += 1
                return

            async def _do_send():
                if is_reply:
                    if mode == "forward":
                        await content.forward(chat_id)
                    elif content.text:
                        try:
                            await app.send_message(
                                chat_id, content.text.html,
                                reply_markup=content.reply_markup,
                                parse_mode=ParseMode.HTML,
                                disable_web_page_preview=False,
                            )
                        except TypeError:
                            from pyrogram.types import LinkPreviewOptions
                            await app.send_message(
                                chat_id, content.text.html,
                                reply_markup=content.reply_markup,
                                parse_mode=ParseMode.HTML,
                                link_preview_options=LinkPreviewOptions(is_disabled=False),
                            )
                    else:
                        await content.copy(chat_id, reply_markup=content.reply_markup)
                else:
                    try:
                        await app.send_message(
                            chat_id, text_payload,
                            parse_mode=ParseMode.HTML,
                            disable_web_page_preview=False,
                        )
                    except TypeError:
                        from pyrogram.types import LinkPreviewOptions
                        await app.send_message(
                            chat_id, text_payload,
                            parse_mode=ParseMode.HTML,
                            link_preview_options=LinkPreviewOptions(is_disabled=False),
                        )

            try:
                async with SEMAPHORE:
                    await _do_send()
                    if str(chat_id).startswith("-"):
                        sent_chats += 1
                    else:
                        sent_users += 1
                    await asyncio.sleep(0.1)

            except FloodWait as fw:
                if fw.value > 60:
                    LOG.warning(f"[Broadcast] FloodWait {fw.value}s too long for {chat_id}, skipping")
                    failed_count += 1
                else:
                    LOG.warning(f"[Broadcast] FloodWait {fw.value}s for {chat_id}, waiting…")
                    await asyncio.sleep(fw.value)
                    try:
                        async with SEMAPHORE:
                            await _do_send()
                            if str(chat_id).startswith("-"):
                                sent_chats += 1
                            else:
                                sent_users += 1
                    except Exception:
                        failed_count += 1

            except (InputUserDeactivated, UserIsBlocked, PeerIdInvalid):
                FAILED_IDS.add(chat_id)
                failed_count += 1

            except Exception as e:
                LOG.error(f"[Broadcast] Delivery error to {chat_id}: {e}\n"
                          + traceback.format_exc())
                failed_count += 1

        i = 0
        while i < len(remaining):
            if CANCEL_BROADCAST:
                LOG.info("[Broadcast] Cancelled by user")
                if status_msg:
                    await status_msg.edit_text("🛑 <b>Broadcast cancelled.</b>")
                _cleanup_files()
                return

            batch = remaining[i: i + BATCH_SIZE]
            await asyncio.gather(*[_send_to(cid) for cid in batch])

            real_index = start_idx + i + len(batch)
            new_state  = {**state, "current_index": real_index, "stats": {
                "sent_users": sent_users, "sent_chats": sent_chats,
                "failed": failed_count, "skipped": skipped_count,
            }}
            await _write_json(STATE_FILE, new_state)
            await _save_failed()

            # Progress update every 15 s
            if status_msg and (time.time() - last_update) > 15:
                elapsed = max(time.time() - start_time, 1)
                total_done = sent_users + sent_chats + failed_count + skipped_count
                speed      = max(total_done / elapsed, 0.1)
                eta        = _readable_time(int((total_targets - real_index) / speed))
                try:
                    await status_msg.edit_text(
                        "📢 <b>Broadcast in Progress…</b>\n\n"
                        f"👤 <b>Users:</b> <code>{sent_users}</code>\n"
                        f"👥 <b>Chats:</b> <code>{sent_chats}</code>\n"
                        f"❌ <b>Failed:</b> <code>{failed_count}</code>\n"
                        f"⏩ <b>Skipped:</b> <code>{skipped_count}</code>\n\n"
                        f"📊 <b>Progress:</b> <code>{real_index}/{total_targets}</code>\n"
                        f"⚡ <b>Speed:</b> <code>{round(speed, 1)} msg/s</code>\n"
                        f"⏳ <b>ETA:</b> <code>{eta}</code>"
                    )
                    last_update = time.time()
                except FloodWait as fw:
                    await asyncio.sleep(fw.value)
                except Exception:
                    pass

            i += BATCH_SIZE
            await asyncio.sleep(1.5)

        _cleanup_files()

        summary = (
            "✅ <b>Broadcast Completed!</b>\n\n"
            f"👤 <b>Users:</b> <code>{sent_users}</code>\n"
            f"👥 <b>Chats:</b> <code>{sent_chats}</code>\n"
            f"❌ <b>Failed:</b> <code>{failed_count}</code>\n"
            f"🚫 <b>Skipped:</b> <code>{skipped_count}</code>\n"
            f"🎯 <b>Total:</b> <code>{total_targets}</code>\n"
            f"⏱ <b>Time:</b> <code>{_readable_time(int(time.time() - start_time))}</code>"
        )
        LOG.info(f"[Broadcast] Done. Users={sent_users}, Chats={sent_chats}, "
                 f"Failed={failed_count}, Skipped={skipped_count}")

        if status_msg:
            try:
                await status_msg.edit_text(summary)
            except Exception:
                pass
        elif initiator_id:
            try:
                await app.send_message(initiator_id, summary)
            except Exception:
                pass


def _cleanup_files():
    for f in (STATE_FILE, TARGETS_FILE):
        if os.path.exists(f):
            os.remove(f)


# ── Commands ──────────────────────────────────────────────────────────────────

@app.on_message(filters.command("broadcast") & SUDOERS)
async def broadcast_command(client, message: Message):
    if BROADCAST_LOCK.locked():
        return await message.reply_text("⚠️ <b>A broadcast is already running!</b>")

    if os.path.exists(STATE_FILE) and os.path.exists(TARGETS_FILE):
        if "-new" not in (message.text or "").lower():
            return await message.reply_text(
                "⚠️ <b>Unfinished broadcast found!</b>\n\n"
                "Use <code>/resume_broadcast</code> to continue or <code>/broadcast -new</code> for fresh."
            )

    query = (message.text or "").lower()
    mode  = "forward" if "-forward" in query else "copy"

    raw_text = message.text or message.caption or ""
    parts    = raw_text.split(None, 1)
    bc_text  = parts[1] if len(parts) > 1 else ""
    for flag in ["-all", "-users", "-chats", "-forward", "-copy", "-new"]:
        bc_text = re.sub(rf"(?i)\b{re.escape(flag)}\b", "", bc_text)
    bc_text = bc_text.strip()

    if not message.reply_to_message and not bc_text:
        return await message.reply_text(
            "⚠️ <b>Reply to a message OR provide text.</b>\n\n"
            "<b>Example:</b> <code>/broadcast -all Hello everyone!</code>"
        )

    msg = await message.reply_text("⏳ <b>Fetching targets from database…</b>")
    LOG.info(f"[Broadcast] Started by {message.from_user.id}")

    users_list = chats_list = []
    if "-all" in query:
        users_list = await get_served_users()
        chats_list = await get_served_chats()
    elif "-chats" in query:
        chats_list = await get_served_chats()
    elif "-users" in query:
        users_list = await get_served_users()
    else:
        return await msg.edit_text(
            "⚠️ Specify target: <code>-all</code>, <code>-users</code>, or <code>-chats</code>."
        )

    try:
        raw_ids = [
            (u.get("user_id") if isinstance(u, dict) else u) for u in users_list
        ] + [
            (c.get("chat_id") if isinstance(c, dict) else c) for c in chats_list
        ]
    except Exception as e:
        LOG.error(f"[Broadcast] ID extraction error: {e}")
        return await msg.edit_text(f"❌ <b>Error extracting IDs:</b> <code>{e}</code>")

    targets = list(set(raw_ids))
    if not targets:
        return await msg.edit_text("❌ <b>No targets found in database.</b>")

    await _write_json(TARGETS_FILE, targets)
    state = {
        "mode":          mode,
        "is_reply":      bool(message.reply_to_message),
        "content_chat":  message.reply_to_message.chat.id if message.reply_to_message else None,
        "content_msg":   message.reply_to_message.id     if message.reply_to_message else None,
        "text_payload":  bc_text,
        "initiator":     message.from_user.id,
        "start_time":    time.time(),
        "current_index": 0,
        "total_users":   len(users_list),
        "total_chats":   len(chats_list),
        "stats":         {"sent_users": 0, "sent_chats": 0, "failed": 0, "skipped": 0},
    }
    await _write_json(STATE_FILE, state)
    await run_broadcast(state, targets, msg)


@app.on_message(filters.command("resume_broadcast") & SUDOERS)
async def resume_broadcast(client, message: Message):
    if not (os.path.exists(STATE_FILE) and os.path.exists(TARGETS_FILE)):
        return await message.reply_text("❌ <b>No paused broadcast found.</b>")
    msg    = await message.reply_text("♻️ <b>Resuming broadcast…</b>")
    state   = _load_json(STATE_FILE)
    targets = _load_json(TARGETS_FILE)
    if not state or not targets:
        return await msg.edit_text("❌ <b>Save files corrupted. Start a new broadcast.</b>")
    LOG.info(f"[Broadcast] Resumed by {message.from_user.id}")
    await run_broadcast(state, targets, msg)


@app.on_message(filters.command("cancelbroadcast") & SUDOERS)
async def cancel_broadcast_cmd(client, message: Message):
    global CANCEL_BROADCAST
    CANCEL_BROADCAST = True
    LOG.info(f"[Broadcast] Cancelled by {message.from_user.id}")
    await message.reply_text("🛑 <b>Stopping broadcast…</b>")


@app.on_message(filters.command("clearfailed") & SUDOERS)
async def clear_failed_cmd(client, message: Message):
    global FAILED_IDS
    FAILED_IDS.clear()
    if os.path.exists(FAILED_FILE):
        os.remove(FAILED_FILE)
    LOG.info(f"[Broadcast] Failed cache cleared by {message.from_user.id}")
    await message.reply_text("✅ <b>Cleared failed/blocked cache.</b>")


# ── Callback buttons ──────────────────────────────────────────────────────────

@app.on_callback_query(
    filters.regex(r"^(resume_broadcast|cancel_broadcast)$")
    & filters.user(config.OWNER_ID)
)
async def broadcast_callback(client, query: CallbackQuery):
    global CANCEL_BROADCAST
    if query.data == "cancel_broadcast":
        CANCEL_BROADCAST = True
        _cleanup_files()
        await query.message.edit_text("🛑 <b>Broadcast Cancelled.</b>")
    else:
        state   = _load_json(STATE_FILE)
        targets = _load_json(TARGETS_FILE)
        if state and targets:
            await query.message.edit_text("♻️ <b>Resuming Broadcast…</b>")
            await run_broadcast(state, targets, query.message)
        else:
            await query.message.edit_text("❌ <b>Broadcast data expired or invalid.</b>")


# ── Admin-list refresh loop ────────────────────────────────────────────────────
# (Keep active admin list fresh every 10 s)

from AnonXMusic.utils.database import get_active_chats, get_authuser_names
from AnonXMusic.utils.formatters import alpha_to_int
from config import adminlist


async def _refresh_admin_list():
    while True:
        await asyncio.sleep(10)
        try:
            from pyrogram.enums import ChatMembersFilter
            for chat_id in await get_active_chats():
                if chat_id not in adminlist:
                    adminlist[chat_id] = []
                async for user in app.get_chat_members(chat_id, filter=ChatMembersFilter.ADMINISTRATORS):
                    if user.privileges.can_manage_video_chats:
                        adminlist[chat_id].append(user.user.id)
                for user in await get_authuser_names(chat_id):
                    adminlist[chat_id].append(await alpha_to_int(user))
        except Exception:
            pass


# ── Auto-resume on startup ─────────────────────────────────────────────────────

async def _auto_resume_check():
    await asyncio.sleep(5)
    if not (os.path.exists(STATE_FILE) and os.path.exists(TARGETS_FILE)):
        return
    try:
        state   = _load_json(STATE_FILE)
        targets = _load_json(TARGETS_FILE)
        if not state or not targets:
            return
        total   = len(targets)
        current = state.get("current_index", 0)
        text = (
            "⚠️ <b>Broadcast Interrupted!</b>\n\n"
            f"📊 <b>Progress:</b> <code>{current}/{total}</code>\n\n"
            "Resume it now?"
        )
        buttons = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Resume", callback_data="resume_broadcast"),
            InlineKeyboardButton("❌ Cancel", callback_data="cancel_broadcast"),
        ]])
        await app.send_message(config.OWNER_ID, text, reply_markup=buttons)
    except Exception as e:
        LOG.error(f"[Broadcast] Auto-resume check failed: {e}")


asyncio.create_task(_refresh_admin_list())
asyncio.create_task(_auto_resume_check())
