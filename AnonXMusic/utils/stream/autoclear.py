# ╔══════════════════════════════════════════════════════════════════╗
# ║        Copyright © tusar404 — All Rights Reserved               ║
# ║     AnonXMusic · Telegram Music Bot · Powered by PyTgCalls      ║
# ║        Unauthorized copying or distribution is prohibited        ║
# ╚══════════════════════════════════════════════════════════════════╝

import os

from config import autoclean


async def auto_clean(popped):
    try:
        rem = popped["file"]
        autoclean.remove(rem)
        count = autoclean.count(rem)
        if count == 0:
            if "vid_" not in rem or "live_" not in rem or "index_" not in rem:
                try:
                    os.remove(rem)
                except:
                    pass
    except:
        pass
