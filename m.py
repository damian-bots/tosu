# ╔══════════════════════════════════════════════════════════════════╗
# ║        Copyright © tusar404 — All Rights Reserved               ║
# ║     AnonXMusic · Telegram Music Bot · Powered by PyTgCalls      ║
# ║        Unauthorized copying or distribution is prohibited        ║
# ╚══════════════════════════════════════════════════════════════════╝

import asyncio
import os
from pymongo import AsyncMongoClient
import config

MONGO_URL = config.MONGO_DB_URI
DB_NAME = config.DB_NAME

async def revert_migration():
    if not MONGO_URL or not DB_NAME:
        raise ValueError("Error: MONGO_URL and DB_NAME environment variables must be set before running this script.")

    print("Connecting to MongoDB...")
    client = AsyncMongoClient(MONGO_URL)
    db = client[DB_NAME]
    
    usersdb = db.users
    tgusersdb = db.tgusersdb
    chatsdb = db.chats
    cache = db.cache
    
    print("Fetching currently migrated users...")
    current_users = [user["_id"] async for user in usersdb.find() if isinstance(user.get("_id"), int)]
        
    print("Fetching currently migrated chats...")
    current_chats = [chat["_id"] async for chat in chatsdb.find() if isinstance(chat.get("_id"), int)]
        
    print("Dropping migrated collections...")
    await usersdb.drop()
    await chatsdb.drop()
    
    if current_users:
        print(f"Restoring {len(current_users)} users to old format...")
        old_users_format = [{"user_id": uid} for uid in current_users]
        await tgusersdb.insert_many(old_users_format) 

    if current_chats:
        print(f"Restoring {len(current_chats)} chats to old format...")
        old_chats_format = [{"chat_id": cid} for cid in current_chats]
        await chatsdb.insert_many(old_chats_format)
        
    print("Removing 'migrated' flag from cache...")
    await cache.delete_one({"_id": "migrated"})
    
    print("\nReversion complete! Your chats and users are back in their original format.")
    await client.close()

if __name__ == "__main__":
    asyncio.run(revert_migration())
