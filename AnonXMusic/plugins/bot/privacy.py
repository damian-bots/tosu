from pyrogram import filters
from pyrogram.enums import ParseMode
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

import config
from AnonXMusic import app

_PRIVACY_TEXT = """
<b>🔐 Privacy Policy</b>

<b>{mention}</b> is a Telegram voice-chat music bot. By using it you agree to this policy. We take your privacy seriously and this document explains exactly what data we handle, why, and how.

━━━━━━━━━━━━━━━━━━━━━━━━
<b>📦 Data We Collect</b>
━━━━━━━━━━━━━━━━━━━━━━━━
We store only the minimum data required to operate the bot:

• <b>Chat IDs</b> — to remember per-group settings (language, play mode, loop, admin list, authorized users).
• <b>User IDs</b> — to identify who requested a track, for auth checks, and for global-ban enforcement.
• <b>Play queues</b> — current and recently played track metadata (title, duration, YouTube video ID) kept in memory while the bot is active in a voice chat.
• <b>Language preference</b> — stored per chat so the bot responds in the chosen language.
• <b>Session strings</b> — encrypted Telegram session strings for assistant accounts, used solely to join group voice chats. These are never logged or forwarded anywhere.

We do <b>not</b> collect, store, or process:
— Message text or captions
— Voice or audio recordings
— Photos, videos, or documents
— Personal information (name, phone number, email)
— Payment or billing information

━━━━━━━━━━━━━━━━━━━━━━━━
<b>🎯 How Data Is Used</b>
━━━━━━━━━━━━━━━━━━━━━━━━
All collected data is used <b>solely</b> to provide bot functionality within Telegram:

• Delivering the correct language strings to each group.
• Maintaining the play queue so tracks stream in order.
• Enforcing admin-only play restrictions per group setting.
• Preventing globally banned users from requesting tracks.
• Logging errors to a private admin channel for debugging only.

We do <b>not</b> use your data for advertising, analytics, profiling, or any commercial purpose.

━━━━━━━━━━━━━━━━━━━━━━━━
<b>🔗 Third-Party Services</b>
━━━━━━━━━━━━━━━━━━━━━━━━
To resolve and stream audio, the bot interacts with external services:

• <b>YouTube</b> — for searching and downloading audio/video tracks.
• <b>Spotify</b> — for resolving Spotify track/album/playlist links.
• <b>Apple Music</b> — for resolving Apple Music links.
• <b>SoundCloud, Deezer, JioSaavn, Tidal, Gaana</b> — for platform-specific playback.
• <b>Arc API</b> — our custom download API used to fetch media files.

These services have their own privacy policies. We do not control what data they collect when the bot queries them. We recommend reviewing their policies if you have concerns.

━━━━━━━━━━━━━━━━━━━━━━━━
<b>🗄️ Data Retention & Deletion</b>
━━━━━━━━━━━━━━━━━━━━━━━━
• <b>In-memory data</b> (queues, playback state) is cleared as soon as the bot leaves or is removed from a voice chat.
• <b>Database records</b> (chat settings, auth users, play preferences) persist until you remove the bot or request deletion.
• <b>To delete your data:</b> remove the bot from your group, then contact our support chat to request a full data purge for your chat ID or user ID.
• We aim to fulfil deletion requests within <b>7 days</b>.

━━━━━━━━━━━━━━━━━━━━━━━━
<b>🛡️ Security</b>
━━━━━━━━━━━━━━━━━━━━━━━━
• All communication runs over <b>Telegram's end-to-end encrypted infrastructure</b>.
• Assistant session strings are stored as environment variables or in a secure config — never in public code.
• Error logs sent to the admin channel contain only technical details (chat ID, exception type, traceback) — no message content or user data.
• Access to the database is restricted to the bot process only.

━━━━━━━━━━━━━━━━━━━━━━━━
<b>👤 Your Rights</b>
━━━━━━━━━━━━━━━━━━━━━━━━
Depending on your jurisdiction you may have the right to:

• <b>Access</b> — request a copy of data we hold for your chat/user ID.
• <b>Rectification</b> — correct inaccurate settings data.
• <b>Erasure</b> — request deletion of all stored data (see Retention section).
• <b>Portability</b> — receive your data in a structured format.
• <b>Objection</b> — opt out of non-essential data use (we collect only essentials, so there is little to object to).

To exercise any right, contact us via the support chat below.

━━━━━━━━━━━━━━━━━━━━━━━━
<b>👶 Children's Privacy</b>
━━━━━━━━━━━━━━━━━━━━━━━━
This bot is not directed at children under 13. We do not knowingly collect any data from users under 13. If you believe a child has used this bot and data has been collected, please contact us immediately for deletion.

━━━━━━━━━━━━━━━━━━━━━━━━
<b>📝 Policy Updates</b>
━━━━━━━━━━━━━━━━━━━━━━━━
We may update this policy as the bot evolves. Significant changes will be announced in the support channel. Continued use of the bot after a change constitutes acceptance of the updated policy.

━━━━━━━━━━━━━━━━━━━━━━━━
<b>📬 Contact Us</b>
━━━━━━━━━━━━━━━━━━━━━━━━
For questions, data requests, or concerns about your privacy, reach us via:
"""


@app.on_message(filters.command("privacy"))
async def privacy(_, message: Message):
    text = _PRIVACY_TEXT.format(mention=app.mention).strip()
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💬 Support Chat", url=config.SUPPORT_CHAT),
            InlineKeyboardButton("📢 Channel", url=config.SUPPORT_CHANNEL),
        ],
    ])
    await message.reply_text(
        text,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
        reply_markup=keyboard,
    )
