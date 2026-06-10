# ╔══════════════════════════════════════════════════════════════════╗
# ║        Copyright © tusar404 — All Rights Reserved               ║
# ║     AnonXMusic · Telegram Music Bot · Powered by PyTgCalls      ║
# ║        Unauthorized copying or distribution is prohibited        ║
# ╚══════════════════════════════════════════════════════════════════╝

"""Pre-mock heavy external dependencies so tests can import application
modules without needing pyrogram, motor, heroku3, etc. installed.
"""
import os
import sys
import types
from unittest.mock import MagicMock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("LOGGER_ID", "0")
os.environ.setdefault("MEDIA_CHANNEL_ID", "0")
os.environ.setdefault("BOT_TOKEN", "0:TEST")
os.environ.setdefault("MONGO_DB_URI", "mongodb://localhost:27017")
os.environ.setdefault("DB_URI", "mongodb://localhost:27017")
os.environ.setdefault("DB_NAME", "test")


def _make_stub(name: str, path: str = None) -> types.ModuleType:
    mod = types.ModuleType(name)
    if path:
        mod.__path__ = [path]
    else:
        mod.__path__ = []
    return mod


_STUBS = [
    "pyrogram",
    "pyrogram.client",
    "pyrogram.enums",
    "pyrogram.errors",
    "pyrogram.filters",
    "pyrogram.handlers",
    "pyrogram.types",
    "pyrogram.raw",
    "pyrogram.raw.functions",
    "motor",
    "motor.motor_asyncio",
    "heroku3",
    "py_tgcalls",
    "pytgcalls",
    "pytgcalls.types",
    "pytgcalls.types.input_stream",
    "pykeyboard",
    "pykeyboard.inline_keyboard",
    "youtubesearchpython",
    "youtubesearchpython.__future__",
    "spotipy",
    "spotipy.oauth2",
    "git",
]

for _name in _STUBS:
    if _name not in sys.modules:
        stub = _make_stub(_name)
        stub.__dict__.setdefault("Client", MagicMock())
        stub.__dict__.setdefault("filters", MagicMock())
        stub.__dict__.setdefault("errors", MagicMock())
        stub.__dict__.setdefault("ChatMemberStatus", MagicMock())
        stub.__dict__.setdefault("ParseMode", MagicMock())
        stub.__dict__.setdefault("MessageEntityType", MagicMock())
        stub.__dict__.setdefault("AsyncIOMotorClient", MagicMock())
        stub.__dict__.setdefault("SpotifyClientCredentials", MagicMock())
        stub.__dict__.setdefault("VideosSearch", MagicMock())
        stub.__dict__.setdefault("Repo", MagicMock())
        sys.modules[_name] = stub


_logging_mod = _make_stub("AnonXMusic.logging")
_mock_logger_cls = MagicMock()
_logging_mod.LOGGER = lambda name: _mock_logger_cls
sys.modules["AnonXMusic.logging"] = _logging_mod

_core_mod = _make_stub("AnonXMusic.core", os.path.join(REPO_ROOT, "AnonXMusic", "core"))
sys.modules["AnonXMusic.core"] = _core_mod

_bot_mod = _make_stub("AnonXMusic.core.bot")
_bot_mod.Anony = MagicMock()
sys.modules["AnonXMusic.core.bot"] = _bot_mod

_dir_mod = _make_stub("AnonXMusic.core.dir")
_dir_mod.dirr = MagicMock()
sys.modules["AnonXMusic.core.dir"] = _dir_mod

_git_mod = _make_stub("AnonXMusic.core.git")
_git_mod.git = MagicMock()
sys.modules["AnonXMusic.core.git"] = _git_mod

_ub_mod = _make_stub("AnonXMusic.core.userbot")
_ub_mod.Userbot = MagicMock()
_ub_mod.assistants = [1]
sys.modules["AnonXMusic.core.userbot"] = _ub_mod

_mongo_mod = _make_stub("AnonXMusic.core.mongo")
_mongo_mod.mongodb = MagicMock()
sys.modules["AnonXMusic.core.mongo"] = _mongo_mod

_misc_mod = _make_stub("AnonXMusic.misc")
_misc_mod.db = {}
_misc_mod.dbb = MagicMock()
_misc_mod.heroku = MagicMock()
_misc_mod.SUDOERS = MagicMock()
_misc_mod._boot_ = 0
sys.modules["AnonXMusic.misc"] = _misc_mod

_platforms_mod = _make_stub("AnonXMusic.platforms", os.path.join(REPO_ROOT, "AnonXMusic", "platforms"))
for _cls in ("AppleAPI", "CarbonAPI", "SoundAPI", "SpotifyAPI", "RessoAPI", "TeleAPI", "YouTubeAPI"):
    setattr(_platforms_mod, _cls, MagicMock())
sys.modules["AnonXMusic.platforms"] = _platforms_mod

_anon_mod = _make_stub("AnonXMusic", os.path.join(REPO_ROOT, "AnonXMusic"))
_anon_mod.app = MagicMock()
_anon_mod.userbot = MagicMock()
for _cls in ("Apple", "Carbon", "SoundCloud", "Spotify", "Resso", "Telegram", "YouTube"):
    setattr(_anon_mod, _cls, MagicMock())
sys.modules["AnonXMusic"] = _anon_mod

_utils_mod = _make_stub("AnonXMusic.utils", os.path.join(REPO_ROOT, "AnonXMusic", "utils"))
sys.modules["AnonXMusic.utils"] = _utils_mod

_decs_mod = _make_stub("AnonXMusic.utils.decorators")
sys.modules["AnonXMusic.utils.decorators"] = _decs_mod

_inline_mod = _make_stub("AnonXMusic.utils.inline")
sys.modules["AnonXMusic.utils.inline"] = _inline_mod

_cp_mod = _make_stub("AnonXMusic.utils.channelplay")
sys.modules["AnonXMusic.utils.channelplay"] = _cp_mod

_ext_mod = _make_stub("AnonXMusic.utils.extraction")
sys.modules["AnonXMusic.utils.extraction"] = _ext_mod

_sys_mod = _make_stub("AnonXMusic.utils.sys")
sys.modules["AnonXMusic.utils.sys"] = _sys_mod

_stream_mod = _make_stub("AnonXMusic.utils.stream", os.path.join(REPO_ROOT, "AnonXMusic", "utils", "stream"))
sys.modules["AnonXMusic.utils.stream"] = _stream_mod

_mongo_pkg = _make_stub("AnonXMusic.mongo", os.path.join(REPO_ROOT, "AnonXMusic", "mongo"))
sys.modules["AnonXMusic.mongo"] = _mongo_pkg

_utils_mongo_mod = _make_stub("AnonXMusic.utils.mongo")
_utils_mongo_mod.db = MagicMock()
sys.modules["AnonXMusic.utils.mongo"] = _utils_mongo_mod

_src_mod = _make_stub("src")
sys.modules["src"] = _src_mod
_src_db_mod = _make_stub("src.database")
_src_db_mod.db = MagicMock()
sys.modules["src.database"] = _src_db_mod
