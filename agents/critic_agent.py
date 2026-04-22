import os
import sys
import json
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

# Ensure imports work regardless of execution context
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database.db_manager import DBManager

CRITIC_SYSTEM_PROMPT = """You are the Lead Quantitative Strategist and Reflection Agent for PortoGraph AI.
Your job is to review the trading history and user feedback for the Crypto Midnight Target strategy.
Analyze the patterns in the wins (CLOSED_WIN) and losses (CLOSED_LOSS). 

Look for:
1. Ticker correlations (e.g., "Are we losing on HYPE while winning on BTC?").
2. Target accuracy (e.g., "Was the Target Sell set too high for current volatility?").
3. RSI/MACD effectiveness (e.g., "Did the entries happen during RSI exhaustion?").

Return a structured JSON output:
{
    "win_rate_analysis": "Concise analysis of trade outcomes based on user feedback.",
    "market_regime": "Inference of crypto conditions (e.g., 'Altcoin season', 'Range-bound BTC').",
    "adjusted_rules": [
        "New rule for the Screener to avoid repeating past mistakes",
        "Refined volatility adjustment for target prices"
    ]
}
"""

class CriticAgent:
    def __init__(self):
        load_dotenv()
        
        # Consistent with your Forge API setup
        self.llm = ChatOpenAI(
            model=os.getenv("MODEL_NAME", "tensorblock/gpt-5-mini"),
            openai_api_key=os.getenv("FORGE_API_KEY"),
            openai_api_base=os.getenv("FORGE_BASE_URL"),
            temperature=1,
            model_kwargs={"seed": 42}
        )
        self.db = DBManager()

    def fetch_closed_trades(self) -> list:
        """Pulls completed crypto trades for analysis."""
        query = """
            SELECT ticker, date_suggested, action, suggested_price, target_price, stop_loss, status, 
                   pnl_percentage, agent_reasoning, user_feedback 
            FROM trade_history 
            WHERE status IN ('CLOSED_WIN', 'CLOSED_LOSS')
        """
        with self.db._get_connection() as conn:
            import sqlite3
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(query)
            return [dict(row) for row in cursor.fetchall()]

    def generate_reflection(self) -> dict:
        """Sends trade history to the Forge Engine to generate strategic adjustments."""
        trades = self.fetch_closed_trades()
        
        if not trades:
            return {"message": "No closed trades available for analysis yet."}
        
        prompt_data = f"Recent Crypto Trade History and Feedback:\n{json.dumps(trades, indent=2)}"
        
        print(f"🔍 Critic Agent is analyzing crypto performance via Forge...")
        try:
            response = self.llm.invoke([
                ("system", CRITIC_SYSTEM_PROMPT),
                ("human", prompt_data)
            ])
            
            content = response.content
            
            # Cleaning markdown for Forge/OpenAI style responses
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0].strip()
            elif "```" in content:
                content = content.split("```")[1].split("```")[0].strip()
            
            return json.loads(content)

        except Exception as e:
            print(f"Critic execution failed: {e}")
            return {"error": str(e)}

if __name__ == "__main__":
    agent = CriticAgent()
    reflection = agent.generate_reflection()
    print("\n--- Crypto Strategy Reflection ---")
    print(json.dumps(reflection, indent=2))