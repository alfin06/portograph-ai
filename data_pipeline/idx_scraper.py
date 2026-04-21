import yfinance as yf
import pandas as pd
from datetime import datetime

class IDXScraper:
    def __init__(self, tickers=None):
        # Default to high-liquidity and specific blue-chip IDX stocks
        if tickers is None:
            self.tickers = ["BBCA", "BBRI", "ADRO", "ITMG", "ASII", "TLKM", "BMRI", "BBNI", "AMMN", "BRPT"]
        else:
            self.tickers = tickers

    def _format_ticker(self, ticker: str) -> str:
        """Appends the .JK suffix required by Yahoo Finance for IDX stocks."""
        return ticker if ticker.endswith(".JK") else f"{ticker}.JK"

    def fetch_market_data(self, period="1mo") -> dict:
        """
        Fetches historical data for the defined tickers.
        Returns a dictionary mapping the raw ticker (e.g., 'ASII') to its Pandas DataFrame.
        """
        formatted_tickers = [self._format_ticker(t) for t in self.tickers]
        print(f"Fetching data for {len(formatted_tickers)} stocks...")
        
        # Download all data at once for efficiency
        data = yf.download(formatted_tickers, period=period, group_by='ticker', progress=False)
        
        market_dict = {}
        for original, formatted in zip(self.tickers, formatted_tickers):
            try:
                # Handle single ticker vs multi-ticker yfinance structure
                if len(formatted_tickers) == 1:
                    df = data.copy()
                else:
                    df = data[formatted].copy()
                
                # Drop NaN rows (e.g., holidays)
                df.dropna(inplace=True)
                
                if not df.empty:
                    market_dict[original] = df
            except KeyError:
                print(f"Warning: Could not fetch data for {original}")
                
        return market_dict

    def get_screener_candidates(self) -> list:
        """
        Processes the raw data and returns a clean list of dictionaries summarizing 
        today's action vs the 5-day average. This is what we will feed to the LLM.
        """
        market_data = self.fetch_market_data(period="5d")
        candidates = []

        for ticker, df in market_data.items():
            if len(df) < 2:
                continue

            # Get the most recent day (today) and previous days
            today = df.iloc[-1]
            prev_days = df.iloc[:-1]

            # Calculate basic metrics
            current_close = float(today['Close'])
            current_volume = float(today['Volume'])
            avg_volume_5d = float(prev_days['Volume'].mean())
            
            # Prevent division by zero
            if avg_volume_5d == 0:
                continue
                
            volume_surge = current_volume / avg_volume_5d
            price_change_pct = ((current_close - float(df.iloc[-2]['Close'])) / float(df.iloc[-2]['Close'])) * 100

            candidates.append({
                "ticker": ticker,
                "close_price": current_close,
                "price_change_pct": round(price_change_pct, 2),
                "volume": current_volume,
                "volume_surge_ratio": round(volume_surge, 2)
            })

        # Sort by volume surge (highest first) to find where the "Big Money" is moving
        candidates.sort(key=lambda x: x['volume_surge_ratio'], reverse=True)
        return candidates

# Quick test execution block
if __name__ == "__main__":
    scraper = IDXScraper()
    print("Initializing IDX Scraper...")
    
    # Get today's top candidates based on volume surges
    candidates = scraper.get_screener_candidates()
    
    print("\n--- Top Candidates for Agent Review ---")
    for c in candidates[:5]: # Show top 5
        print(f"[{c['ticker']}] Price: {c['close_price']} IDR | Change: {c['price_change_pct']}% | Vol Surge: {c['volume_surge_ratio']}x")