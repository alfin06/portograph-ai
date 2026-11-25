import os
import json
import sys
from langchain_openai import ChatOpenAI
from dotenv import load_dotenv


from modules.crypto.prompts import SCREENER_SYSTEM_PROMPT 
from core.config import RULES_PATH, FORGE_API_KEY, FORGE_BASE_URL, LLM_MODEL

class ScreenerAgent:
    def __init__(self):
        load_dotenv()
        # Initialize for Forge (gpt-5-mini)
        self.llm = ChatOpenAI(
            model=LLM_MODEL,
            openai_api_key=FORGE_API_KEY,
            openai_api_base=FORGE_BASE_URL,
            temperature=1,
            model_kwargs={"seed": 42}
        )
        self.full_prompt = SCREENER_SYSTEM_PROMPT + "\n\n" + self._load_dynamic_rules()

    def _load_dynamic_rules(self) -> str:
        """Loads rules learned by the Critic Agent from dynamic_rules.json."""
        rules_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), RULES_PATH)
        if os.path.exists(rules_path):
            try:
                with open(rules_path, 'r') as f:
                    rules = json.load(f)
                    if rules and isinstance(rules, list):
                        return "CRITICAL DYNAMIC RULES:\n" + "\n".join([f"- {r}" for r in rules])
            except Exception as e:
                print(f"Error loading dynamic rules: {e}")
        return ""

    def analyze_candidates(self, candidates_data: list) -> dict:
        """Analyzes Crypto snapshots via Forge and returns targets."""
        if not candidates_data:
            return {"action": "NONE", "reasoning": "No crypto pairs provided."}
        
        # Cosmetic Fix: Changed 'stocks' to 'crypto pairs'
        print(f"Agent analyzing {len(candidates_data)} crypto pairs via Forge...")
        
        try:
            response = self.llm.invoke([
                ("system", self.full_prompt),
                ("human", f"Market Snapshot: {json.dumps(candidates_data)}")
            ])
            
            content = response.content.replace("```json", "").replace("```", "").strip()
            return json.loads(content)
        except Exception as e:
            print(f"Agent execution failed: {e}")
            return {"action": "NONE", "reasoning": str(e)}