# AnonXMusic · plugins/misc/related_tracks.py
#
# After the queue empties, suggests 4 related YouTube tracks as inline buttons.
# Uses YouTube Suggestions API (autocomplete) to find genuinely different songs.
# Tapping a button runs the full stream() flow — identical to /play <song>.
# Message auto-deletes after 600 s. Queuing respects playtype (admin-only mode).

import asyncio
import re
import unicodedata

from pyrogram import filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from AnonXMusic import app
from AnonXMusic.misc import SUDOERS
from AnonXMusic.utils.database import (
    get_authuser_names,
    get_lang,
    get_playmode,
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


# ── Title helpers ─────────────────────────────────────────────────────────────

def _to_plain(text: str) -> str:
    """NFKD-normalise Unicode fancy fonts (bold/italic/script/…) to plain ASCII."""
    nfkd = unicodedata.normalize("NFKD", text)
    return re.sub(r"\s+", " ", "".join(c for c in nfkd if ord(c) < 128)).strip()


def _short_title(title: str) -> str:
    """Normalise font, cap at 20 chars for the button label."""
    title = _to_plain(title).strip()
    return title[:20].rstrip() + "…" if len(title) > 20 else title


# ── YouTube Suggestions → Videos ─────────────────────────────────────────────

async def _fetch_related(title: str, vidid: str) -> list[dict]:
    """
    1. Call YouTube Suggestions API with the finished track title.
    2. Take the returned autocomplete query strings (e.g. "Artist - Other Song").
    3. Run VideosSearch on each to get the first real video result.
    4. Deduplicate by vidid, skip the finished track itself.
    """
    from youtubesearchpython import Suggestions, ResultMode          # sync
    from youtubesearchpython.__future__ import VideosSearch          # async

    # Step 1 – get autocomplete suggestions
    try:
        raw = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: Suggestions(language="en", region="US").get(title, mode=ResultMode.dict),
        )
        queries: list[str] = (raw or {}).get("result", [])
    except Exception:
        queries = []

    # Fallback: if Suggestions returned nothing, use the title itself
    if not queries:
        queries = [title]

    results: list[dict] = []
    seen_ids: set[str]  = {vidid}

    # Step 2 – search each suggestion query and take the first video
    for query in queries:
        if len(results) >= 4:
            break
        try:
            res = await VideosSearch(query, limit=3).next()
            items = (res or {}).get("result", [])
            if not items:
                continue
            item = items[0]
            vid = item.get("id") or item.get("videoId") or ""
            t   = (item.get("title") or "").strip()
            if not vid or not t or vid in seen_ids:
                continue
            results.append({"title": t, "vidid": vid})
            seen_ids.add(vid)
        except Exception:
            continue

    return results[:4]


# ── Inline keyboard ───────────────────────────────────────────────────────────

def _markup(suggestions: list[dict]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(_short_title(s["title"]), callback_data=f"RELATED_QUEUE|{i}")]
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
        await asyncio.sleep(600)
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

    try:
        await callback_query.message.delete()
    except Exception:
        pass
    _suggestions.pop(chat_id, None)

    await callback_query.answer(title[:50], show_alert=False)

    try:
        from AnonXMusic import YouTube
        from AnonXMusic.utils.stream.stream import stream as do_stream

        user_name = callback_query.from_user.mention
        mystic    = await app.send_message(chat_id, _["play_1"])
        details, track_id = await YouTube.track(vidid, videoid=True)

        await do_stream(
            _,
            mystic,
            user_id,
            details,
            chat_id,
            user_name,
            chat_id,
            video=None,
            streamtype="youtube",
            spotify=None,
            forceplay=None,
        )

    except Exception as e:
        lang = await get_lang(chat_id)
        s    = get_string(lang)
        await app.send_message(chat_id, s["related_7"].format(e))
