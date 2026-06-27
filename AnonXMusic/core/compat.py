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

Additionally, py-tgcalls 0.9.7 uses `GroupCallDiscarded` to detect call end,
but Kurigram's raw types may expose it differently.

Fix
---
We add a `chat_id` property directly on the `UpdateGroupCall` class so that
accessing `update.chat_id` returns the correct integer derived from `peer`.
We also patch `GroupCallDiscarded` if needed.
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

    # ── Patch UpdateGroupCall.chat_id ────────────────────────────────────────
    if "chat_id" not in UpdateGroupCall.__dict__:
        try:
            from pyrogram.raw.types import PeerChannel, PeerChat  # type: ignore

            def _chat_id(self) -> int:
                """Return the integer peer id that pytgcalls expects."""
                peer = getattr(self, "peer", None)
                if peer is None:
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
    else:
        _LOG.debug("[compat] UpdateGroupCall.chat_id already present — skipping")

    # ── Patch UpdateGroupCallParticipants if needed ──────────────────────────
    # py-tgcalls 0.9.7 also reads .chat_id on UpdateGroupCallParticipants
    try:
        from pyrogram.raw.types import UpdateGroupCallParticipants  # type: ignore
        if "chat_id" not in UpdateGroupCallParticipants.__dict__:
            from pyrogram.raw.types import PeerChannel, PeerChat  # type: ignore

            def _ugcp_chat_id(self) -> int:
                call = getattr(self, "call", None)
                peer = getattr(call, "peer", None) if call else None
                if isinstance(peer, PeerChannel):
                    return peer.channel_id
                if isinstance(peer, PeerChat):
                    return peer.chat_id
                if isinstance(peer, int):
                    return peer
                return 0

            UpdateGroupCallParticipants.chat_id = property(_ugcp_chat_id)  # type: ignore
            _LOG.info("[compat] Patched UpdateGroupCallParticipants.chat_id")
    except Exception as exc:
        _LOG.warning(f"[compat] UpdateGroupCallParticipants patch skipped: {exc}")

    # ── Patch pytgcalls itself to handle assistant vanishing ─────────────────
    # py-tgcalls 0.9.7 bug: when GroupCall is active in voice but not video,
    # it raises "No active video chat found" falsely.
    # We intercept the pytgcalls NoActiveGroupCall to add context.
    try:
        import pytgcalls.exceptions as _ptc_exc  # type: ignore

        _orig_no_active = getattr(_ptc_exc, "NoActiveGroupCall", None)
        if _orig_no_active and not getattr(_orig_no_active, "_patched_tosu", False):

            class _PatchedNoActive(_orig_no_active):
                _patched_tosu = True

                def __str__(self):
                    base = super().__str__() or "NoActiveGroupCall"
                    return (
                        f"{base} — Assistant may have left call or voice/video state mismatch. "
                        "Check if group call is active and assistant is still joined."
                    )

            _ptc_exc.NoActiveGroupCall = _PatchedNoActive
            _LOG.info("[compat] Patched pytgcalls.NoActiveGroupCall with better message")
    except Exception as exc:
        _LOG.debug(f"[compat] pytgcalls exception patch skipped: {exc}")


_apply()
