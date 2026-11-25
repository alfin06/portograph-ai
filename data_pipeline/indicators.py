import pandas as pd
import numpy as np

class TechnicalIndicators:
    """
    Utility class for calculating technical indicators.
    Optimized for the PortoGraph AI BSJP/Crypto Strategy.
    """

    @staticmethod
    def add_rsi(df: pd.DataFrame, window: int = 14) -> pd.DataFrame:
        """
        Calculates RSI using Wilder's Smoothing.
        Safely handles division by zero if there are no losses.
        """
        delta = df['Close'].diff()
        gain = delta.where(delta > 0, 0.0)
        loss = -delta.where(delta < 0, 0.0)
        
        # Wilder's Smoothing (alpha=1/N)
        avg_gain = gain.ewm(alpha=1/window, min_periods=window, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1/window, min_periods=window, adjust=False).mean()
        
        # Handle division by zero: if avg_loss is 0, RSI is 100
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))
        
        # Use numpy to replace inf/nan with 100 (if no losses) or 0 (if no gains)
        df['RSI'] = rsi.replace([np.inf, -np.inf], 100).fillna(0).round(2)
        return df

    @staticmethod
    def add_macd(df: pd.DataFrame, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
        """Standard MACD with Fast, Slow, and Signal lines."""
        ema_fast = df['Close'].ewm(span=fast, adjust=False).mean()
        ema_slow = df['Close'].ewm(span=slow, adjust=False).mean()
        
        df['MACD'] = (ema_fast - ema_slow).round(2)
        df['MACD_Signal'] = df['MACD'].ewm(span=signal, adjust=False).mean().round(2)
        df['MACD_Hist'] = (df['MACD'] - df['MACD_Signal']).round(2)
        
        return df

    @staticmethod
    def add_volume_surge(df: pd.DataFrame, window: int = 5) -> pd.DataFrame:
        """
        Calculates the Volume Surge Ratio.
        Compares today's volume to the average of the previous N days.
        """
        # Average volume of the previous 'window' days (excluding today)
        df['Avg_Vol_Prev'] = df['Volume'].shift(1).rolling(window=window).mean()
        
        # Today's Volume / Previous Average
        df['Vol_Surge'] = (df['Volume'] / df['Avg_Vol_Prev']).round(2)
        
        # Replace NaN/Inf if volume is zero
        df['Vol_Surge'] = df['Vol_Surge'].fillna(0).replace(np.inf, 0)
        return df
        
    @staticmethod
    def add_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
        """Applies all strategy-critical indicators to the DataFrame."""
        if df.empty or len(df) < 26:
            return df
            
        df = TechnicalIndicators.add_rsi(df)
        df = TechnicalIndicators.add_macd(df)
        df = TechnicalIndicators.add_volume_surge(df)
        return df
