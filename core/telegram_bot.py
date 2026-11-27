import os
import sys
import asyncio
import json
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest, NetworkError, TimedOut
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
import logging

log = logging.getLogger("bot")

from database.db_manager import DBManager
from agents.critic_agent import CriticAgent
from core.config import TELEGRAM_CHAT_ID
import functools


def is_owner(update, owner_chat_id: str = None) -> bool:
    """True only for the owner's private chat. Fails closed: with no TELEGRAM_CHAT_ID
    set, nobody is the owner (anyone can find a bot by its username)."""
    owner = str(owner_chat_id if owner_chat_id is not None else TELEGRAM_CHAT_ID).strip()
    chat, user = update.effective_chat, update.effective_user
    if not owner or chat is None or user is None:
        return False
    # Private chats only: in a group, every member would share the chat id
    return chat.type == "private" and str(chat.id) == owner and str(user.id) == owner


def owner_only(handler):
    """Ignore everyone except the owner. Unknown chats get no reply at all, so the bot
    doesn't reveal that it exists or what it does."""
    @functools.wraps(handler)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not is_owner(update):
            chat = update.effective_chat
            log.warning("Ignored update from unauthorized chat %s", getattr(chat, "id", None))
            if update.callback_query is not None:
                try:
                    await update.callback_query.answer()
                except Exception:
                    pass
            return
        return await handler(update, context)
    return wrapper

# Load environment variables
load_dotenv()
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

# Initialize database
db = DBManager()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles the /start command. Before TELEGRAM_CHAT_ID is set it only tells you your
    chat id (nothing else works yet); afterwards only the owner gets the command list."""
    chat_id = update.message.chat_id
    if not str(TELEGRAM_CHAT_ID).strip():
        if update.effective_chat.type == "private":
            await update.message.reply_text(
                f"Your chat ID is {chat_id}. Put it in .env as TELEGRAM_CHAT_ID and restart "
                f"PortoGraph; until then every command is switched off.")
        return
    if not is_owner(update):
        log.warning("Ignored /start from unauthorized chat %s", chat_id)
        return
    await update.message.reply_text(
        f"🤖 Welcome to PortoGraph AI!\n\n"
        f"Commands:\n"
        f"/pending - Trades waiting for execution\n"
        f"/portfolio - Your holdings, profit and allocation\n"
        f"/buy, /sell SYM QTY PRICE [DATE] - Record a trade (IDX in lots)\n"
        f"/div SYM AMOUNT [DATE] - Record a dividend\n"
        f"/txns [SYM], /deltxn ID - List or delete transactions\n"
        f"/price SYM - Latest close, zone and your position\n"
        f"/dividends - Upcoming ex-dates and income for your holdings\n"
        f"/calendar - Rate decisions, US data, market holidays\n"
        f"/portofolio - Old AI swing trades (ACTIVE)\n"
        f"/reflect - Run Critic Agent analysis\n"
        f"/digest idx|us - Latest market close digest\n"
        f"/fund SYM - Fundamentals for a watched symbol\n"
        f"/watch SYM idx|us [etf] [hold] - Add to watchlist\n"
        f"/unwatch SYM - Stop tracking\n"
        f"/screens - Saved screens and rule fields\n"
        f"/screen NAME|RULE - Run a screen now\n"
        f"/addscreen NAME [idx|us] RULE - Save a screen\n"
        f"/delscreen NAME - Delete a screen\n"
        f"/sentiment [idx|us] - Fear & greed score\n"
        f"/etf SYM - ETF fees, holdings, sectors\n"
        f"/overlap ETF1 ETF2 - How similar two ETFs are\n"
        f"/compare SYM SYM [1m|3m|6m|1y|2y|ytd] - Returns in IDR and USD\n"
        f"/value SYM - Cheap or expensive vs its own 5 years\n"
        f"/zone SYM LOW HIGH [note] - Buy-zone alert in the digest\n"
        f"/zones, /delzone ID - List or delete zones\n"
        f"/capital AMOUNT - Your capital for sizing (e.g. 150jt)\n"
        f"/size SYM ENTRY STOP [RISK%] [MAX%] - How many lots\n"
        f"/status - Last digests, latest prices, backups"
    )

@owner_only
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

@owner_only
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

@owner_only
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

class _Collector:
    """Notifier that keeps the text so the bot can reply with it."""
    def __init__(self):
        self.messages = []

    def send(self, text, parse_mode="HTML"):
        self.messages.append(text)


@owner_only
async def digest_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/digest idx | /digest us : refresh prices and show the latest close digest."""
    from app.jobs import digest
    from app.notify.telegram import split_message

    market = (context.args[0].upper() if context.args else "IDX")
    if market not in ("IDX", "US"):
        await _reply(update, "Usage: /digest idx  or  /digest us")
        return
    await _notice(update, f"⏳ Building {market} digest...")
    collector = _Collector()
    try:
        await asyncio.to_thread(digest.run, market, collector, db)
    except Exception as e:
        await _reply(update, f"⚠️ Digest failed: {type(e).__name__}: {e}")
        return
    for text in collector.messages:
        for chunk in split_message(text):
            await _reply(update, chunk, parse_mode="HTML")


REPLY_WAITS = (2, 5, 10)  # seconds between attempts when Telegram times out


async def _reply(update: Update, text: str, **kw):
    """reply_text that survives short network drops (VPN hiccups, Telegram timeouts).
    A rejected message (BadRequest, e.g. broken HTML) is raised at once."""
    for wait in (0, *REPLY_WAITS):
        if wait:
            await asyncio.sleep(wait)
        try:
            return await update.message.reply_text(text, **kw)
        except BadRequest:
            raise
        except (TimedOut, NetworkError) as e:
            last = e
            log.warning("Telegram reply failed (%s), retrying", type(e).__name__)
    raise last


async def _notice(update: Update, text: str):
    """A progress note like '⏳ Fetching...': nice to have, never worth failing over."""
    try:
        await update.message.reply_text(text)
    except (TimedOut, NetworkError):
        log.warning("Progress notice not delivered; continuing")


def _text_command(fn, needs_args=True, slow=False):
    """Wrap an app.commands function (db, args) -> HTML as an owner-only bot handler."""
    @owner_only
    async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
        from app.notify.telegram import split_message
        if slow:
            await _notice(update, "⏳ Fetching from Yahoo...")
        try:
            call = (lambda: fn(db, context.args or [])) if needs_args else (lambda: fn(db))
            text = await asyncio.to_thread(call)
        except Exception as e:
            text = f"⚠️ {type(e).__name__}: {e}"
        for chunk in split_message(text):
            await _reply(update, chunk, parse_mode="HTML")
    return handler


async def _on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    """One line per error instead of a full traceback for routine network drops."""
    err = context.error
    if isinstance(err, (TimedOut, NetworkError)) and not isinstance(err, BadRequest):
        log.warning("Telegram network error: %s", type(err).__name__)
    else:
        log.error("Unhandled error in bot handler", exc_info=err)


# Updated portfolio function
@owner_only
async def portofolio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View and manage active positions."""
    all_trades = db.get_pending_trades() 
    active_trades = [t for t in all_trades if t['status'] == 'ACTIVE']
    
    if not active_trades:
        await update.message.reply_text("💼 Your portfolio is empty.")
        return

    # Header with capital info
    total_at_risk = sum(t['capital_allocated'] for t in active_trades)
    header = f"💼 <b>Active Portfolio</b>\nCapital at risk: {total_at_risk:,.0f} IDR\n"
    header += "----------------------------\n"
    await update.message.reply_text(header, parse_mode='HTML')

    for trade in active_trades:
        text = (
            f"🚀 <b>{trade['ticker']}</b>\n"
            f"Entry: {trade['suggested_price']:,.0f} IDR\n"
            f"TP: {trade['target_price']:,.0f} | SL: {trade['stop_loss']:,.0f}"
        )
        keyboard = [[
            InlineKeyboardButton("🎯 Target Hit", callback_data=f"win_{trade['id']}"),
            InlineKeyboardButton("🛑 Stop Hit", callback_data=f"loss_{trade['id']}")
        ]]
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='HTML')

def main():
    if not TOKEN:
        print("Error: TELEGRAM_BOT_TOKEN not found in .env file.")
        return
        
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # httpx logs every request URL at INFO, and Telegram's URLs contain the bot token
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if not str(TELEGRAM_CHAT_ID).strip():
        log.warning("TELEGRAM_CHAT_ID is not set: every command is off. Send /start to the bot "
                    "to see your chat id, put it in .env and restart.")
    # Generous timeouts: the link to Telegram (through a VPN) can be slow for a few seconds
    app = (Application.builder().token(TOKEN)
           .connect_timeout(20).read_timeout(30).write_timeout(30).pool_timeout(30)
           .get_updates_connect_timeout(20).get_updates_read_timeout(40)
           .build())
    app.add_error_handler(_on_error)
    
    # Register handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("pending", pending_trades))
    app.add_handler(CommandHandler("portofolio", portofolio))
    app.add_handler(CommandHandler("reflect", reflect))
    app.add_handler(CommandHandler("digest", digest_cmd))

    from app import commands as cmd
    app.add_handler(CommandHandler("fund", _text_command(cmd.cmd_fund)))
    app.add_handler(CommandHandler("watch", _text_command(cmd.cmd_watch, slow=True)))
    app.add_handler(CommandHandler("unwatch", _text_command(cmd.cmd_unwatch)))
    app.add_handler(CommandHandler("screens", _text_command(cmd.cmd_screens, needs_args=False)))
    app.add_handler(CommandHandler("screen", _text_command(cmd.cmd_screen)))
    app.add_handler(CommandHandler("addscreen", _text_command(cmd.cmd_addscreen)))
    app.add_handler(CommandHandler("delscreen", _text_command(cmd.cmd_delscreen)))
    app.add_handler(CommandHandler("sentiment", _text_command(cmd.cmd_sentiment, slow=True)))
    app.add_handler(CommandHandler("etf", _text_command(cmd.cmd_etf, slow=True)))
    app.add_handler(CommandHandler("overlap", _text_command(cmd.cmd_overlap, slow=True)))
    app.add_handler(CommandHandler("compare", _text_command(cmd.cmd_compare, slow=True)))
    app.add_handler(CommandHandler("status", _text_command(cmd.cmd_status, needs_args=False)))
    app.add_handler(CommandHandler("value", _text_command(cmd.cmd_value, slow=True)))
    app.add_handler(CommandHandler("zone", _text_command(cmd.cmd_zone)))
    app.add_handler(CommandHandler("zones", _text_command(cmd.cmd_zones, needs_args=False)))
    app.add_handler(CommandHandler("delzone", _text_command(cmd.cmd_delzone)))
    app.add_handler(CommandHandler("capital", _text_command(cmd.cmd_capital)))
    app.add_handler(CommandHandler("size", _text_command(cmd.cmd_size)))
    app.add_handler(CommandHandler("portfolio", _text_command(cmd.cmd_portfolio, needs_args=False)))
    app.add_handler(CommandHandler("buy", _text_command(cmd.cmd_buy)))
    app.add_handler(CommandHandler("sell", _text_command(cmd.cmd_sell)))
    app.add_handler(CommandHandler("div", _text_command(cmd.cmd_div)))
    app.add_handler(CommandHandler("txns", _text_command(cmd.cmd_txns)))
    app.add_handler(CommandHandler("deltxn", _text_command(cmd.cmd_deltxn)))
    app.add_handler(CommandHandler("price", _text_command(cmd.cmd_price, slow=True)))
    app.add_handler(CommandHandler("dividends", _text_command(cmd.cmd_dividends, slow=True)))
    app.add_handler(CommandHandler("calendar", _text_command(cmd.cmd_calendar)))
    app.add_handler(CallbackQueryHandler(button_handler))
    
    print("🤖 PortoGraph AI Telegram Bot is running...")
    print("Send /start to your bot in Telegram.")
    app.run_polling()

if __name__ == "__main__":
    main()