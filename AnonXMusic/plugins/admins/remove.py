from pyrogram import filters
from pyrogram.types import Message

from AnonXMusic import app
from AnonXMusic.misc import db
from AnonXMusic.utils.decorators import AdminRightsCheck
from AnonXMusic.utils.inline import close_markup
from AnonXMusic.utils.stream.autoclear import auto_clean
from config import BANNED_USERS


@app.on_message(
    filters.command(["remove", "cremove"]) & filters.group & ~BANNED_USERS
)
@AdminRightsCheck
async def remove_from_queue(client, message: Message, _, chat_id):
    """Remove a specific track from the queue by position without stopping playback.

    Usage: /remove <position>
    Position 0 = currently playing track (cannot be removed via this command).
    Position 1+ = queued tracks.
    """
    # Require a position argument
    if len(message.command) < 2:
        return await message.reply_text(_["remove_1"])

    arg = message.command[1].strip()
    if not arg.isnumeric():
        return await message.reply_text(_["remove_2"])

    position = int(arg)

    # Position 0 is the currently playing track — cannot be removed
    if position == 0:
        return await message.reply_text(_["remove_3"])

    check = db.get(chat_id)
    if not check:
        return await message.reply_text(_["queue_2"])

    # position is 1-based for queued tracks; index into db is position itself
    # (db[0] = now playing, db[1] = first queued, etc.)
    if position >= len(check):
        max_pos = len(check) - 1
        return await message.reply_text(_["remove_4"].format(max_pos))

    # Pop the track at the given index
    try:
        popped = check.pop(position)
    except IndexError:
        return await message.reply_text(_["remove_5"])

    # Clean up cached file if no longer needed
    if popped:
        await auto_clean(popped)

    title = popped.get("title", "Unknown").title()
    requester = popped.get("by", "Unknown")

    await message.reply_text(
        _["remove_6"].format(position, title, requester, message.from_user.mention),
        reply_markup=close_markup(_),
    )
