"""Provider interface. Every data source implements this, so a broken source
(yfinance changes often) can be swapped without touching analytics or the bot."""
from typing import Protocol
import pandas as pd

from app.symbols import Instrument

# Every history frame uses these columns, indexed by a tz-naive DatetimeIndex
OHLCV_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]


class MarketDataProvider(Protocol):
    name: str

    def get_history(self, instruments: list[Instrument], period: str = "1y") -> dict[str, pd.DataFrame]:
        """Daily OHLCV keyed by plain symbol. Missing symbols are simply absent."""
        ...

    def get_fx(self, pair: str, period: str = "1y") -> pd.Series:
        """Daily close of an FX pair such as 'USDIDR'."""
        ...


def normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce any provider frame into the standard OHLCV shape."""
    if df is None or df.empty:
        return pd.DataFrame(columns=OHLCV_COLUMNS)
    df = df.copy()
    missing = [c for c in OHLCV_COLUMNS if c not in df.columns]
    for c in missing:
        df[c] = float("nan")
    df = df[OHLCV_COLUMNS].astype(float)
    df = df.dropna(subset=["Close"])
    idx = pd.to_datetime(df.index)
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    df.index = idx.normalize()
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df
