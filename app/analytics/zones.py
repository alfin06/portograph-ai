"""Buy zones: price ranges you'd consider buying in. Each market digest checks the
closes against them and tells you once when a stock moves into (or falls through) a zone.

State per zone is where the last checked close was: above | inside | below.
A message is new when the state changes to inside or below (or on the first check).
"""
from dataclasses import dataclass

STATES = ("above", "inside", "below")


def state_of(close: float, low: float, high: float) -> str:
    if close > high:
        return "above"
    return "inside" if close >= low else "below"


def distance_pct(close: float, low: float, high: float) -> float:
    """How far the close must move to reach the zone (negative = must fall), 0 inside it."""
    if close > high:
        return (high / close - 1) * 100
    if close < low:
        return (low / close - 1) * 100
    return 0.0


@dataclass
class ZoneEvent:
    zone: dict
    close: float
    state: str          # inside | below
    new: bool           # just changed into this state


def check(db, market: str, snaps, session_date, record: bool = True) -> list[ZoneEvent]:
    """Zones in this market whose stock closed inside or below them this session."""
    by_symbol = {s.symbol: s for s in snaps if s.date == session_date}
    events = []
    for z in db.get_zones(market=market):
        snap = by_symbol.get(z["symbol"])
        if snap is None:
            continue
        st = state_of(snap.close, z["low"], z["high"])
        changed = st != z["state"]
        if record and changed:
            db.set_zone_state(z["id"], st, f"{session_date:%Y-%m-%d}")
        if st != "above":
            events.append(ZoneEvent(z, snap.close, st, changed))
    return events
