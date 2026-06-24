import logging
from pymongo import MongoClient
from pymongo.errors import BulkWriteError
import config

# Configure basic logging to match the Go script's style
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# --- EXACT CONFIGURATION FROM database.go ---
MONGO_URI = config.MONGO_DB_URI # Replace with your actual MongoDB URI if different
NEW_DB_NAME = "ArcMusic"                 # Updated from database.go
OLD_DB_NAME = "Yukki"                    # From migrate_data.go

def reverse_migrate():
    client = MongoClient(MONGO_URI)
    
    new_db = client[NEW_DB_NAME]
    old_db = client[OLD_DB_NAME]
    
    logger.info("Starting reverse migration to restore old Yukki database structures...")
    
    reverse_cplay(new_db, old_db)
    reverse_served_users_and_chats(new_db, old_db)
    reverse_sudoers(new_db, old_db)
    
    # Clear the migration flag so the Go script can run again in the future if needed
    old_db.migration_status.delete_one({"migrated": True})
    
    logger.info("Reverse data migration complete.")

def reverse_cplay(new_db, old_db):
    """Restores the old cplaymode collection."""
    # Updated to use the exact collection name from database.go
    chat_settings_coll = new_db["chat_settings"] 
    old_cplay_coll = old_db["cplaymode"]
    
    # Find all documents that have the new cplay_id field
    cursor = chat_settings_coll.find({"cplay_id": {"$exists": True}})
    
    old_docs = []
    for doc in cursor:
        old_docs.append({
            "chat_id": doc["_id"],
            "mode": doc["cplay_id"]
        })
        
    if old_docs:
        try:
            old_cplay_coll.insert_many(old_docs)
            logger.info(f"Restored {len(old_docs)} records to 'cplaymode'.")
        except BulkWriteError as bwe:
            logger.error(f"Failed to bulk insert into cplaymode: {bwe.details}")
    else:
        logger.info("No cplay records found to restore.")

def reverse_served_users_and_chats(new_db, old_db):
    """Restores the old tgusersdb and chats collections from the global settings."""
    # Updated to use the exact collection name from database.go
    settings_coll = new_db["bot_settings"]
    old_users_coll = old_db["tgusersdb"]
    old_chats_coll = old_db["chats"]
    
    global_doc = settings_coll.find_one({"_id": "global"})
    if not global_doc:
        logger.info("No global document found. Skipping users and chats restoration.")
        return

    served = global_doc.get("served", {})
    
    # Restore Users
    users = served.get("users", [])
    if users:
        user_docs = [{"user_id": uid} for uid in users]
        try:
            old_users_coll.insert_many(user_docs)
            logger.info(f"Restored {len(users)} records to 'tgusersdb'.")
        except Exception as e:
            logger.error(f"Failed to restore served users: {e}")
            
    # Restore Chats
    chats = served.get("chats", [])
    if chats:
        chat_docs = [{"chat_id": cid} for cid in chats]
        try:
            old_chats_coll.insert_many(chat_docs)
            logger.info(f"Restored {len(chats)} records to 'chats'.")
        except Exception as e:
            logger.error(f"Failed to restore served chats: {e}")

def reverse_sudoers(new_db, old_db):
    """Restores the old sudoers collection."""
    # Updated to use the exact collection name from database.go
    settings_coll = new_db["bot_settings"]
    old_sudoers_coll = old_db["sudoers"]
    
    global_doc = settings_coll.find_one({"_id": "global"})
    if not global_doc:
        return

    sudoers = global_doc.get("sudoers", [])
    if sudoers:
        try:
            old_sudoers_coll.insert_one({
                "sudo": "sudo",
                "sudoers": sudoers
            })
            logger.info(f"Restored sudoers list with {len(sudoers)} users.")
        except Exception as e:
            logger.error(f"Failed to restore sudoers: {e}")

if __name__ == "__main__":
    reverse_migrate()
