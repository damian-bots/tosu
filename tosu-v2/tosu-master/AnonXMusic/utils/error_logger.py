# AnonXMusic · error_logger.py  (v1.0.4)
# Logs EVERY unhandled exception to terminal (stderr) AND to ERROR_LOG_ID.
# Also catches song download/search failures when called as a decorator.
#
# Usage:
#   @error_logger
#   async def foo(): ...
#
#   @error_logger(label="YouTube Download")
#   async def bar(): ...
#
# The decorator is fully transparent:
#   • Success  → return value passed through unchanged.
#   • Failure  → full traceback printed to terminal + sent to ERROR_LOG_ID,
#                then exception is RE-RAISED so callers handle it normally.

import asyncio
import functools
import html
import inspect
import traceback
from typing import Any, Callable, Optional

from pyrogram.enums import ParseMode

import config
from AnonXMusic.logging import LOGGER

_log = LOGGER(__name__)


def _truncate(text: str, limit: int = 3500) -> str:
    if len(text) <= limit:
        return text
    half = limit // 2
    return text[:half] + "\n…[truncated]…\n" + text[-half:]


def _format_args(func: Callable, args: tuple, kwargs: dict) -> str:
    try:
        sig = inspect.signature(func)
        bound = sig.bind(*args, **kwargs)
        bound.apply_defaults()
        lines = []
        for name, value in bound.arguments.items():
            if name in ("self", "cls", "_"):
                continue
            type_name = type(value).__name__
            if type_name in ("Message", "CallbackQuery", "InlineQuery", "Client", "PyTgCalls", "Update"):
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
    """Fire-and-forget: send formatted error to ERROR_LOG_ID."""
    from AnonXMusic import app  # lazy import to avoid circular

    try:
        bot_mention = app.mention or f"@{(await app.get_me()).username}"
    except Exception:
        bot_mention = "Bot"

    module    = getattr(func, "__module__", "unknown")
    func_name = label or func.__qualname__

    tb_text  = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
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
        _log.warning(f"[error_logger] Failed to send to ERROR_LOG_ID: {send_err}")


def error_logger(_func: Optional[Callable] = None, *, label: Optional[str] = None):
    """
    Decorator — use with or without arguments:

        @error_logger
        async def foo(): ...

        @error_logger(label="Spotify Download")
        async def bar(): ...
    """
    def decorator(func: Callable) -> Callable:
        if not asyncio.iscoroutinefunction(func):
            raise TypeError(f"@error_logger only decorates async functions, got {func!r}")

        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return await func(*args, **kwargs)
            except Exception as exc:
                # 1. Always print full traceback to terminal immediately
                _log.error(
                    f"[error_logger] Exception in {func.__qualname__}:\n"
                    + "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
                )
                # 2. Ship to ERROR_LOG_ID (non-blocking)
                asyncio.ensure_future(_send_error(func, exc, args, kwargs, label))
                # 3. Re-raise so callers can handle it
                raise

        return wrapper

    if _func is not None:
        return decorator(_func)
    return decorator
