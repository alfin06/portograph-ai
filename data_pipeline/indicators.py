import pandas as pd
import numpy as np

class TechnicalIndicators:
    """
    A utility class to calculate technical indicators using pure Pandas.
    This avoids heavy dependencies like TA-Lib.
    """

    @staticmethod
    def add_rsi(df: pd.DataFrame, window: int = 14) -> pd.DataFrame:
        """
        Calculates the Relative Strength Index (RSI) and appends it as a column.
        A value < 30 indicates oversold, > 70 indicates overbought.
        """
        delta = df['Close'].diff()
        
        # Separate gains and losses
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)
        
        # Use exponential moving average for Wilder's smoothing
        avg_gain = gain.ewm(alpha=1/window, min_periods=window, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1/window, min_periods=window, adjust=False).mean()
        
        # Calculate RS and RSI
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        
        df['RSI'] = rsi.round(2)
        return df

    @staticmethod
    def add_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
        """
        Calculates MACD, Signal Line, and Histogram.
        A positive histogram indicates bullish momentum.
        """
        # Calculate Exponential Moving Averages
        ema_fast = df['Close'].ewm(span=fast, adjust=False).mean()
        ema_slow = df['Close'].ewm(span=slow, adjust=False).mean()
        
        # Calculate MACD Line
        df['MACD'] = (ema_fast - ema_slow).round(2)
        
        # Calculate Signal Line
        df['MACD_Signal'] = df['MACD'].ewm(span=signal, adjust=False).mean().round(2)
        
        # Calculate Histogram (MACD - Signal)
        df['MACD_Hist'] = (df['MACD'] - df['MACD_Signal']).round(2)
        
        return df
        
    @staticmethod
    def add_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
        """Applies all technical indicators to the DataFrame."""
        if df.empty or len(df) < 26:  # Need enough data for MACD
            return df
            
        df = TechnicalIndicators.add_rsi(df)
        df = TechnicalIndicators.add_macd(df)
        return df

# Quick test execution block
if __name__ == "__main__":
    # We will import the scraper to test them together
    import sys
    import os
    sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from data_pipeline.idx_scraper import IDXScraper
    
    print("Fetching data to test indicators...")
    scraper = IDXScraper(["BBCA", "ASII"]) 
    market_data = scraper.fetch_market_data(period="3mo") # Need 3 months for reliable EMA smoothing
    
    for ticker, df in market_data.items():
        # Apply indicators
        df_with_ti = TechnicalIndicators.add_all_indicators(df)
        
        # Get the latest day's data
        latest = df_with_ti.iloc[-1]
        
        print(f"\n--- {ticker} Technicals ---")
        print(f"Close Price: {latest['Close']}")
        print(f"RSI (14):    {latest['RSI']} (Target < 40 for rebounds)")
        print(f"MACD Hist:   {latest['MACD_Hist']} (> 0 is bullish)")