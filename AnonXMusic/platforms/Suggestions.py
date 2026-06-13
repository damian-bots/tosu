import asyncio

from youtubesearchpython.__future__ import Suggestions, VideosSearch
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import config
from AnonXMusic import app as TG_APP
from AnonXMusic.logging import LOGGER as _LOGGER
from AnonXMusic.utils.database import get_lang
from strings import get_string

LOGGER = _LOGGER(__name__)

# How long the "related tracks" message stays before auto-deleting (seconds)
RELATED_DELETE_AFTER = 600  # 10 minutes


async def send_related_tracks(
    chat_id: int,
    original_chat_id: int,
    vidid: str,
    title: str,
) -> None:
    """
    After a track ends and the queue is empty, suggest up to 3 related
    tracks with inline "queue this" buttons.

    1. youtubesearchpython.Suggestions(title)  → autocomplete-style queries
       similar to the track that just played.
    2. youtubesearchpython.VideosSearch(query) → resolve each suggestion
       into a real video (id, title, duration, thumbnail).
    3. Send one message with up to 3 buttons.  Tapping a button is handled
       by the `RelatedAdd` callback in plugins/tools/queue.py.
    4. The message auto-deletes after RELATED_DELETE_AFTER seconds
       (10 minutes) if nobody interacts with it.

    All user-visible text comes from the language file (related_* / RELATED_BTN
    keys) — nothing is hardcoded here.
    """
    try:
        language = await get_lang(original_chat_id)
        _ = get_string(language)
    except Exception:
        _ = get_string("en")

    # ── Step 1: get autocomplete suggestions for the track title ──────────
    suggestions: list = []
    try:
        raw = await Suggestions(title, language="en").result()
        # raw → {"result": ["suggestion1", "suggestion2", ...]}
        suggestions = (raw or {}).get("result", [])
        suggestions = [
            s for s in suggestions
            if isinstance(s, str) and s.strip() and s.lower() != title.lower()
        ]
    except Exception as e:
        LOGGER.warning(f"[Suggestions] failed for {title!r}: {e}")

    # fallback: just search the title itself
    queries = suggestions[:5] if suggestions else [title]

    # ── Step 2: resolve up to 3 unique video results ───────────────────────
    seen_ids = {vidid}   # exclude the track that just finished
    resolved: list = []

    for query in queries:
        if len(resolved) >= 3:
            break
        try:
            res = await VideosSearch(query, limit=3).next()
            for r in (res or {}).get("result", []):
                vid = r.get("id", "")
                if not vid or vid in seen_ids:
                    continue
                seen_ids.add(vid)
                thumbs = r.get("thumbnails", [])
                thumb  = thumbs[0]["url"].split("?")[0] if thumbs else config.YOUTUBE_IMG_URL
                resolved.append({
                    "vidid":    vid,
                    "title":    r.get("title", "Unknown"),
                    "duration": r.get("duration") or "?",
                    "thumb":    thumb,
                })
                if len(resolved) >= 3:
                    break
        except Exception as e:
            LOGGER.warning(f"[Suggestions] VideosSearch failed for {query!r}: {e}")
            continue

    if not resolved:
        return

    # ── Step 3: build message text + buttons ────────────────────────────────
    lines   = []
    buttons = []
    for i, s in enumerate(resolved, start=1):
        t   = s["title"][:52]
        dur = s["duration"]
        lines.append(f"<b>{i}.</b> {t}  [{dur}]")
        buttons.append(
            InlineKeyboardButton(
                text          = _["RELATED_BTN"].format(f"{i}. {t[:28]}"),
                callback_data = f"RelatedAdd {s['vidid']}|{original_chat_id}",
            )
        )

    body    = "\n".join(lines)
    caption = _["related_1"].format(body)
    markup  = InlineKeyboardMarkup([buttons])

    # ── Step 4: send, then auto-delete ───────────────────────────────────────
    try:
        msg = await TG_APP.send_message(original_chat_id, caption, reply_markup=markup)
    except Exception as e:
        LOGGER.warning(f"[Suggestions] failed to send message: {e}")
        return

    await asyncio.sleep(RELATED_DELETE_AFTER)
    try:
        await msg.delete()
    except Exception:
        pass
