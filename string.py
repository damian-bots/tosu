
from pyrogram import Client
import sys

# --- YOUR CREDENTIALS (inserted as requested) ---
API_ID = 24620300
API_HASH = "9a098f01aa56c836f2e34aee4b7ef963"
# -----------------------------------------------

# TARGET: use "me" for Saved Messages (recommended)
TARGET = "@txsar"

def normalise_target(t):
    if not t:
        return "me"
    t = t.strip()
    if t == "me":
        return "me"
    if t.startswith("@"):
        return t
    return "@" + t

def main():
    if not API_ID or not API_HASH:
        print("ERROR: API_ID/API_HASH missing. Set them in the script and try again.")
        sys.exit(1)

    target = normalise_target(TARGET)
    print("This script will open a temporary in-memory Pyrogram session,")
    print("export a session string, and send it to:", target)
    print("Make sure you run this locally and do NOT share the printed string with others.")
    input("Press Enter to continue (or Ctrl+C to abort)...")

    with Client(":memory:", api_id=API_ID, api_hash=API_HASH, in_memory=True) as app:
        try:
            sess_str = app.export_session_string()
        except AttributeError:
            raise RuntimeError(
                "Your pyrogram version may not support export_session_string(). "
                "Please upgrade: pip install -U pyrogram"
            )

        # Print locally so you can copy if needed (but be careful)
        print("\n=== SESSION STRING (copy if you want, KEEP PRIVATE) ===\n")
        print(sess_str)
        print("\n=== END ===\n")

        msg_text = (
            "⚠️ This message contains your Pyrogram session string. Keep it private.\n\n"
            f"{sess_str}\n\n"
            "If you did not request this, delete it immediately."
        )

        try:
            app.send_message(target, msg_text)
            print(f"Session string sent to {target}. Check that chat in your Telegram.")
        except Exception as e:
            print("Failed to send message to target:", e)
            print("You can copy the printed session string above manually instead.")

        input("Press Enter to exit and clear in-memory session...")

if __name__ == "__main__":
    main()
