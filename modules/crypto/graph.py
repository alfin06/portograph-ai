import os
import sys
from typing import TypedDict, List, Dict, Any
from langgraph.graph import StateGraph, END


from modules.crypto.scraper import CryptoScraper
from modules.crypto.screener_agent import ScreenerAgent
from database.db_manager import DBManager
from core.config import STOP_LOSS_MULTIPLIER

# 1. Define the State
class TradingState(TypedDict):
    candidates: List[Dict[str, Any]]
    decision: Dict[str, Any]
    trade_id: int
    status: str

# 2. Define the Nodes
def fetch_data_node(state: TradingState):
    """Node 1: Pulls Indodax market data (BTC, ETH, HYPE)."""
    print("-> Fetching Crypto Market Snapshot...")
    scraper = CryptoScraper()
    
    # Use the new crypto-specific snapshot method
    candidates = scraper.get_market_snapshot()
    
    return {"candidates": candidates}

def analyze_data_node(state: TradingState):
    """Node 2: Agent determines Entry and Exit targets at Midnight."""
    print("-> Agent Analyzing Crypto Trends via Forge...")
    agent = ScreenerAgent()
    decision = agent.analyze_candidates(state["candidates"])
    return {"decision": decision}

def log_trade_node(state: TradingState):
    """Node 3: Records the specific price targets in the database."""
    print("-> Processing Agent Decision...")
    decision = dict(state["decision"]) 
    db = DBManager()
    
    if decision.get("action") == "BUY":
        # Use the specific keys from your Crypto Prompt
        buy_target = decision["target_buy_price"]
        sell_target = decision["target_sell_price"]
        
        # We still calculate a safety Stop Loss locally
        stop_loss = buy_target * STOP_LOSS_MULTIPLIER
        
        # Attach the stop loss back to the decision for Telegram
        decision["stop_loss"] = stop_loss
        
        trade_id = db.log_suggested_trade(
            ticker=decision["ticker"],
            action=decision["action"],
            suggested_price=buy_target,
            target_price=sell_target,
            stop_loss=stop_loss,
            reasoning=decision["reasoning"]
        )
        print(f"✅ Crypto Trade Logged! ID: {trade_id} for {decision['ticker']}")
        
        return {"trade_id": trade_id, "status": "LOGGED", "decision": decision}
    
    print(f"ℹ️ No trade executed. Reason: {decision.get('reasoning')}")
    return {"trade_id": 0, "status": "SKIPPED", "decision": decision}

# 3. Build and Compile the Graph
def build_trading_graph():
    """Constructs the PortoGraph AI state machine."""
    workflow = StateGraph(TradingState)
    
    workflow.add_node("fetch_data", fetch_data_node)
    workflow.add_node("analyze_data", analyze_data_node)
    workflow.add_node("log_trade", log_trade_node)
    
    workflow.set_entry_point("fetch_data")
    workflow.add_edge("fetch_data", "analyze_data")
    workflow.add_edge("analyze_data", "log_trade")
    workflow.add_edge("log_trade", END)
    
    return workflow.compile()

if __name__ == "__main__":
    app = build_trading_graph()
    final_state = app.invoke({
        "candidates": [], "decision": {}, "trade_id": 0, "status": "INIT"
    })
    print(f"\nFinal Pipeline Status: {final_state['status']}")