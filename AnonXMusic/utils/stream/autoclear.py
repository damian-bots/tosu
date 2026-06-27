import asyncio
import os

from config import autoclean


async def auto_clean(popped):
    """
    Clean up downloaded files that are no longer needed.
    Fixed: properly handles all cases including speed_path files.
    """
    if not popped:
        return
    try:
        # Clean main file
        rem = popped.get("file") or ""
        _try_remove_file(rem)

        # Clean speed-adjusted file if present
        speed_path = popped.get("speed_path") or ""
        if speed_path and speed_path != rem:
            _try_remove_file(speed_path)

    except Exception:
        pass


def _try_remove_file(path: str) -> None:
    """Remove a file from disk if it's safe to do so."""
    if not path:
        return
    # Never remove URLs, in-memory markers, or special identifiers
    if path.startswith(("http", "vid_", "live_", "index_", "yt_search_")):
        return
    try:
        if path in autoclean:
            autoclean.remove(path)
        count = autoclean.count(path)
        if count == 0 and os.path.isfile(path):
            os.remove(path)
    except Exception:
        pass
