# AnonXMusic · plugins/misc/related_tracks.py
#
# After the queue empties, suggests 4 related YouTube tracks as inline buttons.
# Tapping a button runs the full stream() flow — identical to /play <song>.
# Message auto-deletes after 10 s. Queuing respects playtype (admin-only mode).

import asyncio
import re

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

# Words that indicate a remix/cover/variation — skip any result containing these
_VARIATION_WORDS = {
    "lofi", "lo-fi", "slowed", "reverb", "sped up", "nightcore",
    "remix", "cover", "acoustic", "karaoke", "instrumental",
    "piano version", "unplugged", "reprise", "extended", "version",
    "mashup", "tribute", "parody",
}


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

def _strip_meta(text: str) -> str:
    """Remove parentheses/brackets and common suffixes, return lowercase core."""
    text = text.lower()
    text = re.sub(r"[\(\[【][^\)\]】]*[\)\]】]", "", text)
    for s in (" official video", " official audio", " lyrics", " lyric video",
              " official", " audio", " video", " ft.", " feat.", " prod."):
        text = text.replace(s, "")
    return re.sub(r"\s+", " ", text).strip()


def _extract_artist(title: str) -> str:
    """
    Best-effort artist extraction.
    Handles: 'Artist - Song', 'Artist: Song', 'Song by Artist', 'Song ft. Artist'.
    Returns the artist token or empty string.
    """
    t = title.strip()
    # 'Artist - Song' or 'Artist – Song'
    for sep in (" - ", " – ", " — "):
        if sep in t:
            return t.split(sep, 1)[0].strip()
    # 'Song by Artist'
    m = re.search(r"\bby\s+(.+)", t, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return ""


def _is_variation(title: str) -> bool:
    """Return True if the title looks like a lofi/slowed/remix variation."""
    low = title.lower()
    return any(w in low for w in _VARIATION_WORDS)


def _to_plain(text: str) -> str:
    """
    Normalise any Unicode fancy-font characters (bold, italic, script,
    double-struck, monospace, fraktur …) to plain ASCII.
    NFKD decomposition maps every Mathematical Alphanumeric Symbol to its
    base letter/digit, so a single translate covers all font variants.
    """
    import unicodedata
    nfkd = unicodedata.normalize("NFKD", text)
    return re.sub(r"\s+", " ", "".join(c for c in nfkd if ord(c) < 128)).strip()


def _short_title(title: str) -> str:
    """Normalise to plain ASCII font, then cap at 15 chars for the button label."""
    title = _to_plain(title).strip()
    return title[:15].rstrip() + "…" if len(title) > 15 else title


# ── YouTube search ────────────────────────────────────────────────────────────

async def _fetch_related(title: str, vidid: str) -> list[dict]:
    from youtubesearchpython.__future__ import VideosSearch  # type: ignore

    results: list[dict]  = []
    seen_ids: set[str]   = {vidid}
    finished_core        = _strip_meta(title)
    artist               = _extract_artist(title)

    # Query order: artist discography first (most different songs),
    # then a broader genre search. Never search the song name directly
    # to avoid pulling in covers/remixes of the same track.
    queries = []
    if artist:
        queries.append(f"{artist} songs")        # other songs by same artist
        queries.append(f"{artist} best songs")
    queries.append(f"{finished_core.split()[0]} popular songs")  # genre/vibe fallback

    for query in queries:
        if len(results) >= 4:
            break
        try:
            res = await VideosSearch(query, limit=15).next()
            for item in (res or {}).get("result", []):
                vid = item.get("id") or item.get("videoId") or ""
                t   = (item.get("title") or "").strip()
                if not vid or not t:
                    continue
                if vid in seen_ids:
                    continue
                # Skip variations (lofi, slowed, remix, cover…)
                if _is_variation(t):
                    continue
                # Skip if core title is identical to the finished track
                if _strip_meta(t) == finished_core:
                    continue
                results.append({"title": t, "vidid": vid})
                seen_ids.add(vid)
                if len(results) >= 4:
                    break
        except Exception:
            pass

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
