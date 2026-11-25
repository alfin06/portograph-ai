import os
import sys
import pandas as pd
import yfinance as yf
from dotenv import load_dotenv

from data_pipeline.indicators import TechnicalIndicators

load_dotenv()

class IDXScraper:
    def __init__(self, tickers=None):
        # Tickers for Yahoo Finance Indonesia
        self.tickers = tickers if tickers else [
            'BMRI.JK', 'BRPT.JK', 'TLKM.JK', 'BUMI.JK', 'ADRO.JK',
        ]

    def fetch_market_data(self, period="3mo"):
        """Pulls LIVE data. We stop setting a manual session as per the error fix."""
        print(f"Fetching LIVE data for {len(self.tickers)} stocks via Yahoo Finance...")
        market_data = {}
        
        try:
            # We let yfinance handle its own session and connection logic
            data = yf.download(
                tickers=self.tickers, 
                period=period, 
                interval="1d", 
                group_by='ticker',
                threads=True,
                progress=False
            )
            
            if data.empty:
                print("❌ No data returned from Yahoo Finance.")
                return {}

            for ticker in self.tickers:
                # Extract the data for this specific ticker
                if len(self.tickers) > 1:
                    df = data[ticker].copy()
                else:
                    df = data.copy()
                
                df.dropna(inplace=True)
                
                if not df.empty:
                    market_data[ticker] = df
                    print(f"    [OK] {ticker}")
                else:
                    print(f"    [!] No data found for {ticker}")
                    
        except Exception as e:
            print(f"❌ Yahoo Finance Connection Error: {e}")
            
        return market_data

    def get_screener_candidates(self):
        """Processes real data for the AI Agent."""
        market_data = self.fetch_market_data()
        candidates = []
        
        if not market_data:
            return []

        for ticker, df in market_data.items():
            if len(df) < 20: continue
                
            # Use indicators.py to add RSI, MACD, etc.
            df = TechnicalIndicators.add_all_indicators(df)
            today = df.iloc[-1]
            prev_days = df.iloc[-6:-1]
            
            avg_vol = prev_days['Volume'].mean()
            vol_surge = today['Volume'] / avg_vol if avg_vol > 0 else 0
            
            candidates.append({
                "ticker": ticker.replace(".JK", ""),
                "close_price": float(today['Close']),
                "volume_surge_ratio": round(float(vol_surge), 2),
                "RSI": float(today.get('RSI', 0)),
                "MACD_Hist": float(today.get('MACD_Hist', 0))
            })
            
        candidates.sort(key=lambda x: x['volume_surge_ratio'], reverse=True)
        return candidates[:5]

if __name__ == "__main__":
    scraper = IDXScraper()
    cands = scraper.get_screener_candidates()
    print(f"\n--- REAL MARKET DATA PREVIEW ---")
    for c in cands:
        print(f"{c['ticker']}: Price {c['close_price']}, Vol Surge {c['volume_surge_ratio']}x")