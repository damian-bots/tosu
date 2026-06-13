HELP_1 = """<b><u>Admin Commands</u></b>

These commands can only be used by group admins or auth users.
Add <b>c</b> at the start of any command to use it for a connected channel instead of the group.

<code>/pause</code> — Pause the currently playing stream.

<code>/resume</code> — Resume a paused stream from where it left off.

<code>/skip</code> — Skip the current track and start playing the next one in the queue.

<code>/end</code> or <code>/stop</code> — Stop the current stream and clear the entire queue.

<code>/player</code> — Open an interactive player panel with playback controls.

<code>/queue</code> — Show the list of all tracks currently queued up."""

HELP_2 = """<b><u>Auth Users</u></b>

Auth users are granted bot admin privileges inside the group without needing to be a Telegram group admin. This lets trusted members control playback without full admin access.

<code>/auth [username or user_id]</code> — Add a user to the auth list.

<code>/unauth [username or user_id]</code> — Remove a user from the auth list.

<code>/authusers</code> — Show the full list of auth users in this group."""

HELP_3 = """<b><u>Broadcast</u></b> <i>(Sudo only)</i>

Send a message to all chats and users the bot has served.

<code>/broadcast [message or reply]</code> — Broadcast a message to all served chats.

<b>Available flags:</b>

<code>-pin</code> — Pin the broadcasted message silently in served chats.
<code>-pinloud</code> — Pin the message and notify all members.
<code>-user</code> — Send the broadcast to users who have started the bot in private.
<code>-assistant</code> — Send the broadcast from the assistant account instead of the bot.
<code>-nobot</code> — Skip the bot and only use the assistant account for broadcasting.

Flags can be combined in a single command.
<b>Example:</b> <code>/broadcast -user -assistant -pin Testing broadcast</code>"""

HELP_4 = """<b><u>Chat Blacklist</u></b> <i>(Sudo only)</i>

Prevent specific chats from using the bot entirely.

<code>/blacklistchat [chat_id]</code> — Blacklist a chat. The bot will leave and refuse to rejoin.

<code>/whitelistchat [chat_id]</code> — Remove a chat from the blacklist, allowing it to use the bot again.

<code>/blacklistedchats</code> — Show the full list of currently blacklisted chats."""

HELP_5 = """<b><u>Block Users</u></b> <i>(Sudo only)</i>

Blocked users are ignored by the bot and cannot use any commands.

<code>/block [username or reply]</code> — Block a user from using the bot.

<code>/unblock [username or reply]</code> — Unblock a previously blocked user.

<code>/blockedusers</code> — Show the full list of blocked users."""

HELP_6 = """<b><u>Channel Play</u></b>

Stream audio or video directly into a channel's video chat.

<code>/cplay [song name or URL]</code> — Stream an audio track in the connected channel's video chat.

<code>/cvplay [song name or URL]</code> — Stream a video track in the connected channel's video chat.

<code>/cplayforce</code> or <code>/cvplayforce</code> — Stop the ongoing stream and immediately start the requested track in the channel.

<code>/channelplay [username or chat_id]</code> — Link a channel to this group so it can be controlled via group commands.

<code>/channelplay disable</code> — Unlink the connected channel."""

HELP_7 = """<b><u>Global Ban</u></b> <i>(Sudo only)</i>

Globally ban a user across all chats the bot serves and block them from using the bot.

<code>/gban [username or reply]</code> — Globally ban a user. They will be banned from all served chats and blacklisted from the bot.

<code>/ungban [username or reply]</code> — Remove the global ban from a user.

<code>/gbannedusers</code> — Show the full list of globally banned users."""

HELP_8 = """<b><u>Loop</u></b>

Repeat the current stream continuously or for a set number of times.

<code>/loop enable</code> — Enable infinite loop for the ongoing stream.

<code>/loop disable</code> — Disable looping and return to normal playback.

<code>/loop [number]</code> — Loop the current track a specific number of times. For example, <code>/loop 5</code> will repeat the track 5 times before moving on."""

HELP_9 = """<b><u>Maintenance</u></b> <i>(Sudo only)</i>

Manage bot logging and put the bot into maintenance mode when needed.

<code>/logs</code> — Fetch the latest log file from the bot.

<code>/logger enable</code> or <code>/logger disable</code> — Turn activity logging on or off. When enabled, the bot will send logs to the configured log channel.

<code>/maintenance enable</code> or <code>/maintenance disable</code> — Enable or disable maintenance mode. While in maintenance mode, only sudo users can interact with the bot."""

HELP_10 = """<b><u>Ping and Stats</u></b>

Basic commands to check the bot's status and performance.

<code>/start</code> — Start the bot or check if it is online.

<code>/help</code> — Open the help menu to explore all available commands.

<code>/ping</code> — Check the bot's response time and view live system stats such as CPU, RAM, and uptime.

<code>/stats</code> — View overall usage statistics including total served chats, users, and streams."""

HELP_11 = """<b><u>Play Commands</u></b>

Stream music or video in your group's voice chat. Supports YouTube, Spotify, Apple Music, SoundCloud, Deezer, JioSaavn and more.

<b>Prefixes:</b>
<b>v</b> — plays as video instead of audio.
<b>force</b> — immediately stops the current stream and plays the new request.

<code>/play [song name or URL]</code> — Search and stream an audio track in the voice chat.

<code>/vplay [song name or URL]</code> — Search and stream a video track in the voice chat.

<code>/playforce [song name or URL]</code> — Force play audio, skipping the current stream immediately.

<code>/vplayforce [song name or URL]</code> — Force play video, skipping the current stream immediately."""

HELP_12 = """<b><u>Shuffle</u></b>

Randomise the order of tracks in the current queue.

<code>/shuffle</code> — Randomly shuffle all tracks waiting in the queue. The currently playing track is not affected.

<code>/queue</code> — Show the updated queue after shuffling so you can see the new playback order."""

HELP_13 = """<b><u>Seek</u></b>

Jump to a specific position in the currently playing stream without stopping it.

<code>/seek [seconds]</code> — Skip forward by the given number of seconds. For example, <code>/seek 30</code> jumps 30 seconds ahead.

<code>/seekback [seconds]</code> — Rewind backward by the given number of seconds. For example, <code>/seekback 15</code> goes 15 seconds back."""

HELP_14 = """<b><u>Song Download</u></b>

Download tracks directly from YouTube as audio or video files.

<code>/song [song name or YouTube URL]</code> — Search YouTube for the track and send it as a downloadable file. You will be given the choice to download it as MP3 (audio) or MP4 (video)."""

HELP_15 = """<b><u>Speed Control</u></b> <i>(Admins only)</i>

Adjust the playback speed of the ongoing stream in real time.

<code>/speed</code> or <code>/playback</code> — Open the speed control panel for the group voice chat. You can slow down or speed up the current stream.

<code>/cspeed</code> or <code>/cplayback</code> — Open the speed control panel for a connected channel's video chat."""
