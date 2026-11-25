"""Fear & Greed score (0 = extreme fear, 100 = extreme greed) for the US and IDX markets.

Our own approximation built only from free Yahoo data, in the spirit of CNN's index
(which can't be fetched automatically). Each input is turned into a percentile against
its own last 252 trading days, flipped where a high value means fear, and the inputs are
averaged.

US inputs                                    IDX inputs
  momentum   S&P 500 vs its 125-day avg        momentum   IHSG vs its 125-day avg
  volatility VIX vs its 50-day avg (flipped)   volatility IHSG 20-day realized vol (flipped)
  safe haven SPY minus IEF, 20-day return      rupiah     USD/IDR 20-day change (flipped)
  junk bonds HYG minus IEF, 20-day return      foreign    EIDO minus SPY, 20-day return
"""
from dataclasses import dataclass, field

import pandas as pd

from app.symbols import Instrument

WINDOW = 252          # percentile look-back (1 trading year)
MIN_PERIODS = 60      # need at least this many days before a percentile means anything
MIN_COMPONENTS = 3
BACKFILL_BARS = 400   # 252-day percentile of a 125-day average needs ~380 bars

# Yahoo symbols fetched as-is (market "US" makes to_yahoo leave them unchanged)
REFERENCE = {"US": ["^GSPC", "^VIX", "SPY", "IEF", "HYG"], "IDX": ["^JKSE", "EIDO", "SPY"]}
BASE = {"US": "^GSPC", "IDX": "^JKSE"}  # whose trading calendar the score follows


@dataclass
class Component:
    key: str
    name: str
    value: float               # 0-100, high = greed
    fear_text: str
    greed_text: str

    @property
    def reading(self) -> str:
        if 40 <= self.value <= 60:
            return "neutral"
        return self.fear_text if self.value < 50 else self.greed_text


@dataclass
class Sentiment:
    market: str
    date: pd.Timestamp
    score: float
    week_ago: float | None
    components: list[Component] = field(default_factory=list)

    @property
    def label(self) -> str:
        return label_for(self.score)

    def drivers(self, n: int = 2, min_distance: float = 20) -> list[str]:
        """The inputs furthest from neutral, as short phrases."""
        far = sorted(self.components, key=lambda c: -abs(c.value - 50))
        return [c.reading for c in far[:n] if abs(c.value - 50) >= min_distance]


def label_for(score: float) -> str:
    # CNN's bands
    if score < 25:
        return "Extreme fear"
    if score < 45:
        return "Fear"
    if score <= 55:
        return "Neutral"
    if score <= 75:
        return "Greed"
    return "Extreme greed"


def pct_rank(series: pd.Series, window: int = WINDOW) -> pd.Series:
    """Percentile (0-100) of each value within its trailing window, itself excluded."""
    def rank(w):
        return (w[:-1] < w[-1]).sum() / (len(w) - 1) * 100
    return series.dropna().rolling(window, min_periods=min(MIN_PERIODS, window)).apply(rank, raw=True)


# (key, display name, fear text, greed text) per input
_SPECS = {
    "US": [
        ("momentum", "S&P 500 vs 125-day avg", "S&P 500 below its trend", "S&P 500 above its trend"),
        ("volatility", "VIX vs 50-day avg", "VIX elevated", "VIX calm"),
        ("safe_haven", "Stocks vs Treasuries (20d)", "money moving into bonds", "stocks beating bonds"),
        ("junk_bonds", "High-yield vs Treasuries (20d)", "junk bonds lagging", "junk bonds in demand"),
    ],
    "IDX": [
        ("momentum", "IHSG vs 125-day avg", "IHSG below its trend", "IHSG above its trend"),
        ("volatility", "IHSG 20-day volatility", "IHSG swinging hard", "IHSG calm"),
        ("rupiah", "USD/IDR 20-day change", "rupiah weakening", "rupiah firming"),
        ("foreign", "EIDO vs S&P 500 (20d)", "foreign appetite for Indonesia weak",
         "foreign appetite for Indonesia strong"),
    ],
}


def _inputs(market: str, close: dict[str, pd.Series], usdidr: pd.Series) -> dict[str, pd.Series]:
    """Raw percentile series per input, already oriented so high = greed."""
    def ret20(s):
        return s.pct_change(20)

    def spread(a, b):
        df = pd.concat([ret20(a), ret20(b)], axis=1, join="inner").dropna()
        return df.iloc[:, 0] - df.iloc[:, 1]

    out = {}
    if market == "US":
        gspc, vix = close.get("^GSPC"), close.get("^VIX")
        spy, ief, hyg = close.get("SPY"), close.get("IEF"), close.get("HYG")
        if gspc is not None:
            out["momentum"] = pct_rank(gspc / gspc.rolling(125).mean() - 1)
        if vix is not None:
            out["volatility"] = 100 - pct_rank(vix / vix.rolling(50).mean() - 1)
        if spy is not None and ief is not None:
            out["safe_haven"] = pct_rank(spread(spy, ief))
        if hyg is not None and ief is not None:
            out["junk_bonds"] = pct_rank(spread(hyg, ief))
    else:
        jkse, eido, spy = close.get("^JKSE"), close.get("EIDO"), close.get("SPY")
        if jkse is not None:
            out["momentum"] = pct_rank(jkse / jkse.rolling(125).mean() - 1)
            out["volatility"] = 100 - pct_rank(jkse.pct_change().rolling(20).std())
        if usdidr is not None and not usdidr.empty:
            out["rupiah"] = 100 - pct_rank(usdidr.pct_change(20))
        if eido is not None and spy is not None:
            out["foreign"] = pct_rank(spread(eido, spy))
    return out


def compute(market: str, close: dict[str, pd.Series], usdidr: pd.Series | None = None) -> Sentiment | None:
    base = close.get(BASE[market])
    if base is None or base.empty:
        return None
    series = _inputs(market, close, usdidr)
    if not series:
        return None
    df = pd.concat(series, axis=1, sort=True).ffill()
    # follow the home index's calendar; other inputs carry their last value forward
    df = df.reindex(df.index.union(base.index)).ffill().reindex(base.index)
    score = df.mean(axis=1).where(df.notna().sum(axis=1) >= MIN_COMPONENTS).dropna()
    if score.empty:
        return None

    day = score.index[-1]
    comps = []
    for key, name, fear, greed in _SPECS[market]:
        v = df.at[day, key] if key in df.columns else None
        if v is not None and not pd.isna(v):
            comps.append(Component(key, name, float(v), fear, greed))
    week_ago = float(score.iloc[-6]) if len(score) >= 6 else None
    return Sentiment(market, day, float(score.iloc[-1]), week_ago, comps)


# --- DB glue ----------------------------------------------------------------------
def refresh(db, provider, market: str) -> None:
    """Fetch the reference series for one market into price_bars."""
    syms = REFERENCE[market]
    short = any(len(db.get_price_history(s)) < BACKFILL_BARS for s in syms)
    insts = [Instrument(s, "US", "ETF", "USD") for s in syms]
    for sym, df in provider.get_history(insts, period="2y" if short else "1mo").items():
        db.upsert_price_bars(sym, df)


def from_db(db, market: str) -> Sentiment | None:
    close = {}
    for s in REFERENCE[market]:
        h = db.get_price_history(s)
        if not h.empty:
            close[s] = h["Close"]
    return compute(market, close, db.get_fx_history("USDIDR"))
