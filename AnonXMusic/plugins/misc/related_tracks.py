# AnonXMusic · plugins/misc/related_tracks.py
#
# After the queue empties, suggests 4 related YouTube tracks as inline buttons.
# Language & region are detected from:
#   1. Unicode script of the finished track title (most accurate)
#   2. Bot's configured chat language (fallback)
# This ensures Hindi songs get Hindi suggestions, Arabic gets Arabic, etc.
# Suggestions are filtered so NO previously-played song title re-appears.
# Uses the async youtubesearchpython.__future__ API throughout.
# Tapping a button runs the full stream() flow — identical to /play <song>.
# Message auto-deletes after 600 s. Queuing respects playtype (admin-only mode).

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
    get_playtype,
)
from config import BANNED_USERS, adminlist
from strings import get_string

# ── In-memory state ───────────────────────────────────────────────────────────

# chat_id → [{title, vidid}, ...]  (pending suggestion buttons)
_suggestions: dict[int, list[dict]] = {}

# chat_id → set of normalised title tokens seen in this session
_played_titles: dict[int, set[str]] = {}


# ── Language / Region resolution ──────────────────────────────────────────────

# Maps bot lang code → (yt_language, yt_region)
# yt_language = BCP-47 'hl' param for YouTube; yt_region = ISO 3166-1 'gl' param
_LANG_MAP: dict[str, tuple[str, str]] = {
    "en":  ("en", "US"),
    "hi":  ("hi", "IN"),
    "pa":  ("pa", "IN"),
    "ar":  ("ar", "SA"),
    "ta":  ("ta", "IN"),
    "te":  ("te", "IN"),
    "bn":  ("bn", "BD"),
    "mr":  ("mr", "IN"),
    "gu":  ("gu", "IN"),
    "kn":  ("kn", "IN"),
    "ml":  ("ml", "IN"),
    "ur":  ("ur", "PK"),
    "tr":  ("tr", "TR"),
    "ru":  ("ru", "RU"),
    "fr":  ("fr", "FR"),
    "de":  ("de", "DE"),
    "es":  ("es", "ES"),
    "pt":  ("pt", "BR"),
    "ko":  ("ko", "KR"),
    "ja":  ("ja", "JP"),
    "zh":  ("zh-Hans", "CN"),
    "id":  ("id", "ID"),
    "ms":  ("ms", "MY"),
    "th":  ("th", "TH"),
    "vi":  ("vi", "VN"),
    "it":  ("it", "IT"),
    "nl":  ("nl", "NL"),
    "pl":  ("pl", "PL"),
    "sv":  ("sv", "SE"),
    "no":  ("no", "NO"),
    "da":  ("da", "DK"),
    "fi":  ("fi", "FI"),
    "cs":  ("cs", "CZ"),
    "ro":  ("ro", "RO"),
    "hu":  ("hu", "HU"),
    "uk":  ("uk", "UA"),
}

# Unicode block ranges → (bot_lang, yt_language, yt_region)
# Ordered by priority: more specific first
_SCRIPT_RANGES: list[tuple[range, str, str, str]] = [
    # Devanagari  (Hindi, Marathi, Sanskrit, Nepali)
    (range(0x0900, 0x097F + 1), "hi", "hi", "IN"),
    # Gurmukhi    (Punjabi)
    (range(0x0A00, 0x0A7F + 1), "pa", "pa", "IN"),
    # Gujarati
    (range(0x0A80, 0x0AFF + 1), "gu", "gu", "IN"),
    # Bengali / Assamese
    (range(0x0980, 0x09FF + 1), "bn", "bn", "BD"),
    # Tamil
    (range(0x0B80, 0x0BFF + 1), "ta", "ta", "IN"),
    # Telugu
    (range(0x0C00, 0x0C7F + 1), "te", "te", "IN"),
    # Kannada
    (range(0x0C80, 0x0CFF + 1), "kn", "kn", "IN"),
    # Malayalam
    (range(0x0D00, 0x0D7F + 1), "ml", "ml", "IN"),
    # Arabic / Urdu / Persian (shared block)
    (range(0x0600, 0x06FF + 1), "ar", "ar", "SA"),
    # Cyrillic (Russian / Ukrainian)
    (range(0x0400, 0x04FF + 1), "ru", "ru", "RU"),
    # CJK Unified (Chinese / Japanese Kanji)
    (range(0x4E00, 0x9FFF + 1), "zh", "zh-Hans", "CN"),
    # Hiragana
    (range(0x3040, 0x309F + 1), "ja", "ja", "JP"),
    # Katakana
    (range(0x30A0, 0x30FF + 1), "ja", "ja", "JP"),
    # Hangul syllables (Korean)
    (range(0xAC00, 0xD7AF + 1), "ko", "ko", "KR"),
    # Thai
    (range(0x0E00, 0x0E7F + 1), "th", "th", "TH"),
    # Arabic Presentation Forms-A (Urdu typography)
    (range(0xFB50, 0xFDFF + 1), "ur", "ur", "PK"),
]


def _detect_script(text: str) -> tuple[str, str] | None:
    """
    Scan the text character-by-character and return (yt_language, yt_region)
    for the dominant non-ASCII script found.
    Returns None if the title is pure ASCII / Latin.

    Special case: CJK Unified (0x4E00-0x9FFF) is shared by Chinese, Japanese,
    and Korean kanji. We resolve ambiguity by:
      - If hiragana OR katakana is also present → Japanese (merge CJK into ja)
      - If Hangul is present → Korean already wins by count
      - Otherwise CJK-only → Chinese (zh-Hans/CN)
    """
    counts: dict[str, list] = {}   # lang_key → [yt_language, yt_region, count]
    for ch in text:
        cp = ord(ch)
        for rng, lang_key, yt_lang, yt_region in _SCRIPT_RANGES:
            if cp in rng:
                if lang_key not in counts:
                    counts[lang_key] = [yt_lang, yt_region, 0]
                counts[lang_key][2] += 1
                break

    if not counts:
        return None

    # Japanese disambiguation: if CJK kanji present AND hiragana/katakana present,
    # merge the CJK count into 'ja' so Japanese wins over Chinese.
    if "zh" in counts and "ja" in counts:
        counts["ja"][2] += counts.pop("zh")[2]

    # Return the script with the most characters
    best = max(counts.values(), key=lambda v: v[2])
    if best[2] < 2:          # fewer than 2 chars = noise, treat as ASCII
        return None
    return best[0], best[1]  # yt_language, yt_region


async def _resolve_lang_region(chat_id: int, title: str) -> tuple[str, str]:
    """
    Return (yt_language, yt_region) by:
      1. Detecting Unicode script in the title (most reliable)
      2. Falling back to the bot's configured chat language
      3. Default to ("en", "US")
    """
    # Step 1: script detection from title
    script = _detect_script(title)
    if script:
        return script

    # Step 2: bot lang code
    try:
        bot_lang = await get_lang(chat_id)
        if bot_lang and bot_lang in _LANG_MAP:
            return _LANG_MAP[bot_lang]
    except Exception:
        pass

    return ("en", "US")


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
    Works on both ASCII and non-ASCII titles (splits on whitespace/punctuation).
    """
    noise = {
        "the", "and", "for", "with", "this", "that", "from", "into",
        "audio", "video", "official", "lyrics", "lyric", "full", "song",
        "music", "feat", "ft", "remix", "remastered", "live", "version",
        "hd", "hq", "mv", "original", "extended", "mix",
    }
    # Try plain ASCII words first
    plain = _to_plain(title).lower()
    ascii_words = {w for w in re.sub(r"[^a-z0-9\s]", " ", plain).split()
                   if len(w) >= 3 and w not in noise}

    # For non-Latin scripts, also split the raw title on whitespace/punctuation
    raw_words = set(re.split(r"[\s\-\|–—/\\,.()\[\]\"']+", title.strip()))
    non_latin = {w for w in raw_words if w and len(w) >= 2 and not w.isascii()}

    return ascii_words | non_latin


def _overlaps_played(title: str, played_tokens: set[str]) -> bool:
    """
    Return True if the candidate title shares 2+ meaningful tokens with ANY
    previously-played title token-set.
    """
    candidate_tokens = _tokenise(title)
    if not candidate_tokens:
        return False
    return len(candidate_tokens & played_tokens) >= 2


def _short_title(title: str) -> str:
    """Cap button label at 22 chars."""
    t = _to_plain(title).strip() or title.strip()
    return t[:22].rstrip() + "…" if len(t) > 22 else t


# ── Collect played token history ──────────────────────────────────────────────

def _snapshot_played_tokens(chat_id: int) -> set[str]:
    accumulated = _played_titles.get(chat_id, set())
    in_queue: set[str] = set()
    for item in (db.get(chat_id) or []):
        t = (item.get("title") or "").strip()
        if t:
            in_queue |= _tokenise(t)
    return accumulated | in_queue


def record_played_title(chat_id: int, title: str) -> None:
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
    yt_lang: str,
    yt_region: str,
) -> list[dict]:
    """
    1. Fetch YouTube autocomplete suggestions in the correct language/region.
    2. Build diversity fallback queries appropriate for the detected language.
    3. Search each query with the correct language/region.
    4. Filter out played titles and within-round duplicates.
    5. Return up to 4 results.
    """
    from youtubesearchpython.__future__ import Suggestions, VideosSearch

    plain_title = _to_plain(title)
    is_non_latin = bool(_detect_script(title))   # True for Hindi, Arabic, etc.

    # ── Step 1: primary autocomplete suggestions ──────────────────────────────
    queries: list[str] = []
    try:
        raw = await Suggestions.get(title, language=yt_lang, region=yt_region)
        primary: list[str] = (raw or {}).get("result", [])
        # Drop suggestions that are too close to the finished title itself
        curr_tokens = _tokenise(title)
        for q in primary:
            if not q:
                continue
            if len(_tokenise(q) & curr_tokens) < max(1, len(curr_tokens) - 1):
                queries.append(q)
    except Exception:
        primary = []

    # ── Step 2: diversity fallbacks ───────────────────────────────────────────
    words = plain_title.split() if plain_title else title.split()

    if is_non_latin:
        # For non-Latin titles: keep queries in the original script
        # Use the raw title words as the seed, YouTube handles the rest
        if len(words) >= 2:
            queries.append(f"{words[0]} {words[1]} songs" if plain_title else title)
        # Generic "popular songs" in the target language
        _popular_phrases = {
            "hi":  ["हिंदी गाने 2024", "बॉलीवुड हिट्स", "नए हिंदी गाने"],
            "pa":  ["ਪੰਜਾਬੀ ਗਾਣੇ 2024", "ਨਵੇਂ ਪੰਜਾਬੀ ਗਾਣੇ"],
            "ta":  ["தமிழ் பாடல்கள் 2024", "புதிய தமிழ் பாடல்"],
            "te":  ["తెలుగు పాటలు 2024", "కొత్త తెలుగు పాటలు"],
            "bn":  ["বাংলা গান 2024", "নতুন বাংলা গান"],
            "ar":  ["أغاني عربية 2024", "أفضل أغاني عربية"],
            "ko":  ["한국 노래 2024", "케이팝 히트곡"],
            "ja":  ["日本の歌 2024", "Jポップ ヒット"],
            "zh":  ["中文歌曲 2024", "华语流行歌曲"],
            "ru":  ["русские песни 2024", "популярные русские хиты"],
            "tr":  ["türkçe şarkılar 2024", "en iyi türkçe şarkılar"],
            "th":  ["เพลงไทย 2024", "เพลงฮิตไทย"],
            "vi":  ["nhạc Việt 2024", "bài hát Việt Nam mới nhất"],
            "id":  ["lagu Indonesia 2024", "lagu pop Indonesia terbaru"],
            "ur":  ["اردو گانے 2024", "پاکستانی گانے"],
        }
        for phrase in _popular_phrases.get(yt_lang, []):
            queries.append(phrase)
    else:
        # For Latin/ASCII titles: mood & genre seeds
        if len(words) >= 2:
            queries.append(f"{words[0]} {words[1]} songs")

        if re.search(r"\b(love|heart|miss|need|want|girl|boy)\b", plain_title, re.I):
            queries.append(f"best romantic songs {yt_region}")
        if re.search(r"\b(party|night|dance|club|dj)\b", plain_title, re.I):
            queries.append(f"best party songs {yt_region}")
        if re.search(r"\b(sad|cry|tears|alone|broken|pain)\b", plain_title, re.I):
            queries.append(f"sad emotional songs {yt_region}")
        if re.search(r"\b(rap|hip.?hop|verse|bars|flow|trap)\b", plain_title, re.I):
            queries.append(f"best hip hop songs {yt_region}")
        if re.search(r"\b(lofi|chill|relax|study|calm)\b", plain_title, re.I):
            queries.append("lofi chill music")

        queries.append(f"songs like {plain_title}")
        queries.append(f"top viral songs 2024 {yt_region}")

    # ── Step 3: search each query with correct language/region ────────────────
    results: list[dict] = []
    seen_ids: set[str] = {vidid}
    seen_title_tokens: set[str] = set(played_tokens)

    for query in queries:
        if len(results) >= 4:
            break
        if not query.strip():
            continue
        try:
            res = await VideosSearch(
                query, limit=5, language=yt_lang, region=yt_region
            ).next()
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
                continue

            results.append({"title": t, "vidid": vid})
            seen_ids.add(vid)
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

    # Detect language & region from title script + bot lang config
    yt_lang, yt_region = await _resolve_lang_region(chat_id, finished_title)

    # Snapshot ALL played tokens for filtering
    played_tokens = _snapshot_played_tokens(chat_id)

    suggestions = await _fetch_related(
        finished_title, finished_vidid, played_tokens, yt_lang, yt_region
    )
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

    # Record chosen track so the next suggestion round skips it
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
