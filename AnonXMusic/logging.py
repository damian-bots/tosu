import logging
from logging.handlers import RotatingFileHandler

# Suppress noisy third-party loggers first
for noisy in ("httpx", "httpcore", "pyrogram", "pytgcalls", "ntgcalls", "pymongo", "motor"):
    logging.getLogger(noisy).setLevel(logging.ERROR)

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s - %(levelname)s] - %(name)s - %(message)s",
    datefmt="%d-%b-%y %H:%M:%S",
    handlers=[
        RotatingFileHandler("log.txt", maxBytes=10 * 1024 * 1024, backupCount=5),
        logging.StreamHandler(),
    ],
)


def LOGGER(name: str) -> logging.Logger:
    return logging.getLogger(name)
