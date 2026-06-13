
import asyncio
import re
import unicodedata

from pyrogram import filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from AnonXMusic import app
from AnonXMusic.misc import SUDOERS, db
from AnonXMusic.utils.database import (
    get_authuser_names,
    get_lang,
    get_playmode,
    get_playtype,
)
from config import BANNED_USERS, adminlist
from strings import get_string

# ── In-memory state ───────────────────────────────────────────────────────────

# chat_id → [{title, vidid}, ...]  (pending suggestion buttons)
_suggestions: dict[int, list[dict]] = {}

# chat_id → set of normalised title tokens seen in this session
# Updated every time a song finishes; never pruned (session-lifetime only).
_played_titles: dict[int, set[str]] = {}


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


def _tokenise(title: str) -> set[str]:
    """
    Extract meaningful words from a title for duplicate detection.
    Strips noise words and punctuation, returns lower-case tokens of 3+ chars.
    """
    noise = {
        "the", "and", "for", "with", "this", "that", "from", "into",
        "audio", "video", "official", "lyrics", "lyric", "full", "song",
        "music", "feat", "ft", "remix", "remastered", "live", "version",
        "hd", "hq", "mv", "original", "extended", "mix",
    }
    plain = _to_plain(title).lower()
    words = re.sub(r"[^a-z0-9\s]", " ", plain).split()
    return {w for w in words if len(w) >= 3 and w not in noise}


def _overlaps_played(title: str, played_tokens: set[str]) -> bool:
    """
    Return True if the candidate title shares 2+ meaningful tokens with ANY
    previously-played title token-set, which strongly suggests it is the same
    song (even with minor title variations like 'official video' appended).
    A single token match is intentionally tolerated so 'Blinding Lights Remix'
    isn't blocked just because 'Lights' appeared in an earlier track.
    """
    candidate_tokens = _tokenise(title)
    if not candidate_tokens:
        return False
    overlap = candidate_tokens & played_tokens
    return len(overlap) >= 2


def _short_title(title: str) -> str:
    """Normalise font, cap at 22 chars for the button label."""
    title = _to_plain(title).strip()
    return title[:25].rstrip() if len(title) > 25 else title


# ── Collect all played titles from the current db queue ───────────────────────

def _snapshot_played_tokens(chat_id: int) -> set[str]:
    """
    Merge tokens from:
      1. _played_titles[chat_id]  – accumulated across past tracks this session
      2. db[chat_id]              – tracks still in queue (not yet played)
    Returns the combined set so we filter against everything.
    """
    accumulated = _played_titles.get(chat_id, set())
    in_queue_tokens: set[str] = set()
    for item in (db.get(chat_id) or []):
        t = (item.get("title") or "").strip()
        if t:
            in_queue_tokens |= _tokenise(t)
    return accumulated | in_queue_tokens


def record_played_title(chat_id: int, title: str) -> None:
    """
    Called externally (or from send_related_suggestions) to log a finished track.
    Adds its tokens to the per-chat set so future suggestions avoid it.
    """
    if not title:
        return
    if chat_id not in _played_titles:
        _played_titles[chat_id] = set()
    _played_titles[chat_id] |= _tokenise(title)


# ── YouTube Suggestions → Videos ─────────────────────────────────────────────

async def _fetch_related(
    title: str,
    vidid: str,
    played_tokens: set[str],
) -> list[dict]:
    """
    1. Call YouTube async Suggestions API with the finished track title.
    2. Also try artist-name and genre-style fallback queries for diversity.
    3. Run async VideosSearch on each candidate query.
    4. Deduplicate by vidid AND by title tokens against played_tokens.
    5. Return up to 4 diverse results.
    """
    from youtubesearchpython.__future__ import Suggestions, VideosSearch  # fully async

    # ── Step 1: gather suggestion queries ────────────────────────────────────
    queries: list[str] = []

    # Primary: YouTube's own autocomplete
    try:
        raw = await Suggestions.get(title, language="en", region="US")
        primary: list[str] = (raw or {}).get("result", [])
        # Filter out suggestions that already match the current title too closely
        curr_tokens = _tokenise(title)
        for q in primary:
            if len(_tokenise(q) & curr_tokens) < len(curr_tokens):
                queries.append(q)
    except Exception:
        primary = []

    # Diversity fallbacks: use fragments of the title to get different angles
    plain_title = _to_plain(title)
    words = plain_title.split()

    # "Artist name songs" – try the first 1-2 meaningful words
    if len(words) >= 2:
        artist_guess = " ".join(words[:2])
        queries.append(f"{artist_guess} songs")

    # Genre/mood seeds based on common patterns
    if re.search(r"\b(love|heart|miss|need|want)\b", plain_title, re.I):
        queries.append("best romantic songs")
    if re.search(r"\b(party|night|dance|club)\b", plain_title, re.I):
        queries.append("best party songs")
    if re.search(r"\b(sad|cry|tears|alone|broken)\b", plain_title, re.I):
        queries.append("sad emotional songs")
    if re.search(r"\b(rap|hip.?hop|verse|bars|flow)\b", plain_title, re.I):
        queries.append("best hip hop songs")
    if re.search(r"\b(lofi|chill|relax|study)\b", plain_title, re.I):
        queries.append("lofi chill music")

    # Last-resort: generic "songs like X"
    queries.append(f"songs like {plain_title}")
    queries.append("top viral songs 2024")

    # ── Step 2: search each query and collect unique results ─────────────────
    results: list[dict] = []
    seen_ids: set[str] = {vidid}                  # never re-suggest the finished track
    seen_title_tokens: set[str] = set(played_tokens)  # copy so we can extend it

    for query in queries:
        if len(results) >= 4:
            break
        try:
            res = await VideosSearch(query, limit=5).next()
            items = (res or {}).get("result", [])
        except Exception:
            continue

        for item in items:
            if len(results) >= 4:
                break
            vid = item.get("id") or item.get("videoId") or ""
            t   = (item.get("title") or "").strip()
            if not vid or not t:
                continue
            if vid in seen_ids:
                continue
            if _overlaps_played(t, seen_title_tokens):
                continue   # ← this is the key fix: skip songs matching played titles

            results.append({"title": t, "vidid": vid})
            seen_ids.add(vid)
            # Also mark this suggestion's tokens as "taken" so the next
            # candidate from a different query doesn't produce a near-duplicate.
            seen_title_tokens |= _tokenise(t)

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

    # Record finished track BEFORE fetching so it can't appear in its own suggestions
    record_played_title(chat_id, finished_title)

    # Snapshot ALL played tokens (accumulated + still-queued) for filtering
    played_tokens = _snapshot_played_tokens(chat_id)

    suggestions = await _fetch_related(finished_title, finished_vidid, played_tokens)
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

    # Record the user-chosen suggestion as played so the NEXT suggestion round
    # doesn't offer it again.
    record_played_title(chat_id, title)

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
