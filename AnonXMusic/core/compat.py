"""
compat.py
~~~~~~~~~
Kurigram 2.2.23 + py-tgcalls 0.9.7 compatibility patch.

Root cause
----------
py-tgcalls 0.9.7 reads `update.chat_id` on every `UpdateGroupCall` TL object:

    # pytgcalls/mtproto/pyrogram_client.py, line 97
    chat_id = self.chat_id(data2[update.chat_id])

Kurigram 2.2.23 removed the `chat_id` shortcut and uses `update.peer`
(a PeerChannel or PeerChat instance) instead.

Fix
---
We add a `chat_id` property directly on the `UpdateGroupCall` class so that
accessing `update.chat_id` returns the correct integer derived from `peer`.
This is a class-level descriptor and works even if the TL type uses __slots__.

This module must be imported before any PyTgCalls client is constructed.
"""

import logging

_LOG = logging.getLogger(__name__)


def _apply():
    try:
        from pyrogram.raw.types import UpdateGroupCall  # type: ignore
    except ImportError:
        _LOG.warning("[compat] pyrogram not importable — skipping patch")
        return

    # If the attribute already exists as a real slot/field, nothing to do.
    if "chat_id" in UpdateGroupCall.__dict__:
        _LOG.debug("[compat] UpdateGroupCall.chat_id already present — skipping patch")
        return

    try:
        from pyrogram.raw.types import PeerChannel, PeerChat  # type: ignore

        def _chat_id(self) -> int:  # type: ignore[override]
            """Return the integer peer id that pytgcalls expects."""
            peer = getattr(self, "peer", None)
            if peer is None:
                # Some builds put peer inside the nested Call object.
                call = getattr(self, "call", None)
                peer = getattr(call, "peer", None) if call else None
            if isinstance(peer, PeerChannel):
                return peer.channel_id
            if isinstance(peer, PeerChat):
                return peer.chat_id
            if isinstance(peer, int):
                return peer
            return 0

        UpdateGroupCall.chat_id = property(_chat_id)  # type: ignore[attr-defined]
        _LOG.info(
            "[compat] Patched UpdateGroupCall.chat_id "
            "(Kurigram 2.2.23 + py-tgcalls 0.9.7 fix applied)"
        )

    except Exception as exc:
        _LOG.error(f"[compat] Failed to patch UpdateGroupCall: {exc}")


_apply()
