# ╔══════════════════════════════════════════════════════════════════╗
# ║        Copyright © tusar404 — All Rights Reserved               ║
# ║     AnonXMusic · Telegram Music Bot · Powered by PyTgCalls      ║
# ║        Unauthorized copying or distribution is prohibited        ║
# ╚══════════════════════════════════════════════════════════════════╝

import AnonXMusic.core.compat  # noqa: F401

from AnonXMusic.core.bot import Anony
from AnonXMusic.core.dir import dirr
from AnonXMusic.core.git import git
from AnonXMusic.core.userbot import Userbot
from AnonXMusic.misc import dbb, heroku

from .logging import LOGGER

dirr()
git()
dbb()
heroku()

app = Anony()
userbot = Userbot()


from .platforms import *

Apple = AppleAPI()
Api = ApiPlatform()
Carbon = CarbonAPI()
Deezer = DeezerAPI()
DirectMedia = DirectMediaAPI()
Gaana = GaanaAPI()
JioSaavn = JioSaavnAPI()
Kick = KickAPI()
MXPlayer = MXPlayerAPI()
Resso = RessoAPI()
SoundCloud = SoundAPI()
Spotify = SpotifyAPI()
Telegram = TeleAPI()
Tidal = TidalAPI()
Twitch = TwitchAPI()
YouTube = YouTubeAPI()
