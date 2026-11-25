"""Yahoo Finance provider (unofficial API via yfinance). Covers IDX (.JK), US stocks/ETFs and FX.
Note: Yahoo is blocked in mainland China, so a VPN is needed when running locally there."""
import logging
import pandas as pd

from app.providers.base import normalize_ohlcv
from app.symbols import Instrument, FX_YAHOO

log = logging.getLogger(__name__)


class YahooProvider:
    name = "yahoo"

    def __init__(self, downloader=None):
        # downloader is injectable so tests can run without network
        if downloader is None:
            import yfinance as yf
            downloader = yf.download
        self._download = downloader

    def get_history(self, instruments: list[Instrument], period: str = "1y") -> dict[str, pd.DataFrame]:
        if not instruments:
            return {}
        yahoo_to_plain = {i.yahoo: i.symbol for i in instruments}
        try:
            raw = self._download(
                tickers=list(yahoo_to_plain), period=period, interval="1d",
                group_by="ticker", auto_adjust=True, threads=True, progress=False,
            )
        except Exception as e:  # network / Yahoo format change
            log.error("Yahoo download failed: %s", e)
            return {}
        if raw is None or raw.empty:
            return {}

        out: dict[str, pd.DataFrame] = {}
        multi = isinstance(raw.columns, pd.MultiIndex)
        for ysym, plain in yahoo_to_plain.items():
            if multi:
                if ysym not in raw.columns.get_level_values(0):
                    log.warning("No data for %s", ysym)
                    continue
                df = raw[ysym]
            else:
                df = raw
            df = normalize_ohlcv(df)
            if not df.empty:
                out[plain] = df
        return out

    def get_fx(self, pair: str, period: str = "1y") -> pd.Series:
        ysym = FX_YAHOO.get(pair.upper())
        if ysym is None:
            raise ValueError(f"Unsupported FX pair: {pair}")
        try:
            raw = self._download(tickers=[ysym], period=period, interval="1d",
                                 group_by="ticker", auto_adjust=True, progress=False)
        except Exception as e:
            log.error("Yahoo FX download failed: %s", e)
            return pd.Series(dtype=float, name=pair)
        if raw is None or raw.empty:
            return pd.Series(dtype=float, name=pair)
        df = raw[ysym] if isinstance(raw.columns, pd.MultiIndex) else raw
        return normalize_ohlcv(df)["Close"].rename(pair.upper())
