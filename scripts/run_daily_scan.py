import os
import sys
import asyncio
from dotenv import load_dotenv
from telegram import Bot

# Ensure imports work from the root directory
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.graph import build_trading_graph

# Load environment variables
load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

async def send_telegram_alert(decision: dict, trade_id: int):
    """Pushes a notification to your Telegram Chat ID based on the AI's decision."""
    if not TOKEN or not CHAT_ID:
        print("⚠️ Warning: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing in .env.")
        print("Cannot send Telegram alert, but the trade is logged in the database.")
        return

    bot = Bot(token=TOKEN)
    
    if decision.get("action") == "BUY":
        text = (
            f"🚨 *PortoGraph AI: BUY SIGNAL* 🚨\n\n"
            f"📊 *{decision['ticker']}*\n"
            f"Entry Price: {decision['entry_price']}\n"
            f"Target: {decision['target_price']}\n"
            f"Stop Loss: {decision['stop_loss']}\n\n"
            f"🧠 *Reasoning:* {decision['reasoning']}\n\n"
            f"👉 *Action required:* Go to your bot chat and type `/pending` to log your execution."
        )
    else:
        text = (
            f"ℹ️ *PortoGraph AI: Market Update*\n\n"
            f"No trades triggered today.\n"
            f"Reasoning: {decision.get('reasoning', 'No viable candidates.')}"
        )
        
    try:
        await bot.send_message(chat_id=CHAT_ID, text=text, parse_mode='Markdown')
        print("📲 Telegram alert sent successfully!")
    except Exception as e:
        print(f"❌ Failed to send Telegram alert: {e}")

def main():
    print("🚀 Starting PortoGraph AI Daily Scan...")
    
    # 1. Initialize the LangGraph Pipeline
    app = build_trading_graph()
    
    # 2. Run the pipeline
    final_state = app.invoke({
        "candidates": [],
        "decision": {},
        "trade_id": 0,
        "status": "INIT"
    })
    
    # 3. Extract the results
    decision = final_state.get('decision', {})
    trade_id = final_state.get('trade_id', 0)
    
    # 4. Push the Telegram notification
    asyncio.run(send_telegram_alert(decision, trade_id))
    print("✅ Daily scan complete.")

if __name__ == "__main__":
    main()