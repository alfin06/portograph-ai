import os
import sys
from typing import TypedDict, List, Dict, Any
from langgraph.graph import StateGraph, END

# Ensure imports work regardless of where the script is run
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data_pipeline.idx_scraper import IDXScraper
from data_pipeline.indicators import TechnicalIndicators
from agents.screener_agent import ScreenerAgent
from database.db_manager import DBManager

# 1. Define the State
class TradingState(TypedDict):
    candidates: List[Dict[str, Any]]
    decision: Dict[str, Any]
    trade_id: int
    status: str

# 2. Define the Nodes
def fetch_data_node(state: TradingState):
    """Node 1: Pulls market data and calculates indicators."""
    print("-> Fetching Market Data & Calculating Indicators...")
    scraper = IDXScraper()
    market_data = scraper.fetch_market_data(period="3mo")
    
    # We apply the technical indicators to the raw data here
    enhanced_candidates = []
    base_candidates = scraper.get_screener_candidates()
    
    # Merge the RSI and MACD into the candidate payload
    for candidate in base_candidates:
        ticker = candidate["ticker"]
        if ticker in market_data:
            df = market_data[ticker]
            df_with_ti = TechnicalIndicators.add_all_indicators(df)
            latest = df_with_ti.iloc[-1]
            
            # Add technicals to the dictionary
            candidate["RSI"] = latest.get("RSI", 0)
            candidate["MACD_Hist"] = latest.get("MACD_Hist", 0)
            enhanced_candidates.append(candidate)
            
    # Keep only the top 5 to save LLM tokens and focus on the best setups
    return {"candidates": enhanced_candidates[:5]}

def analyze_data_node(state: TradingState):
    """Node 2: Feeds the data to the LLM for a trading decision."""
    print("-> Agent Analyzing Top Candidates...")
    agent = ScreenerAgent()
    decision = agent.analyze_candidates(state["candidates"])
    return {"decision": decision}

def log_trade_node(state: TradingState):
    """Node 3: Logs the decision to the database if it's a BUY."""
    print("-> Processing Agent Decision...")
    decision = state["decision"]
    db = DBManager()
    
    if decision.get("action") == "BUY":
        trade_id = db.log_suggested_trade(
            ticker=decision["ticker"],
            action=decision["action"],
            suggested_price=decision["entry_price"],
            target_price=decision["target_price"],
            stop_loss=decision["stop_loss"],
            reasoning=decision["reasoning"]
        )
        print(f"✅ Trade Logged! ID: {trade_id} for {decision['ticker']}")
        return {"trade_id": trade_id, "status": "LOGGED"}
    
    print(f"ℹ️ No trade executed. Reason: {decision.get('reasoning')}")
    return {"trade_id": 0, "status": "SKIPPED"}

# 3. Build and Compile the Graph
def build_trading_graph():
    """Constructs the LangGraph state machine."""
    workflow = StateGraph(TradingState)
    
    # Add nodes
    workflow.add_node("fetch_data", fetch_data_node)
    workflow.add_node("analyze_data", analyze_data_node)
    workflow.add_node("log_trade", log_trade_node)
    
    # Define the flow
    workflow.set_entry_point("fetch_data")
    workflow.add_edge("fetch_data", "analyze_data")
    workflow.add_edge("analyze_data", "log_trade")
    workflow.add_edge("log_trade", END)
    
    return workflow.compile()

# Quick test execution block
if __name__ == "__main__":
    print("Initializing PortoGraph AI Pipeline...")
    app = build_trading_graph()
    
    # Invoke the graph with an empty initial state
    final_state = app.invoke({
        "candidates": [],
        "decision": {},
        "trade_id": 0,
        "status": "INIT"
    })
    
    print("\n--- Final Pipeline State ---")
    print(f"Status: {final_state['status']}")
    if final_state['decision'].get('action') == "BUY":
        print(f"Target: {final_state['decision']['ticker']} @ {final_state['decision']['entry_price']}")