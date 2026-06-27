"""
sys.py — System / startup helpers + runtime stats.
"""
import asyncio
import glob
import logging
import os
import time
from datetime import datetime

import psutil

_LOG = logging.getLogger(__name__)
_STALE_AGE_SECS = 3 * 3600  # 3 hours


def cleanup_downloads(directory: str = "downloads") -> int:
    removed = 0
    if not os.path.isdir(directory):
        return 0
    now = time.time()
    for pattern in ("*.mp3", "*.mp4", "*.webm", "*.m4a", "*.opus"):
        for path in glob.glob(os.path.join(directory, pattern)):
            try:
                if now - os.path.getmtime(path) > _STALE_AGE_SECS:
                    os.remove(path)
                    removed += 1
            except OSError:
                pass
    if removed:
        _LOG.info(f"[startup] Cleaned {removed} stale download file(s).")
    return removed


def cleanup_playback_cache(directory: str = "playback") -> int:
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


_BOOT_TIME = datetime.now()


def _fmt_uptime() -> str:
    delta = datetime.now() - _BOOT_TIME
    days  = delta.days
    hours, rem = divmod(delta.seconds, 3600)
    mins, secs  = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h {mins}m"
    if hours:
        return f"{hours}h {mins}m {secs}s"
    return f"{mins}m {secs}s"


async def bot_sys_stats():
    """
    Returns (uptime_str, cpu_str, ram_str, disk_str).
    Called by /ping to display live system stats.
    Runs psutil calls in executor to avoid blocking the event loop.
    """
    loop = asyncio.get_event_loop()

    def _collect():
        cpu  = psutil.cpu_percent(interval=0.2)
        vm   = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        return cpu, vm, disk

    cpu_pct, vm, disk = await loop.run_in_executor(None, _collect)

    uptime   = _fmt_uptime()
    cpu_str  = f"{cpu_pct}%"
    ram_str  = f"{round(vm.used / (1024**3), 1)}GB / {round(vm.total / (1024**3), 1)}GB ({vm.percent}%)"
    disk_str = f"{round(disk.used / (1024**3), 1)}GB / {round(disk.total / (1024**3), 1)}GB ({disk.percent}%)"

    return uptime, cpu_str, ram_str, disk_str
