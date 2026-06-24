"""
AnonXMusic/plugins/play/splay.py
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
/splay <song name>  — Search Spotify by name, fetch metadata instantly,
                       download via API-2, then play/queue.

Flow:
  1. /splay <query>
  2. Search Spotify via API-2 /api/search → show match details instantly
  3. Download via API-2 (AES-CTR decrypt for Spotify)
  4. Feed into stream() — reuses all queue/NowPlaying logic:
       • If chat already active → adds to queue, edits mystic to show queue position
       • If chat is fresh      → joins call, edits mystic to Now Playing card
"""

import logging
import os
import traceback as _tb

from pyrogram import filters
from pyrogram.types import Message

import config
from AnonXMusic import Spotify, app
from AnonXMusic.utils.decorators.language import language
from AnonXMusic.utils.formatters import seconds_to_min
from AnonXMusic.utils.stream.stream import stream
from config import BANNED_USERS

LOGGER = logging.getLogger(__name__)


@app.on_message(
    filters.command(["splay", "sp"]) & filters.group & ~BANNED_USERS
)
@language
async def splay_command(client, message: Message, _):
    """Search Spotify by song name and play via API-2."""

    if len(message.command) < 2:
        return await message.reply_text(
            "🎵 <b>Spotify Play</b>\n\n"
            "<b>Usage:</b> <code>/splay song name</code>\n"
            "<b>Example:</b> <code>/splay Blinding Lights</code>",
            quote=True,
        )

    if not config.ENABLE_SPOTIFY:
        return await message.reply_text(
            "❌ Spotify is disabled on this bot.", quote=True
        )

    if not config.API_URL2 or not config.API_KEY2:
        return await message.reply_text(
            "❌ <b>API-2 not configured.</b>\n"
            "Spotify search requires <code>API_URL2</code> & <code>API_KEY2</code>.",
            quote=True,
        )

    query     = message.text.split(None, 1)[1].strip()
    user_id   = message.from_user.id
    user_name = message.from_user.first_name
    chat_id   = message.chat.id

    mystic = await message.reply_text(
        f"🔍 <b>Searching Spotify for:</b> <i>{query}</i>...",
        quote=True,
    )

    # ── 1. Search via API-2 ──────────────────────────────────────────────────
    try:
        from AnonXMusic.platforms.Api import ApiPlatform
        api = ApiPlatform()
        search_result = await api.search(query, limit=5, platform="spotify")
    except Exception as exc:
        LOGGER.error(
            f"splay: API-2 search failed for {query!r}:\n"
            + "".join(_tb.format_exception(type(exc), exc, exc.__traceback__))
        )
        return await mystic.edit_text(
            "❌ <b>Spotify search failed.</b>\n"
            "API-2 is unreachable. Please try again later."
        )

    if not search_result:
        return await mystic.edit_text("❌ No results found on Spotify.")

    tracks = (
        search_result.get("results")
        or search_result.get("tracks")
        or search_result.get("items")
        or []
    )
    if not tracks:
        return await mystic.edit_text(
            f"❌ No results found for <b>{query}</b> on Spotify."
        )

    song = tracks[0]
    title        = song.get("title") or song.get("name") or "Unknown"
    track_url    = song.get("url") or ""
    track_id     = song.get("id") or track_url
    duration_sec = int(song.get("duration") or 0)
    thumbnail    = song.get("thumbnail") or config.SPOTIFY_PLAYLIST_IMG_URL
    artist       = song.get("channel") or song.get("artist") or ""
    duration_min = seconds_to_min(duration_sec) if duration_sec > 0 else "N/A"

    if not track_url:
        return await mystic.edit_text(
            f"❌ No playable Spotify URL found for <b>{title}</b>.\n"
            "Try a more specific search term."
        )

    if duration_sec > config.DURATION_LIMIT:
        return await mystic.edit_text(
            f"❌ <b>Track too long.</b>\n\n"
            f"<b>{title}</b> is {duration_min} — max allowed is "
            f"{config.DURATION_LIMIT_MIN} min."
        )

    # ── 2. Show match instantly ──────────────────────────────────────────────
    artist_line = f"\n🎤 <b>Artist:</b> {artist}" if artist else ""
    await mystic.edit_text(
        f"🎵 <b>Found on Spotify</b>\n\n"
        f"🎶 <b>{title}</b>{artist_line}\n"
        f"⏱ <b>Duration:</b> {duration_min}\n\n"
        f"⬇️ <i>Downloading via API-2...</i>",
        disable_web_page_preview=True,
    )

    # ── 3. Download via API-2 ────────────────────────────────────────────────
    try:
        file_path = await api.download(track_url)
    except Exception as exc:
        LOGGER.error(
            f"splay: API-2 download failed for {track_url!r}:\n"
            + "".join(_tb.format_exception(type(exc), exc, exc.__traceback__))
        )
        file_path = None

    if not file_path:
        return await mystic.edit_text(
            f"❌ <b>Download Failed</b>\n\n"
            f"Could not download <b>{title}</b> from Spotify.\n"
            f"The track may be unavailable or API-2 is having issues.\n\n"
            f"Try: <code>/play {title}</code> to search YouTube instead."
        )

    # Ensure we have an absolute path (pytgcalls / ffprobe requires it)
    if not os.path.isabs(file_path):
        file_path = os.path.join(os.getcwd(), file_path)

    if not os.path.isfile(file_path):
        return await mystic.edit_text(
            f"❌ <b>File Not Found</b>\n\n"
            f"The downloaded file for <b>{title}</b> could not be located.\n"
            f"Try: <code>/play {title}</code> to search YouTube instead."
        )

    # ── 4. Feed into stream() ── mystic is edited in-place by stream() ───────
    details = {
        "title":        title,
        "link":         track_url,
        "vidid":        track_id,
        "duration_min": duration_min,
        "thumb":        thumbnail,
        "file_path":    file_path,
    }

    try:
        await stream(
            _,
            mystic,
            user_id,
            details,
            chat_id,
            user_name,
            message.chat.id,
            video=None,
            streamtype="spotify",
            spotify=True,
            forceplay=None,
        )
    except Exception as exc:
        LOGGER.error(
            f"splay: stream() error for {title!r}:\n"
            + "".join(_tb.format_exception(type(exc), exc, exc.__traceback__))
        )
        return await mystic.edit_text(
            f"❌ <b>Playback Error</b>\n\n"
            f"Failed to start <b>{title}</b>.\n"
            f"<code>{type(exc).__name__}: {exc}</code>"
        )
