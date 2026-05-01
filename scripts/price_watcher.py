import os
import sys
import time
import asyncio
from telegram import Bot
from dotenv import load_dotenv

# Path setup
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database.db_manager import DBManager
from data_pipeline.crypto_scraper import CryptoScraper

load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

async def run_watcher():
    db = DBManager()
    scraper = CryptoScraper()
    bot = Bot(token=TOKEN)
    
    print("👀 PortoGraph Watcher: Active monitoring started...")
    
    while True:
        active_trades = [t for t in db.get_pending_trades() if t['status'] == 'ACTIVE']
        
        if not active_trades:
            time.sleep(300) # Wait 5 mins if no active trades
            continue
            
        for trade in active_trades:
            # 1. Fetch live price from Indodax
            snapshot = scraper.get_market_snapshot()
            current_data = next(
                (item for item in snapshot if item["ticker"].replace("/", "").replace("_", "").upper() == 
                trade['ticker'].replace("/", "").replace("_", "").upper()), 
                None
            )
            
            if not current_data:
                print(f"⚠️ Could not find data for {trade['ticker']}")
                continue
                
            current_price = current_data["current_price"]
            
            # --- ADD THIS LINE BELOW ---
            print(f"📊 [LIVE] {trade['ticker']}: {current_price:,.0f} IDR | SL: {trade['stop_loss']:,.0f} | TP: {trade['target_price']:,.0f}")

            # Check Stop Loss
            if current_price <= trade['stop_loss']:
                msg = f"🛑 <b>STOP LOSS ALERT: {trade['ticker']}</b>\nPrice dropped to {current_price:,.0f} IDR. Target was {trade['stop_loss']:,.0f}."
                await bot.send_message(CHAT_ID, msg, parse_mode='HTML')
                print(f"🛑 STOP LOSS ALERT: {trade['ticker']} - Price dropped to {current_price:,.0f} IDR.")
            # Check Take Profit
            elif current_price >= trade['target_price']:
                msg = f"🎯 <b>TARGET REACHED: {trade['ticker']}</b>\nPrice hit {current_price:,.0f} IDR! Sell now."
                await bot.send_message(CHAT_ID, msg, parse_mode='HTML')
                print(f"🎯 TARGET REACHED: {trade['ticker']} - Price hit {current_price:,.0f} IDR!")

        time.sleep(120) # Check every 2 minutes

if __name__ == "__main__":
    asyncio.run(run_watcher())