import os
import sys
import asyncio
import json
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# Add the parent directory to the path so we can import the database manager
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database.db_manager import DBManager
from agents.critic_agent import CriticAgent

# Load environment variables
load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Initialize database
db = DBManager()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the /start command and gives you your Chat ID."""
    chat_id = update.message.chat_id
    await update.message.reply_text(
        f"🤖 Welcome to PortoGraph AI!\n\n"
        f"Your Chat ID is: `{chat_id}`\n"
        f"Please add this to your .env file as TELEGRAM_CHAT_ID so I only talk to you.\n\n"
        f"Commands:\n"
        f"/pending - Trades waiting for execution\n"
        f"/portofolio - View and manage your ACTIVE trades\n"
        f"/reflect - Run Critic Agent analysis"
    )

async def pending_trades(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fetches pending trades from the database and displays them with action buttons."""
    trades = db.get_pending_trades()
    
    if not trades:
        await update.message.reply_text("No pending trades right now.")
        return

    for trade in trades:
        text = (
            f"📊 *{trade['ticker']}* ({trade['action']})\n"
            f"Status: {trade['status']}\n"
            f"Suggested Entry: {trade['suggested_price']}\n"
            f"Target (1% net): {trade['target_price']}\n"
            f"Stop Loss: {trade['stop_loss']}\n"
            f"Reasoning: {trade['agent_reasoning']}"
        )
        
        keyboard = []
        # If the agent just suggested it, show the "Buy" button
        if trade['status'] == 'PENDING':
            keyboard.append([InlineKeyboardButton("✅ I Bought This", callback_data=f"bought_{trade['id']}")])
        
        # If you bought it yesterday, show the "Result" buttons today
        elif trade['status'] == 'ACTIVE':
            keyboard.append([
                InlineKeyboardButton("🎯 Hit Target", callback_data=f"win_{trade['id']}"),
                InlineKeyboardButton("🛑 Hit Stop Loss", callback_data=f"loss_{trade['id']}")
            ])
            
        reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer() 
    
    data = query.data.split('_')
    action = data[0]
    
    if action in ["bought", "win", "loss"]:
        trade_id = int(data[1])
        if action == "bought":
            # For now, we use a default of 5,000,000 IDR. 
            # In the future, you could make this a dynamic input!
            db.update_execution(trade_id, 0.0, 5000000.0) 
            await query.edit_message_text(text=f"✅ Trade {trade_id} marked as ACTIVE with 5M IDR.")
        elif action == "win":
            # 1. Fetch the trade from DB to get the entry price
            trade = db.get_trade_by_id(trade_id)
            
            # 2. Use your actual entry price (or the suggested one if you didn't log entry)
            entry = trade['suggested_price'] 
            exit_price = trade['target_price']
            
            # 3. Calculate actual PnL: ((Exit - Entry) / Entry) * 100
            actual_pnl = ((exit_price - entry) / entry) * 100
            
            # 4. Save real data to the DB
            db.update_feedback(trade_id, "CLOSED_WIN", round(actual_pnl, 2), "Target hit via Indodax limit order.")
            await query.edit_message_text(text=f"🎯 Trade {trade_id} closed! Actual Profit: {actual_pnl:.2f}%")
        elif action == "loss":
            # Fetch trade to get entry and stop loss
            trade = db.get_trade_by_id(trade_id)
            entry = trade['suggested_price']
            exit_price = trade['stop_loss']
            
            # Calculate actual PnL (will be negative)
            actual_pnl = ((exit_price - entry) / entry) * 100
            
            db.update_feedback(trade_id, "CLOSED_LOSS", round(actual_pnl, 2), "Stopped out.")
            await query.edit_message_text(text=f"🛑 Trade {trade_id} closed as LOSS ({actual_pnl:.2f}%).")
            
    elif action == "approve":
        rule_idx = data[1]
        rule_text = context.user_data.get(f'proposed_rule_{rule_idx}')
        
        if rule_text:
            rules_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'dynamic_rules.json')
            try:
                # Append the new rule to the JSON file
                with open(rules_path, 'r') as f:
                    rules = json.load(f)
                rules.append(rule_text)
                with open(rules_path, 'w') as f:
                    json.dump(rules, f, indent=2)
                    
                await query.edit_message_text(text=f"✅ Rule Approved & Injected:\n{rule_text}")
            except Exception as e:
                await query.edit_message_text(text=f"❌ Failed to save rule: {e}")
        else:
            await query.edit_message_text(text="⚠️ Rule data expired. Please run /reflect again.")
            
    elif action == "reject":
        await query.edit_message_text(text="❌ Rule Rejected. It will not be added to the agent.")

async def reflect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Triggers the Critic Agent to review past trades and propose new rules."""
    await update.message.reply_text("🔍 Critic Agent is analyzing your trade history. Please wait...")
    
    critic = CriticAgent()
    reflection = critic.generate_reflection()

    if "error" in reflection or "message" in reflection:
        await update.message.reply_text(reflection.get("error", reflection.get("message")))
        return

    # Send the main analysis
    report = (
        f"🧠 *Critic Agent Weekly Report*\n\n"
        f"📈 *Win Rate Analysis:* {reflection.get('win_rate_analysis', 'N/A')}\n\n"
        f"🌍 *Market Regime:* {reflection.get('market_regime', 'N/A')}\n\n"
        f"💡 *Proposed Rules for Screener Agent:*"
    )
    await update.message.reply_text(report, parse_mode='Markdown')

    # Send each proposed rule as a separate message with Approve/Reject buttons
    rules = reflection.get("adjusted_rules", [])
    for idx, rule in enumerate(rules):
        # Temporarily store the rule text in memory using context.user_data
        context.user_data[f'proposed_rule_{idx}'] = rule
        
        keyboard = [
            [
                InlineKeyboardButton("✅ Approve", callback_data=f"approve_{idx}"),
                InlineKeyboardButton("❌ Reject", callback_data=f"reject_{idx}")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(f"Rule: {rule}", reply_markup=reply_markup)

async def portofolio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Fetches only ACTIVE trades to show your current market exposure."""
    # Note: You'll need to add get_active_trades() to your DBManager 
    # or filter get_pending_trades() here.
    all_trades = db.get_pending_trades() 
    active_trades = [t for t in all_trades if t['status'] == 'ACTIVE']
    
    if not active_trades:
        await update.message.reply_text("💼 Your portofolio is currently empty (no active trades).")
        return

    total_capital = sum(t['capital_allocated'] for t in active_trades)
    
    header = f"💼 *Current portofolio*\nTotal Capital: {total_capital:,.0f} IDR\n"
    header += "----------------------------\n"
    await update.message.reply_text(header, parse_mode='Markdown')

    for trade in active_trades:
        text = (
            f"🚀 *{trade['ticker']}*\n"
            f"Entry: {trade['suggested_price']:,.0f} IDR\n"
            f"Capital: {trade['capital_allocated']:,.0f} IDR\n"
            f"Target: {trade['target_price']:,.0f} IDR\n"
            f"Status: {trade['status']}"
        )
        
        # Action buttons to close the trade
        keyboard = [[
            InlineKeyboardButton("🎯 Hit Target", callback_data=f"win_{trade['id']}"),
            InlineKeyboardButton("🛑 Hit Stop Loss", callback_data=f"loss_{trade['id']}")
        ]]
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')

def main():
    if not TOKEN:
        print("Error: TELEGRAM_BOT_TOKEN not found in .env file.")
        return
        
    app = Application.builder().token(TOKEN).build()
    
    # Register handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("pending", pending_trades))
    app.add_handler(CommandHandler("portofolio", portofolio))
    app.add_handler(CommandHandler("reflect", reflect))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    print("🤖 PortoGraph AI Telegram Bot is running...")
    print("Send /start to your bot in Telegram.")
    app.run_polling()

if __name__ == "__main__":
    main()