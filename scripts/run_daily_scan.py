import os
import sys
import asyncio
from dotenv import load_dotenv
from telegram import Bot

# Ensure imports work from the root directory
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data_pipeline.crypto_scraper import CryptoScraper
from agents.screener_agent import ScreenerAgent
from database.db_manager import DBManager

# Load environment variables
load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

async def send_telegram_alert(decision: dict, trade_id: int):
    """Pushes a notification to your Telegram Chat ID based on the AI's decision."""
    if not TOKEN or not CHAT_ID:
        print("⚠️ Warning: Telegram credentials missing in .env.")
        return

    bot = Bot(token=TOKEN)
    
    if decision.get("action") == "BUY":
        # Keys updated to match the Crypto Prompt: target_buy_price, target_sell_price
        text = (
            f"🚨 <b>PortoGraph AI: BUY SIGNAL</b> 🚨\n\n"
            f"📊 <b>{decision['ticker']}</b>\n"
            f"<b>Entry Target:</b> {decision['target_buy_price']:,.0f} IDR\n"
            f"<b>Take Profit:</b> {decision['target_sell_price']:,.0f} IDR\n\n"
            f"🧠 <b>Reasoning:</b> {decision['reasoning']}\n\n"
            f"👉 <b>Trade ID: #{trade_id}</b>\n"
            f"Action required: Go to your bot chat and type /pending to log your execution."
        )
    else:
        text = (
            f"ℹ️ <b>PortoGraph AI: Market Update</b>\n\n"
            f"No trades triggered today.\n"
            f"<b>Reasoning:</b> {decision.get('reasoning', 'No viable candidates.')}"
        )
        
    try:
        await bot.send_message(chat_id=CHAT_ID, text=text, parse_mode='HTML')
        print("📲 Telegram alert sent successfully!")
    except Exception as e:
        print(f"❌ Failed to send Telegram alert: {e}")

def main():
    print("🚀 Starting PortoGraph AI Crypto Midnight Scan...")
    
    # 0. INITIALIZE DB & CHECK FOR ACTIVE TRADES (Token Saver Logic)
    db = DBManager()
    # Check if we already have an open position
    active_trades = [t for t in db.get_pending_trades() if t['status'] == 'ACTIVE']
    
    if active_trades:
        # Logic: Only one trade at a time to focus capital (5M limit)
        print(f"⏩ Skipping Scan: Active trade in {active_trades[0]['ticker']} is still open.")
        return

    # 1. Fetch Crypto Data from Indodax (Only happens if no active trades)
    scraper = CryptoScraper()
    snapshot = scraper.get_market_snapshot()
    
    if not snapshot:
        print("❌ Scan aborted: No market data.")
        return

    # 2. Agent Analysis via Forge (Only happens if no active trades)
    agent = ScreenerAgent()
    decision = agent.analyze_candidates(snapshot)
    
    # 3. Log the Trade in the Database
    trade_id = 0
    if decision.get("action") == "BUY":
        trade_id = db.log_suggested_trade(
            ticker=decision['ticker'],
            action="BUY",
            suggested_price=decision['target_buy_price'],
            target_price=decision['target_sell_price'],
            stop_loss=decision['target_buy_price'] * 0.95, 
            reasoning=decision['reasoning']
        )
        print(f"📝 Trade logged in DB with ID: {trade_id}")
    
    # 4. Push the Telegram notification
    asyncio.run(send_telegram_alert(decision, trade_id))
    print("✅ Daily scan complete.")

if __name__ == "__main__":
    main()