"""
kurigram_compat.py
~~~~~~~~~~~~~~~~~~
Fixes the Kurigram 2.2.x + pytgcalls 0.9.7 incompatibility.

Root cause
----------
Kurigram 2.2.x removed the `chat_id` shortcut attribute from the
`UpdateGroupCall` TL type.  pytgcalls 0.9.7 still reads it directly:

    chat_id = self.chat_id(data2[update.chat_id])   # line 97

This raises:
    AttributeError: 'UpdateGroupCall' object has no attribute 'chat_id'

Fix strategy
------------
Rather than trying to monkey-patch the pytgcalls method (which is
registered as a decorated handler and not a plain class attribute), we
patch the Pyrogram *dispatcher* so that every raw update is pre-processed
before any handler sees it.  When an UpdateGroupCall arrives without
`chat_id`, we reconstruct it from `update.peer` (the field Kurigram 2.2.x
uses instead) and inject it back.  pytgcalls then reads it fine.

This module is imported at the very top of AnonXMusic/__init__.py.
"""

import logging

LOGGER = logging.getLogger(__name__)


def _patch_dispatcher() -> None:
    """
    Wrap Pyrogram's Dispatcher.handler_worker so every raw update gets
    `chat_id` injected into UpdateGroupCall objects before handlers run.
    """
    try:
        import pyrogram.dispatcher as _disp_mod
        from pyrogram.dispatcher import Dispatcher
    except ImportError:
        LOGGER.warning("[kurigram_compat] pyrogram not available — skipping patch.")
        return

    original_handler_worker = Dispatcher.handler_worker

    async def _patched_handler_worker(self, lock):
        """
        Wraps the original handler_worker.  We can't easily intercept
        individual updates here, so we use a different strategy below.
        """
        return await original_handler_worker(self, lock)

    # Strategy: patch at the raw-update level by wrapping the dispatcher's
    # update processing.  The cleanest hook is to wrap the *raw_update*
    # handler resolution inside Pyrogram's dispatcher loop.
    #
    # Pyrogram 2.x calls dispatcher.updates_queue.get() then dispatches.
    # The actual per-update call is  handler.callback(client, *args)
    # where for RawUpdateHandlers args = (update, users, chats).
    #
    # We patch at the source: override __init_subclass__ on UpdateGroupCall
    # so any instance automatically synthesises chat_id from peer.

    try:
        from pyrogram.raw.types import UpdateGroupCall as _UGC
        from pyrogram.raw.types import PeerChannel, PeerChat

        _original_ugc_init = _UGC.__init__

        def _patched_ugc_init(self, *args, **kwargs):
            _original_ugc_init(self, *args, **kwargs)
            _inject_chat_id(self)

        def _inject_chat_id(obj):
            if hasattr(obj, "chat_id"):
                return   # already present (older Kurigram / stock Pyrogram)
            peer = getattr(obj, "peer", None)
            if peer is None:
                call = getattr(obj, "call", None)
                peer = getattr(call, "peer", None) if call else None
            if peer is None:
                return
            if isinstance(peer, PeerChannel):
                resolved = peer.channel_id
            elif isinstance(peer, PeerChat):
                resolved = peer.chat_id
            elif isinstance(peer, int):
                resolved = peer
            else:
                return
            try:
                obj.chat_id = resolved
            except (AttributeError, TypeError):
                try:
                    object.__setattr__(obj, "chat_id", resolved)
                except Exception:
                    pass

        _UGC.__init__ = _patched_ugc_init
        LOGGER.info(
            "[kurigram_compat] Patched UpdateGroupCall.__init__ "
            "to inject chat_id from peer (Kurigram 2.2.x compat)."
        )

    except Exception as exc:
        LOGGER.warning(f"[kurigram_compat] UpdateGroupCall patch failed: {exc}")
        return

    # Secondary safety net: also wrap the dispatcher so that any
    # UpdateGroupCall that somehow slips through without chat_id is caught
    # at dispatch time and the AttributeError is swallowed gracefully.
    try:
        original_hw = Dispatcher.handler_worker.__wrapped__ \
            if hasattr(Dispatcher.handler_worker, "__wrapped__") \
            else Dispatcher.handler_worker

        import asyncio
        import functools

        @functools.wraps(original_hw)
        async def _safe_hw(self, lock):
            try:
                return await original_hw(self, lock)
            except AttributeError as exc:
                if "UpdateGroupCall" in str(type(exc).__name__) or "chat_id" in str(exc):
                    LOGGER.debug(
                        f"[kurigram_compat] Suppressed dispatcher AttributeError: {exc}"
                    )
                    return
                raise

        Dispatcher.handler_worker = _safe_hw
        LOGGER.info("[kurigram_compat] Wrapped Dispatcher.handler_worker as safety net.")
    except Exception as exc:
        LOGGER.debug(f"[kurigram_compat] handler_worker wrap skipped: {exc}")


_patch_dispatcher()
