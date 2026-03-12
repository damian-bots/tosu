import os

from ..logging import LOGGER

# Define your backup directory here
BACKUP_DIR = "backup"

def dirr():
    # Clean up leftover image files
    for file in os.listdir():
        if file.endswith((".jpg", ".jpeg", ".png")):
            os.remove(file)

    # Ensure required directories exist
    if "downloads" not in os.listdir():
        os.mkdir("downloads")
    
    if "cache" not in os.listdir():
        os.mkdir("cache")
        
    # Ensure the backup directory exists
    if BACKUP_DIR not in os.listdir():
        os.mkdir(BACKUP_DIR)

    LOGGER(__name__).info("Directories Updated.")
