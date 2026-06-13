# AnonXMusic · plugins/misc/related_tracks.py
#
# After the queue empties, suggests 4 related YouTube tracks as inline buttons.
# Message auto-deletes after 10 s. Queuing respects playtype (admin-only mode).

import asyncio

from pyrogram import filters
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from AnonXMusic import app
from AnonXMusic.misc import SUDOERS
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
    """
    Search for tracks related to the finished one, excluding the finished vidid.
    Uses the async __future__ VideosSearch (same as the rest of the codebase).
    Tries two queries to get 4 distinct results.
    """
    from youtubesearchpython.__future__ import VideosSearch  # type: ignore

    results: list[dict] = []
    seen: set[str] = {vidid}  # always exclude the finished track

    # Strip common suffixes that bias the search back to the same song
    clean_title = title
    for suffix in (" (official video)", " (official audio)", " (lyrics)",
                   " (lyric video)", " (official)", " | official"):
        clean_title = clean_title.lower().replace(suffix, "")
    clean_title = clean_title.strip()

    queries = [
        f"{clean_title} similar",
        clean_title,
    ]

    for query in queries:
        if len(results) >= 4:
            break
        try:
            res = await VideosSearch(query, limit=10).next()
            for item in (res or {}).get("result", []):
                vid = item.get("id") or item.get("videoId") or ""
                t   = (item.get("title") or "").strip()
                if vid and t and vid not in seen:
                    results.append({"title": t, "vidid": vid})
                    seen.add(vid)
                if len(results) >= 4:
                    break
        except Exception:
            pass

    return results[:4]


# ── Inline keyboard ───────────────────────────────────────────────────────────

def _short_title(title: str) -> str:
    """Keep title readable inside a button — max ~40 chars with ellipsis."""
    return title[:40].rstrip() + "…" if len(title) > 40 else title


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

    # Delete suggestion message immediately
    try:
        await callback_query.message.delete()
    except Exception:
        pass
    _suggestions.pop(chat_id, None)

    await callback_query.answer(title[:50], show_alert=False)

    try:
        from AnonXMusic import YouTube
        from AnonXMusic.utils.stream.queue import put_queue
        from AnonXMusic.utils.database import add_active_chat, get_assistant, is_active_chat
        from AnonXMusic.utils.thumbnails import get_thumb
        from AnonXMusic.utils.inline.play import stream_markup
        from AnonXMusic.core.call import Anony
        from pytgcalls.types.input_stream import AudioPiped
        from pytgcalls.types.input_stream.quality import HighQualityAudio

        # YouTube.details returns: title, duration_min, dur_sec, thumbnail, vidid
        track_title, duration, _dur_sec, thumbnail, _vid = await YouTube.details(vidid, True)
        user_mention = callback_query.from_user.mention

        # Re-fetch strings (language may differ from outer scope after delete)
        lang    = await get_lang(chat_id)
        strings = get_string(lang)

        if await is_active_chat(chat_id):
            file_path, _direct = await YouTube.download(vidid, None, videoid=True, video=False)
            if not file_path:
                return await app.send_message(chat_id, strings["related_6"])
            await put_queue(
                chat_id, chat_id, file_path, track_title, duration,
                user_mention, vidid, user_id, "audio",
                thumbnail=thumbnail,
            )
            await app.send_message(chat_id, strings["related_4"].format(track_title))
        else:
            file_path, _direct = await YouTube.download(vidid, None, videoid=True, video=False)
            if not file_path:
                return await app.send_message(chat_id, strings["related_6"])
            stream    = AudioPiped(file_path, HighQualityAudio())
            assistant = await get_assistant(chat_id)
            await Anony.join_call(chat_id, assistant, stream, video_stream=False)
            await put_queue(
                chat_id, chat_id, file_path, track_title, duration,
                user_mention, vidid, user_id, "audio",
                thumbnail=thumbnail,
            )
            from AnonXMusic.utils.database import music_on
            await music_on(chat_id)
            await add_active_chat(chat_id)
            img     = await get_thumb(vidid)
            caption = strings["stream_1"].format(
                f"https://youtube.com/watch?v={vidid}",
                track_title[:23],
                duration,
                user_mention,
            )
            button = stream_markup(strings, chat_id)
            await app.send_photo(
                chat_id,
                photo=img,
                caption=caption,
                reply_markup=InlineKeyboardMarkup(button),
            )

    except Exception as e:
        lang = await get_lang(chat_id)
        s    = get_string(lang)
        await app.send_message(chat_id, s["related_7"].format(e))
