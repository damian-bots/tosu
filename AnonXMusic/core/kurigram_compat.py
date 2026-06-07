"""
kurigram_compat.py
~~~~~~~~~~~~~~~~~~
Monkey-patches pytgcalls' pyrogram_client so it works correctly with
Kurigram 2.2.x (and any Pyrogram fork where `UpdateGroupCall` no longer
carries a `chat_id` attribute but exposes the peer via `call.peer`).

Import this module ONCE, as early as possible (ideally in __init__.py or
__main__.py), before PyTgCalls clients are started.
"""

import logging

LOGGER = logging.getLogger(__name__)


def _patch_pyrogram_client() -> None:
    """Patch pytgcalls.mtproto.pyrogram_client to be Kurigram-2.2.x safe."""
    try:
        import pytgcalls.mtproto.pyrogram_client as _mod
    except ImportError:
        LOGGER.warning("[kurigram_compat] pytgcalls not installed – skipping patch.")
        return

    original_on_update = _mod.PyrogramClient.on_update

    async def _safe_on_update(self, update, users, chats):
        """
        Kurigram 2.2.x changed UpdateGroupCall: the `chat_id` attribute was
        removed; the peer is now inside `update.call.peer` or `update.peer`.

        pytgcalls 0.9.x does:
            chat_id = self.chat_id(data2[update.chat_id])
        which raises AttributeError when `chat_id` is missing.

        We add the attribute back before forwarding to the original handler.
        """
        from pyrogram.raw.types import (
            UpdateGroupCall,
            InputPeerChannel,
            InputPeerChat,
            PeerChannel,
            PeerChat,
        )

        if isinstance(update, UpdateGroupCall) and not hasattr(update, "chat_id"):
            # Kurigram stores the peer in update.peer (added in 2.2.x)
            peer = getattr(update, "peer", None)
            if peer is None:
                # fall back: look inside the GroupCall object itself
                call = getattr(update, "call", None)
                peer = getattr(call, "peer", None) if call else None

            resolved = None
            if isinstance(peer, (PeerChannel,)):
                resolved = peer.channel_id
            elif isinstance(peer, (PeerChat,)):
                resolved = peer.chat_id
            elif isinstance(peer, int):
                resolved = peer

            if resolved is not None:
                try:
                    update.chat_id = resolved
                except AttributeError:
                    # read-only slot — use object.__setattr__
                    object.__setattr__(update, "chat_id", resolved)

        try:
            return await original_on_update(self, update, users, chats)
        except AttributeError as exc:
            if "chat_id" in str(exc):
                LOGGER.debug(
                    "[kurigram_compat] Suppressed UpdateGroupCall.chat_id "
                    f"AttributeError: {exc}"
                )
                return
            raise

    _mod.PyrogramClient.on_update = _safe_on_update
    LOGGER.info("[kurigram_compat] Patched pytgcalls pyrogram_client for Kurigram 2.2.x")


_patch_pyrogram_client()
