import os
import json
import google.generativeai as genai
from core.config import GEMINI_API_KEY, LLM_MODEL, RULES_PATH
from dotenv import load_dotenv

# Import the prompt we just created
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.prompts import SCREENER_SYSTEM_PROMPT

class ScreenerAgent:
    def __init__(self):
        genai.configure(api_key=GEMINI_API_KEY)
        load_dotenv()
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("GEMINI_API_KEY not found in .env file.")
        
        genai.configure(api_key=api_key)
        
        # 🆕 Load approved dynamic rules
        dynamic_rules_text = self._load_dynamic_rules()
        full_prompt = SCREENER_SYSTEM_PROMPT + "\n\n" + dynamic_rules_text
        
        self.model = genai.GenerativeModel(
            model_name="gemini-2.5-flash",
            system_instruction=full_prompt,
            generation_config={"response_mime_type": "application/json"}
        )

    def _load_dynamic_rules(self) -> str:
        """Reads approved rules and formats them for the prompt."""
        rules_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'dynamic_rules.json')
        if os.path.exists(rules_path):
            try:
                with open(rules_path, 'r') as f:
                    rules = json.load(f)
                    if rules and isinstance(rules, list):
                        formatted_rules = "\n".join([f"- {rule}" for rule in rules])
                        return f"CRITICAL DYNAMIC RULES (Learned from past trades):\n{formatted_rules}"
            except Exception as e:
                print(f"Error loading dynamic rules: {e}")
        return ""
    
    def analyze_candidates(self, candidates_data: list) -> dict:
        """
        Passes the technical data to the LLM and returns a structured JSON decision.
        """
        if not candidates_data:
            return {"action": "NONE", "reasoning": "No candidates provided by the data pipeline."}
        
        # Convert the Python list of dictionaries into a compact JSON string
        prompt_data = f"Today's Candidate Data:\n{json.dumps(candidates_data, indent=2)}"
        
        print("Agent is analyzing the market data...")
        try:
            response = self.model.generate_content(prompt_data)
            decision = json.loads(response.text)
            return decision
        except json.JSONDecodeError:
            print("Failed to parse JSON from LLM response.")
            return {"action": "NONE", "reasoning": "Parsing error in agent output."}
        except Exception as e:
            print(f"Agent execution failed: {e}")
            return {"action": "NONE", "reasoning": str(e)}

# Quick test execution block
if __name__ == "__main__":
    # Mock data simulating what the IDXScraper + Indicators would output
    mock_market_data = [
        {
            "ticker": "ASII",
            "close_price": 5000.0,
            "volume_surge_ratio": 2.5,
            "RSI": 45.2,
            "MACD_Hist": 12.5
        },
        {
            "ticker": "BBCA",
            "close_price": 9800.0,
            "volume_surge_ratio": 0.8,
            "RSI": 75.0,
            "MACD_Hist": -5.0
        }
    ]
    
    agent = ScreenerAgent()
    decision = agent.analyze_candidates(mock_market_data)
    print("\n--- Agent Decision ---")
    print(json.dumps(decision, indent=2))