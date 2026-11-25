"""Yahoo Finance provider (unofficial API via yfinance). Covers IDX (.JK), US stocks/ETFs and FX.
Note: Yahoo is blocked in mainland China, so a VPN is needed when running locally there."""
import logging
import pandas as pd

from app.providers.base import normalize_ohlcv
from app.symbols import Instrument, FX_YAHOO

log = logging.getLogger(__name__)

GAP_LOOKBACK_ROWS = 5  # only patch blank closes this recent; older gaps are real holidays


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
        gaps: dict[str, list] = {}   # ysym -> recent dates whose daily close is blank
        multi = isinstance(raw.columns, pd.MultiIndex)
        for ysym, plain in yahoo_to_plain.items():
            if multi:
                if ysym not in raw.columns.get_level_values(0):
                    log.warning("No data for %s", ysym)
                    continue
                df = raw[ysym]
            else:
                df = raw
            if "Close" in df.columns:
                recent = df["Close"].tail(GAP_LOOKBACK_ROWS)
                blank = [pd.Timestamp(d).tz_localize(None).normalize() if pd.Timestamp(d).tzinfo
                         else pd.Timestamp(d).normalize() for d in recent.index[recent.isna()]]
                if blank:
                    gaps[ysym] = blank
            df = normalize_ohlcv(df)
            if not df.empty:
                out[plain] = df
        if gaps:
            self._fill_from_intraday(out, gaps, yahoo_to_plain)
        return out

    def _fill_from_intraday(self, out: dict, gaps: dict, yahoo_to_plain: dict) -> None:
        """Yahoo sometimes posts a session's daily bar hours late, blank, for IDX stocks
        while its hourly bars are already there. Rebuild those days from the hourly bars;
        the real daily bar overwrites it on a later refresh."""
        try:
            raw = self._download(tickers=list(gaps), period="5d", interval="60m",
                                 group_by="ticker", auto_adjust=True, threads=True, progress=False)
        except Exception as e:
            log.warning("Yahoo intraday download failed: %s", e)
            return
        if raw is None or raw.empty:
            return
        multi = isinstance(raw.columns, pd.MultiIndex)
        for ysym, dates in gaps.items():
            if multi and ysym not in raw.columns.get_level_values(0):
                continue
            bars = (raw[ysym] if multi else raw).dropna(subset=["Close"])
            if bars.empty:
                continue
            # the index is in the exchange's own time zone, so .date is the local session day
            day = pd.to_datetime(bars.index.date)
            daily = bars.groupby(day).agg({"Open": "first", "High": "max", "Low": "min",
                                           "Close": "last", "Volume": "sum"})
            daily = daily[daily.index.isin(dates)]
            if daily.empty:
                continue
            plain = yahoo_to_plain[ysym]
            log.info("%s: daily bar blank on Yahoo for %s, rebuilt from hourly bars", plain,
                     ", ".join(f"{d:%Y-%m-%d}" for d in daily.index))
            base = out.get(plain, normalize_ohlcv(None))
            out[plain] = normalize_ohlcv(pd.concat([base, daily]))

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
