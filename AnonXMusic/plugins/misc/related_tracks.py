# AnonXMusic · plugins/misc/related_tracks.py
#
# After the queue empties, suggests 4 related YouTube tracks as inline buttons
# (title only, no emoji prefix).  Message auto-deletes after 10 s.
# Queuing respects the chat's playtype: admin-only chats block normal users.

import asyncio

from pyrogram import filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from AnonXMusic import app
from AnonXMusic.misc import SUDOERS, db
from AnonXMusic.utils.database import (
    get_authuser_names,
    get_lang,
    get_playtype,
)
from config import BANNED_USERS, adminlist
from strings import get_string

# chat_id → [{"title": str, "vidid": str}, ...]
_suggestions: dict[int, list[dict]] = {}


# ── Permission check ──────────────────────────────────────────────────────────

async def _is_allowed_to_play(chat_id: int, user_id: int) -> bool:
    if user_id in SUDOERS:
        return True
    if await get_playtype(chat_id) == "Everyone":
        return True
    if user_id in (adminlist.get(chat_id) or []):
        return True
    try:
        auth = await get_authuser_names(chat_id)
        if str(user_id) in auth:
            return True
    except Exception:
        pass
    return False


# ── YouTube search ────────────────────────────────────────────────────────────

async def _fetch_related(title: str, vidid: str) -> list[dict]:
    results: list[dict] = []
    seen: set[str] = {vidid}

    async def _search(query: str):
        from youtubesearchpython import VideosSearch  # type: ignore
        s = VideosSearch(query, limit=8)
        data = await asyncio.get_event_loop().run_in_executor(None, s.result)
        for item in (data or {}).get("result", []):
            vid = item.get("id") or item.get("videoId")
            t = (item.get("title") or "").strip()
            if vid and t and vid not in seen:
                results.append({"title": t, "vidid": vid})
                seen.add(vid)
            if len(results) == 4:
                break

    try:
        await _search(f"{title} similar songs")
    except Exception:
        pass

    if len(results) < 4:
        try:
            await _search(title)
        except Exception:
            pass

    return results[:4]


# ── Inline keyboard ───────────────────────────────────────────────────────────

def _markup(suggestions: list[dict]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(s["title"][:64], callback_data=f"RELATED_QUEUE|{i}")]
            for i, s in enumerate(suggestions)
        ]
    )


# ── Public entry point (called from core/call.py) ────────────────────────────

async def send_related_suggestions(chat_id: int, finished_title: str, finished_vidid: str):
    language = await get_lang(chat_id)
    _ = get_string(language)

    suggestions = await _fetch_related(finished_title, finished_vidid)
    if not suggestions:
        return

    _suggestions[chat_id] = suggestions

    try:
        msg = await app.send_message(
            chat_id,
            _["related_1"],
            reply_markup=_markup(suggestions),
        )
    except Exception:
        return

    async def _autodel():
        await asyncio.sleep(10)
        try:
            await msg.delete()
        except Exception:
            pass
        _suggestions.pop(chat_id, None)

    asyncio.ensure_future(_autodel())


# ── Queue callback ────────────────────────────────────────────────────────────

@app.on_callback_query(filters.regex(r"^RELATED_QUEUE\|(\d+)$") & ~BANNED_USERS)
async def related_queue_cb(client, callback_query):
    chat_id = callback_query.message.chat.id
    user_id = callback_query.from_user.id
    language = await get_lang(chat_id)
    _ = get_string(language)

    if not await _is_allowed_to_play(chat_id, user_id):
        return await callback_query.answer(_["related_2"], show_alert=True)

    idx = int(callback_query.matches[0].group(1))
    suggestions = _suggestions.get(chat_id)
    if not suggestions or idx >= len(suggestions):
        return await callback_query.answer(_["related_3"], show_alert=True)

    chosen = suggestions[idx]
    vidid  = chosen["vidid"]
    title  = chosen["title"]

    # Remove suggestion message immediately
    try:
        await callback_query.message.delete()
    except Exception:
        pass
    _suggestions.pop(chat_id, None)

    await callback_query.answer(title[:50], show_alert=False)

    try:
        from AnonXMusic import YouTube
        from AnonXMusic.utils.stream.queue import put_queue
        from AnonXMusic.utils.database import add_active_chat, is_active_chat
        from AnonXMusic.utils.thumbnails import get_thumb
        from AnonXMusic.utils.inline.play import stream_markup
        from AnonXMusic.core.call import Anony
        from pytgcalls.types.input_stream import AudioPiped
        from pytgcalls.types.input_stream.quality import HighQualityAudio

        details, _ = await YouTube.details(vidid, True)
        if not details:
            return await app.send_message(chat_id, _["related_5"])

        file_path, direct = await YouTube.download(vidid, None, videoid=True, video=False)
        if not file_path:
            return await app.send_message(chat_id, _["related_6"])

        duration   = details.get("duration_min", "unknown")
        user_mention = callback_query.from_user.mention
        language = await get_lang(chat_id)
        strings  = get_string(language)

        if await is_active_chat(chat_id):
            await put_queue(
                chat_id, chat_id, file_path, title, duration,
                user_mention, vidid, user_id, "audio",
                thumbnail=details.get("thumbnail", ""),
            )
            await app.send_message(chat_id, strings["related_4"].format(title))
        else:
            stream = AudioPiped(file_path, HighQualityAudio())
            assistant = await __import__(
                "AnonXMusic.utils.database", fromlist=["get_assistant"]
            ).get_assistant(chat_id)
            await Anony.join_call(chat_id, assistant, stream, video_stream=False)
            await put_queue(
                chat_id, chat_id, file_path, title, duration,
                user_mention, vidid, user_id, "audio",
                thumbnail=details.get("thumbnail", ""),
            )
            from AnonXMusic.utils.database import music_on
            await music_on(chat_id)
            await add_active_chat(chat_id)
            img     = await get_thumb(vidid)
            caption = strings["stream_1"].format(
                f"https://youtube.com/watch?v={vidid}", title[:23], duration, user_mention
            )
            button  = stream_markup(strings, chat_id)
            await app.send_photo(
                chat_id, photo=img, caption=caption,
                reply_markup=InlineKeyboardMarkup(button),
            )

    except Exception as e:
        lang = await get_lang(chat_id)
        s = get_string(lang)
        await app.send_message(chat_id, s["related_7"].format(e))
