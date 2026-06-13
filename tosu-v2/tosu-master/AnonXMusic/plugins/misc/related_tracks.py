# AnonXMusic · plugins/misc/related_tracks.py
#
# After the queue empties, suggests 4 related YouTube tracks as inline buttons.
# Tapping a button runs the full stream() flow — identical to /play <song>.
# Message auto-deletes after 10 s. Queuing respects playtype (admin-only mode).

import asyncio

from pyrogram import filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

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


# ── YouTube search ────────────────────────────────────────────────────────────

def _normalise(text: str) -> str:
    """Lowercase, strip parens/brackets and common suffixes for comparison."""
    import re
    text = text.lower()
    text = re.sub(r"[\(\[【][^\)\]】]*[\)\]】]", "", text)   # remove (…) / […]
    for s in (" official video", " official audio", " lyrics", " lyric video",
              " official", " audio", " video", " ft.", " feat."):
        text = text.replace(s, "")
    return re.sub(r"\s+", " ", text).strip()


async def _fetch_related(title: str, vidid: str) -> list[dict]:
    """
    Search YouTube for tracks related to the finished one.
    Excludes:
      - the finished track's own vidid
      - any result whose normalised title is too similar to the finished title
    """
    from youtubesearchpython.__future__ import VideosSearch  # type: ignore

    results: list[dict] = []
    seen_ids: set[str]  = {vidid}
    finished_norm       = _normalise(title)

    # Two passes: artist+similar, then artist name alone
    clean = _normalise(title)
    queries = [f"{clean} similar", clean]

    for query in queries:
        if len(results) >= 4:
            break
        try:
            res = await VideosSearch(query, limit=10).next()
            for item in (res or {}).get("result", []):
                vid = item.get("id") or item.get("videoId") or ""
                t   = (item.get("title") or "").strip()
                if not vid or not t:
                    continue
                if vid in seen_ids:
                    continue
                # Skip if normalised title is too close to the finished track
                if _normalise(t) == finished_norm:
                    continue
                results.append({"title": t, "vidid": vid})
                seen_ids.add(vid)
                if len(results) >= 4:
                    break
        except Exception:
            pass

    return results[:4]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _short_title(title: str) -> str:
    return title[:38].rstrip() + "…" if len(title) > 38 else title


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

    # ── Mirror the exact /play flow ───────────────────────────────────────────
    try:
        from AnonXMusic import YouTube
        from AnonXMusic.utils.stream.stream import stream as do_stream

        user_name    = callback_query.from_user.mention
        playmode     = await get_playmode(chat_id)

        # Send a "processing" message — stream() edits/deletes it like /play does
        mystic = await app.send_message(chat_id, _["play_1"])

        # Fetch track details — returns the same dict that play.py passes to stream()
        details, track_id = await YouTube.track(vidid, videoid=True)

        await do_stream(
            _,
            mystic,
            user_id,
            details,
            chat_id,
            user_name,
            chat_id,            # original_chat_id
            video=None,         # audio only
            streamtype="youtube",
            spotify=None,
            forceplay=None,
        )

    except Exception as e:
        lang = await get_lang(chat_id)
        s    = get_string(lang)
        await app.send_message(chat_id, s["related_7"].format(e))
