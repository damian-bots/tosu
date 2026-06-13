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
    Position 1 = currently playing track (cannot be removed via this command).
    Position 2+ = queued tracks.
    """
    # Require a position argument
    if len(message.command) < 2:
        return await message.reply_text(_["remove_1"])

    arg = message.command[1].strip()
    if not arg.isnumeric():
        return await message.reply_text(_["remove_2"])

    position = int(arg)

    # Position must be >= 2 (position 1 is the currently playing track)
    if position < 2:
        return await message.reply_text(_["remove_3"])

    check = db.get(chat_id)
    if not check:
        return await message.reply_text(_["queue_2"])

    # Convert to 0-based index
    index = position - 1

    if index >= len(check):
        # Tell the user the valid range
        max_pos = len(check)
        return await message.reply_text(_["remove_4"].format(max_pos))

    # Pop the track at the given position
    try:
        popped = check.pop(index)
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
