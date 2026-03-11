import asyncio
import os
from pymongo import AsyncMongoClient
import config

# --- CONFIGURATION ---
# Fetching credentials securely from environment variables
MONGO_URL = config.MONGO_DB_URI
DB_NAME = config.DB_NAME
# ---------------------

async def revert_migration():
    # Failsafe to ensure environment variables are actually loaded
    if not MONGO_URL or not DB_NAME:
        raise ValueError("Error: MONGO_URL and DB_NAME environment variables must be set before running this script.")

    print("Connecting to MongoDB...")
    client = AsyncMongoClient(MONGO_URL)
    db = client[DB_NAME]
    
    # Define collections
    usersdb = db.users
    tgusersdb = db.tgusersdb
    chatsdb = db.chats
    cache = db.cache
    
    # 1. Fetch currently migrated data
    print("Fetching currently migrated users...")
    current_users = [user["_id"] async for user in usersdb.find() if isinstance(user.get("_id"), int)]
        
    print("Fetching currently migrated chats...")
    current_chats = [chat["_id"] async for chat in chatsdb.find() if isinstance(chat.get("_id"), int)]
        
    # 2. Drop the newly formatted collections
    print("Dropping migrated collections...")
    await usersdb.drop()
    await chatsdb.drop()
    
    # 3. Re-insert data in the old format
    if current_users:
        print(f"Restoring {len(current_users)} users to old format...")
        old_users_format = [{"user_id": uid} for uid in current_users]
        await tgusersdb.insert_many(old_users_format) 

    if current_chats:
        print(f"Restoring {len(current_chats)} chats to old format...")
        old_chats_format = [{"chat_id": cid} for cid in current_chats]
        await chatsdb.insert_many(old_chats_format)
        
    # 4. Remove the migration flag so the old bot works properly
    print("Removing 'migrated' flag from cache...")
    await cache.delete_one({"_id": "migrated"})
    
    print("\nReversion complete! Your chats and users are back in their original format.")
    await client.close()

if __name__ == "__main__":
    asyncio.run(revert_migration())
