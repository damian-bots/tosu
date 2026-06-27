"""
sys.py — System / startup helpers.
Cleans up stale download files on every restart to prevent disk bloat.
"""
import asyncio
import glob
import logging
import os
import time

_LOG = logging.getLogger(__name__)

# Files older than this (in seconds) will be cleaned up on startup
_STALE_AGE_SECS = 3 * 3600  # 3 hours


def cleanup_downloads(directory: str = "downloads") -> int:
    """
    Delete stale media files from the downloads directory.
    Returns the number of files removed.
    """
    removed = 0
    if not os.path.isdir(directory):
        return 0
    now = time.time()
    for pattern in ("*.mp3", "*.mp4", "*.webm", "*.m4a", "*.opus"):
        for path in glob.glob(os.path.join(directory, pattern)):
            try:
                age = now - os.path.getmtime(path)
                if age > _STALE_AGE_SECS:
                    os.remove(path)
                    removed += 1
            except OSError:
                pass
    if removed:
        _LOG.info(f"[startup] Cleaned {removed} stale download file(s).")
    return removed


def cleanup_playback_cache(directory: str = "playback") -> int:
    """Delete all speed-modified playback files — they are always regenerated."""
    removed = 0
    if not os.path.isdir(directory):
        return 0
    for pattern in ("*.mp3", "*.mp4", "*.webm", "*.m4a"):
        for path in glob.glob(os.path.join(directory, "**", pattern), recursive=True):
            try:
                os.remove(path)
                removed += 1
            except OSError:
                pass
    if removed:
        _LOG.info(f"[startup] Cleaned {removed} playback cache file(s).")
    return removed
