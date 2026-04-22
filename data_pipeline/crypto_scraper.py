import os
import sys
import pandas as pd
import requests
import time
from dotenv import load_dotenv

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from data_pipeline.indicators import TechnicalIndicators

load_dotenv()

class CryptoScraper:
    def __init__(self, tickers=None):
        # Indodax tradingview/history_v2 usually prefers lowercase_underscore
        self.tickers = tickers if tickers else ['BTCIDR', 'ETHIDR', 'HYPEIDR']
        self.base_url = "https://indodax.com/tradingview/history_v2"

    def fetch_ohlcv(self, days=45):
        """Pulls OHLCV data and handles the v2 list-of-lists format."""
        print(f"Fetching LIVE data for {len(self.tickers)} pairs from Indodax...")
        market_data = {}
        
        to_time = int(time.time())
        from_time = to_time - (days * 86400)
        
        for pair in self.tickers:
            params = {
                "symbol": pair.lower(), # Ensure lowercase for the URL
                "tf": "1D",
                "from": from_time,
                "to": to_time
            }
            
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
            }
            
            try:
                response = requests.get(self.base_url, params=params, headers=headers, timeout=15)
                
                if response.status_code == 200:
                    data = response.json()
                    
                    # --- THE FIX: Handle List of Lists format ---
                    if isinstance(data, list) and len(data) > 0:
                        # Indodax v2 format: [timestamp, open, high, low, close, volume]
                        df = pd.DataFrame(data, columns=['Timestamp', 'Open', 'High', 'Low', 'Close', 'Volume'])
                        df['Date'] = pd.to_datetime(df['Timestamp'], unit='s')
                        df.set_index('Date', inplace=True)
                        df = df[['Open', 'High', 'Low', 'Close', 'Volume']].astype(float)
                        
                        market_data[pair.upper()] = df
                        print(f"    [OK] {pair.upper()} ({len(df)} days)")
                        
                    # Fallback for the old dictionary format
                    elif isinstance(data, dict) and data.get('s') == 'ok':
                        df = pd.DataFrame({
                            'Open': data['o'], 'High': data['h'], 
                            'Low': data['l'], 'Close': data['c'], 'Volume': data['v']
                        }, index=pd.to_datetime(data['t'], unit='s'))
                        
                        market_data[pair.upper()] = df.astype(float)
                        print(f"    [OK] {pair.upper()} ({len(df)} days)")
                    else:
                        print(f"    [!] No valid data for {pair}. Response: {str(data)[:50]}...")
                else:
                    print(f"    [!] HTTP {response.status_code} for {pair}")
                    
            except Exception as e:
                print(f"    [!] Connection error for {pair}: {e}")
            
        return market_data

    def get_market_snapshot(self):
        """Processes the raw data into a clean snapshot for the AI Agent."""
        market_data = self.fetch_ohlcv()
        snapshot = []
        
        for ticker, df in market_data.items():
            if len(df) < 26: 
                print(f"    [!] Skipping {ticker}: Not enough history.")
                continue
                
            df = TechnicalIndicators.add_all_indicators(df)
            today = df.iloc[-1]
            
            snapshot.append({
                "ticker": ticker.replace("_", "/"),
                "current_price": float(today['Close']),
                "RSI": float(today.get('RSI', 0)),
                "MACD_Hist": float(today.get('MACD_Hist', 0)),
                "Vol_Surge": float(today.get('Vol_Surge', 0))
            })
        return snapshot