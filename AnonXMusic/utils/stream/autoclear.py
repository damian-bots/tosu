import logging
import os

from config import autoclean

LOGGER = logging.getLogger(__name__)


async def auto_clean(popped):
    try:
        rem = popped["file"]
        autoclean.remove(rem)
        count = autoclean.count(rem)
        if count == 0:
            if "vid_" not in rem or "live_" not in rem or "index_" not in rem:
                try:
                    os.remove(rem)
                except OSError as e:
                    LOGGER.debug(f"Failed to remove file {rem}: {e}")
    except Exception as e:
        LOGGER.debug(f"auto_clean failed: {e}")
