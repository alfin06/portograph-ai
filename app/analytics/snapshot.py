"""Per-instrument end-of-day snapshot: price, returns, indicators and simple signals.

This is the shared analytics layer: the digest job, the Telegram bot and (later)
the web app all read numbers from here, never compute them on their own."""
from dataclasses import dataclass, field

import pandas as pd

from data_pipeline.indicators import TechnicalIndicators

# Signal thresholds (kept here so tests and the digest agree)
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70
VOLUME_SURGE = 2.0     # today's volume vs the previous 5-day average
BIG_MOVE_PCT = 5.0     # absolute 1-day move worth flagging
RSI_WINDOW = 14
TRADING_DAYS_1M = 21
TRADING_DAYS_52W = 252


@dataclass
class Snapshot:
    symbol: str
    market: str
    currency: str
    is_holding: bool
    date: pd.Timestamp
    close: float
    chg_1d: float | None = None    # percent
    chg_5d: float | None = None
    chg_1m: float | None = None
    rsi: float | None = None
    vol_surge: float | None = None
    high_52w: float | None = None
    low_52w: float | None = None
    signals: list[str] = field(default_factory=list)


def _pct(series: pd.Series, lookback: int) -> float | None:
    if len(series) <= lookback:
        return None
    prev = series.iloc[-1 - lookback]
    if not prev:
        return None
    return (series.iloc[-1] / prev - 1) * 100


def _clean(v) -> float | None:
    return None if v is None or pd.isna(v) else float(v)


def build_snapshot(inst: dict, bars: pd.DataFrame) -> Snapshot | None:
    """inst is an `instruments` row; bars is OHLCV history (oldest first)."""
    if bars is None or bars.empty:
        return None
    df = TechnicalIndicators.add_all_indicators(bars.copy())
    close = df["Close"]
    last = df.iloc[-1]
    year = close.tail(TRADING_DAYS_52W)

    snap = Snapshot(
        symbol=inst["symbol"], market=inst["market"], currency=inst["currency"],
        is_holding=bool(inst.get("is_holding")), date=df.index[-1], close=float(last["Close"]),
        chg_1d=_pct(close, 1), chg_5d=_pct(close, 5), chg_1m=_pct(close, TRADING_DAYS_1M),
        # add_rsi fills its warm-up period with 0, so only trust it past that window
        rsi=_clean(last.get("RSI")) if len(df) > RSI_WINDOW else None, vol_surge=_clean(last.get("Vol_Surge")),
        high_52w=float(year.max()), low_52w=float(year.min()),
    )
    snap.signals = detect_signals(snap, enough_history=len(close) >= 60)
    return snap


def detect_signals(s: Snapshot, enough_history: bool = True) -> list[str]:
    out = []
    if s.chg_1d is not None and abs(s.chg_1d) >= BIG_MOVE_PCT:
        out.append(f"{'up' if s.chg_1d > 0 else 'down'} {abs(s.chg_1d):.1f}% today")
    if s.rsi is not None:
        if s.rsi <= RSI_OVERSOLD:
            out.append(f"RSI {s.rsi:.0f} (oversold)")
        elif s.rsi >= RSI_OVERBOUGHT:
            out.append(f"RSI {s.rsi:.0f} (overbought)")
    if s.vol_surge is not None and s.vol_surge >= VOLUME_SURGE:
        out.append(f"volume {s.vol_surge:.1f}× 5-day avg")
    # 52-week extremes only mean something with a decent amount of history
    if enough_history:
        if s.close >= s.high_52w:
            out.append("new 52-week high")
        elif s.close <= s.low_52w:
            out.append("new 52-week low")
    return out


def fx_change(series: pd.Series) -> tuple[float | None, float | None]:
    """(latest rate, 1-day % change) for an FX series."""
    if series is None or series.empty:
        return None, None
    return float(series.iloc[-1]), _pct(series, 1)


def market_snapshots(db, market: str) -> list[Snapshot]:
    snaps = []
    for inst in db.get_instruments(market=market):
        snap = build_snapshot(inst, db.get_price_history(inst["symbol"], days=TRADING_DAYS_52W + 30))
        if snap is not None:
            snaps.append(snap)
    return snaps
