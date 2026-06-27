from pyrogram import Client

import config
from ..logging import LOGGER

assistants = []
assistantids = []


def _make_assistant(name: str, session_string: str) -> Client:
    return Client(
        name=name,
        api_id=config.API_ID,
        api_hash=config.API_HASH,
        session_string=session_string,
        # CRITICAL: no_updates=True saves massive CPU/RAM on busy bots.
        # Assistants only need to handle group calls — they don't need to
        # process any Telegram updates (messages, etc.).
        no_updates=True,
    )


class Userbot(Client):
    def __init__(self):
        self.one   = _make_assistant("AnonXAss1", str(config.STRING1))
        self.two   = _make_assistant("AnonXAss2", str(config.STRING2))
        self.three = _make_assistant("AnonXAss3", str(config.STRING3))
        self.four  = _make_assistant("AnonXAss4", str(config.STRING4))
        self.five  = _make_assistant("AnonXAss5", str(config.STRING5))

    async def _start_one(self, client: Client, number: int) -> bool:
        """Start a single assistant client. Returns True on success."""
        try:
            await client.start()
        except Exception as e:
            LOGGER(__name__).error(f"Assistant {number} failed to start: {e}")
            return False

        try:
            await client.send_message(config.LOGGER_ID, f"✅ Assistant #{number} started.")
        except Exception:
            LOGGER(__name__).error(
                f"Assistant {number} cannot access log group. "
                "Make sure it is added and promoted as admin."
            )
            exit()

        client.id         = client.me.id
        client.name       = client.me.mention
        client.first_name = client.me.first_name
        client.username   = client.me.username
        assistants.append(number)
        assistantids.append(client.id)
        LOGGER(__name__).info(f"Assistant #{number} started as {client.first_name}")
        return True

    async def start(self):
        LOGGER(__name__).info("Starting Assistants...")
        pairs = [
            (self.one,   1, config.STRING1),
            (self.two,   2, config.STRING2),
            (self.three, 3, config.STRING3),
            (self.four,  4, config.STRING4),
            (self.five,  5, config.STRING5),
        ]
        for client, number, string in pairs:
            if string:
                await self._start_one(client, number)

    async def stop(self):
        LOGGER(__name__).info("Stopping Assistants...")
        pairs = [
            (self.one,   config.STRING1),
            (self.two,   config.STRING2),
            (self.three, config.STRING3),
            (self.four,  config.STRING4),
            (self.five,  config.STRING5),
        ]
        for client, string in pairs:
            if string:
                try:
                    await client.stop()
                except Exception:
                    pass
