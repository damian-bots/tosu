from pyrogram import Client, filters
from pyrogram.types import Message
from pyrogram.enums import ParseMode
from AnonXMusic import app
import config

TEXT = f"""
🔒 **Privacy Policy for {app.mention}**

We value your privacy and are committed to protecting your personal information when you use our Telegram voice chat player bot.

**What Data We Collect:**
We do **not** collect or store any personal data such as your name, phone number, messages, or media. The bot only processes commands and streams audio as requested, using Telegram's secure infrastructure.

**How We Use Your Data:**
Any data processed during your interaction with the bot (such as your commands or voice chat activity) remains confidential and is used solely to provide the requested services. We do **not** monitor, record, or log your conversations or actions.

**Data Sharing and Selling:**
We respect your trust. We do **not** share, sell, or distribute any information to third parties, advertisers, or other organizations. Your data remains private and is never used for any unofficial purposes.

**Security Measures:**
Our bot operates within Telegram’s secure ecosystem, which employs end-to-end encryption for voice chats and messages. We take no additional steps to access or store your private data.

**Your Control:**
You maintain full control over your Telegram account and can revoke access or delete your data at any time by simply stopping the use of this bot or removing it from your chats.

**Updates to this Policy:**
This privacy policy may be updated from time to time to reflect changes in practices or regulations. We encourage you to review this policy periodically.

**Contact Us:**
If you have questions or concerns about your privacy or how we handle data, please reach out to our support team for assistance.

For more details, please visit our official Privacy Policy here: [Privacy Policy](https://telegra.ph/Privacy-Policy-Bot-Hub-12-18-2).

Thank you for trusting {app.mention} with your Telegram voice chat experience. We are dedicated to providing a safe, secure, and private environment.
"""

@app.on_message(filters.command("privacy"))
async def privacy(client, message: Message):
    await message.reply_text(TEXT, parse_mode=ParseMode.MARKDOWN, disable_web_page_preview=True)
