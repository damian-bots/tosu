import os

from ..logging import LOGGER

BACKUP_DIR = "backup"
CACHE_DIR = "cache"

def dirr():
    if "downloads" not in os.listdir():
        os.mkdir("downloads")
    
    if CACHE_DIR not in os.listdir():
        os.mkdir(CACHE_DIR)
        
    if BACKUP_DIR not in os.listdir():
        os.mkdir(BACKUP_DIR)

    for file in os.listdir():
        if os.path.isfile(file) and file.endswith((".jpg", ".jpeg", ".png")):
            destination = os.path.join(CACHE_DIR, file)
            os.replace(file, destination) 

    LOGGER(__name__).info("Directories Updated.")
