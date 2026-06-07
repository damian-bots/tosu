"""
AnonXMusic · Error Logger
─────────────────────────
Provides `@error_logger` — a decorator that catches **every** unhandled
exception in any async function (download helpers, call helpers, stream
handlers, playlist fetchers, …) and ships a fully formatted traceback
straight to your ERROR_LOG_ID chat.

Format (Telegram HTML, <pre>…</pre> for code blocks):

    🚨 ERROR in <func_name>

    🤖 Bot: @username
    📌 Module: AnonXMusic.utils.stream.stream
    🔧 Function: stream

    📋 Context:
      chat_id = -1001234567890
      streamtype = spotify

    ❌ Exception: FileNotFoundError
    💬 Message: [Errno 2] No such file or directory: '/tmp/track.ogg'

    📄 Traceback:
    <pre>
    Traceback (most recent call last):
      File "...stream.py", line 142, in stream
        ...
    FileNotFoundError: [Errno 2] No such file or directory: '/tmp/track.ogg'
    </pre>

Usage
─────
    from AnonXMusic.utils.error_logger import error_logger

    @error_logger
    async def my_func(arg1, arg2):
        ...

    # with extra context labels
    @error_logger(label="YouTube Download")
    async def download_yt(vidid, mystic):
        ...

The decorator is transparent:
  • If the function succeeds, its return value is returned normally.
  • If it raises, the exception is logged to ERROR_LOG_ID **and then
    re-raised** so the caller's own error handling still works.
  • If the Telegram send itself fails (network down, bot banned from
    error chat, etc.) the send error is swallowed and the original
    exception is still re-raised — the logger must never swallow bugs.
"""

import asyncio
import functools
import html
import inspect
import sys
import traceback
from typing import Any, Callable, Optional

from pyrogram.enums import ParseMode

import config
from AnonXMusic.logging import LOGGER

_log = LOGGER(__name__)

# ── helpers ───────────────────────────────────────────────────────────────────

def _truncate(text: str, limit: int = 3500) -> str:
    """Telegram message limit is ~4096 chars; keep a safe margin."""
    if len(text) <= limit:
        return text
    half = limit // 2
    return text[:half] + "\n…[truncated]…\n" + text[-half:]


def _format_args(func: Callable, args: tuple, kwargs: dict) -> str:
    """
    Build a human-readable 'Context' block from the call arguments.
    We skip `self` / `cls` and any value that looks like a Pyrogram Message
    (those are huge objects; we just show the type name instead).
    """
    try:
        sig = inspect.signature(func)
        bound = sig.bind(*args, **kwargs)
        bound.apply_defaults()
        lines = []
        for name, value in bound.arguments.items():
            if name in ("self", "cls", "_"):
                continue
            # Avoid dumping giant Pyrogram objects
            type_name = type(value).__name__
            if type_name in (
                "Message", "CallbackQuery", "InlineQuery",
                "Client", "PyTgCalls", "Update",
            ):
                lines.append(f"  {name} = <{type_name}>")
            elif isinstance(value, (int, float, bool, str)) or value is None:
                lines.append(f"  {name} = {value!r}")
            else:
                lines.append(f"  {name} = <{type_name}>")
        return "\n".join(lines) if lines else "  (no inspectable args)"
    except Exception:
        return "  (could not inspect args)"


async def _send_error(
    func: Callable,
    exc: BaseException,
    args: tuple,
    kwargs: dict,
    label: Optional[str],
) -> None:
    """Fire-and-forget: send the formatted error to ERROR_LOG_ID."""
    # Import lazily to avoid circular imports at module load time
    from AnonXMusic import app  # noqa: PLC0415

    try:
        bot_mention = app.mention or f"@{(await app.get_me()).username}"
    except Exception:
        bot_mention = "Bot"

    module = getattr(func, "__module__", "unknown")
    func_name = label or func.__qualname__

    tb_text = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    ctx_text = _format_args(func, args, kwargs)

    text = (
        f"🚨 <b>ERROR in <code>{html.escape(func_name)}</code></b>\n\n"
        f"🤖 <b>Bot:</b> {bot_mention}\n"
        f"📌 <b>Module:</b> <code>{html.escape(module)}</code>\n"
        f"🔧 <b>Function:</b> <code>{html.escape(func.__name__)}</code>\n\n"
        f"📋 <b>Context:</b>\n<pre>{html.escape(ctx_text)}</pre>\n\n"
        f"❌ <b>Exception:</b> <code>{html.escape(type(exc).__name__)}</code>\n"
        f"💬 <b>Message:</b> <code>{html.escape(str(exc))}</code>\n\n"
        f"📄 <b>Traceback:</b>\n<pre>{html.escape(_truncate(tb_text))}</pre>"
    )

    try:
        await app.send_message(
            chat_id=config.ERROR_LOG_ID,
            text=text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
    except Exception as send_err:
        # Never let the logger itself swallow or mask the original exception
        _log.warning(f"[error_logger] Failed to send error to Telegram: {send_err}")


# ── public decorator ──────────────────────────────────────────────────────────

def error_logger(_func: Optional[Callable] = None, *, label: Optional[str] = None):
    """
    Decorator — can be used with or without arguments:

        @error_logger
        async def foo(): ...

        @error_logger(label="Spotify Download")
        async def bar(): ...
    """
    def decorator(func: Callable) -> Callable:
        if not asyncio.iscoroutinefunction(func):
            raise TypeError(
                f"@error_logger can only decorate async functions, got {func!r}"
            )

        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return await func(*args, **kwargs)
            except Exception as exc:
                # Log to console immediately (never loses the error even if TG send fails)
                _log.error(
                    f"[error_logger] Exception in {func.__qualname__}:\n"
                    + "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
                )
                # Ship to ERROR_LOG_ID (fire-and-forget, won't block the caller)
                asyncio.ensure_future(_send_error(func, exc, args, kwargs, label))
                # Always re-raise so the caller's except/AssistantErr flow works
                raise

        return wrapper

    # Called as @error_logger (no parens) — _func is the decorated function
    if _func is not None:
        return decorator(_func)

    # Called as @error_logger(...) — return the decorator
    return decorator
