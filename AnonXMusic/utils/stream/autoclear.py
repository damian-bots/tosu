import asyncio
import os

from config import autoclean


async def auto_clean(popped):
    """Remove a downloaded file from disk if it is no longer referenced in autoclean."""
    if not popped:
        return
    try:
        rem = popped.get("file", "")
        if not rem:
            return

        # Remove this entry from the autoclean tracking list
        try:
            autoclean.remove(rem)
        except ValueError:
            pass

        # Only delete disk files — not stream marker strings or HTTP URLs
        is_marker = any(tag in rem for tag in ("vid_", "live_", "index_"))
        is_url    = rem.startswith("http://") or rem.startswith("https://")

        if is_marker or is_url:
            return

        # If no more references in autoclean, delete the file
        if rem not in autoclean:
            if os.path.isfile(rem):
                try:
                    await asyncio.get_event_loop().run_in_executor(None, os.remove, rem)
                except OSError:
                    pass
    except Exception:
        pass
