import os
import sys
import asyncio
from dotenv import load_dotenv
from telegram import Bot

# Ensure imports work from the root directory
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

async def send_status_check():
    """Sends a simple heartbeat message to ensure the system is online."""
    if not TOKEN or not CHAT_ID:
        print("❌ Error: Missing credentials in .env")
        return

    bot = Bot(token=TOKEN)
    message = (
        "🟢 <b>PortoGraph AI: System Online</b>\n\n"
        "All systems are operational. The market scan is scheduled for 15:00 WIB today."
    )
    
    try:
        await bot.send_message(chat_id=CHAT_ID, text=message, parse_mode='HTML')
        print("✅ Status check sent to Telegram.")
    except Exception as e:
        print(f"❌ Failed to send status check: {e}")

if __name__ == "__main__":
    asyncio.run(send_status_check())