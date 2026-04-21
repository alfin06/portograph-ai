import os
import sys
import json
import google.generativeai as genai
from dotenv import load_dotenv

# Ensure imports work from the root directory
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database.db_manager import DBManager

CRITIC_SYSTEM_PROMPT = """You are the Lead Quantitative Strategist and Reflection Agent for PortoGraph AI.
Your job is to review the trading history and user feedback for the BSJP (Beli Sore Jual Pagi) strategy.
Analyze the patterns in the wins (CLOSED_WIN) and losses (CLOSED_LOSS). Look for correlations between the agent's original reasoning and the actual outcome.

Return a structured JSON output with the following schema:
{
    "win_rate_analysis": "A concise string detailing why recent trades succeeded or failed based on the feedback.",
    "market_regime": "A short inference of current market conditions (e.g., 'High volatility, gap-downs common').",
    "adjusted_rules": [
        "Rule 1 to add to the screener's prompt to avoid repeating mistakes",
        "Rule 2...",
        "Rule 3..."
    ]
}
"""

class CriticAgent:
    def __init__(self):
        load_dotenv()
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY not found in .env file.")
        
        genai.configure(api_key=api_key)
        
        self.model = genai.GenerativeModel(
            model_name="gemini-2.5-flash",
            system_instruction=CRITIC_SYSTEM_PROMPT,
            generation_config={"response_mime_type": "application/json"}
        )
        self.db = DBManager()

    def fetch_closed_trades(self) -> list:
        """Pulls only the trades that have been completed and given user feedback."""
        query = """
            SELECT ticker, date_suggested, action, suggested_price, status, 
                   pnl_percentage, agent_reasoning, user_feedback 
            FROM trade_history 
            WHERE status IN ('CLOSED_WIN', 'CLOSED_LOSS')
        """
        with self.db._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(query)
            return [dict(row) for row in cursor.fetchall()]

    def generate_reflection(self) -> dict:
        """Sends the trade history to the LLM to generate strategic adjustments."""
        trades = self.fetch_closed_trades()
        
        if not trades:
            return {"message": "No closed trades available for analysis yet."}
        
        # Convert trade history into a clean JSON string for the prompt
        prompt_data = f"Recent Trade History and Feedback:\n{json.dumps(trades, indent=2)}"
        
        print("🔍 Critic Agent is analyzing past performance...")
        try:
            response = self.model.generate_content(prompt_data)
            insights = json.loads(response.text)
            return insights
        except json.JSONDecodeError:
            print("Failed to parse JSON from LLM response.")
            return {"error": "Parsing error in agent output."}
        except Exception as e:
            print(f"Critic execution failed: {e}")
            return {"error": str(e)}

# Quick test execution block
if __name__ == "__main__":
    print("Initializing Critic Agent...")
    
    # Let's insert a mock closed trade into the database first to test the logic
    db = DBManager()
    mock_trade_id = db.log_suggested_trade("BRPT", "BUY", 1000, 1015, 980, "High volume surge at close.")
    db.update_execution(mock_trade_id, 1000)
    # Simulate a loss where the stock gapped down the next morning
    db.update_feedback(mock_trade_id, "CLOSED_LOSS", -2.0, "Stock gapped down at the open. Volume was a fakeout.")
    
    agent = CriticAgent()
    reflection = agent.generate_reflection()
    
    print("\n--- Agent Reflection & Adjustments ---")
    print(json.dumps(reflection, indent=2))