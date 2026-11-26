"""Position sizing by risk: buy only as much as you can afford to lose if the stop is hit.

    shares = (capital x risk %) / (entry - stop)

rounded down to whole lots (IDX trades in lots of 100 shares, US in single shares), then
capped so one position is at most max % of capital. Fees and slippage are not included.
"""
import math
from dataclasses import dataclass

LOT = {"IDX": 100, "US": 1}
DEFAULT_RISK_PCT = 1.0
DEFAULT_MAX_POSITION_PCT = 10.0


@dataclass
class Size:
    currency: str
    lot: int
    shares: int
    capped: bool                 # True if the max-position limit, not risk, set the size
    position: float              # in the stock's currency
    position_idr: float
    weight_pct: float            # position / capital
    loss_at_stop: float          # in the stock's currency
    loss_at_stop_idr: float
    loss_pct: float              # of capital
    stop_distance_pct: float
    risk_budget_idr: float
    one_lot_risk_idr: float      # loss at stop for a single lot
    fractional: float            # shares if fractions were allowed (same risk and cap limits)
    position_idr_per_unit: float # IDR per unit of the stock's currency


def size_position(capital_idr: float, entry: float, stop: float, market: str, currency: str,
                  usdidr: float | None = None, risk_pct: float = DEFAULT_RISK_PCT,
                  max_position_pct: float = DEFAULT_MAX_POSITION_PCT) -> Size:
    if capital_idr <= 0:
        raise ValueError("capital must be positive")
    if not 0 < stop < entry:
        raise ValueError("the stop must be below the entry price")
    if not 0 < risk_pct <= 10 or not 0 < max_position_pct <= 100:
        raise ValueError("risk must be 0-10% and max position 0-100%")
    rate = 1.0
    if currency == "USD":
        if not usdidr:
            raise ValueError("no USD/IDR rate stored")
        rate = usdidr
    lot = LOT.get(market, 1)
    risk_budget = capital_idr * risk_pct / 100 / rate          # in the stock's currency
    per_share = entry - stop
    by_risk = math.floor(risk_budget / per_share / lot) * lot
    by_cap = math.floor(capital_idr * max_position_pct / 100 / rate / entry / lot) * lot
    shares = max(0, min(by_risk, by_cap))
    fractional = min(risk_budget / per_share, capital_idr * max_position_pct / 100 / rate / entry)
    position = shares * entry
    loss = shares * per_share
    return Size(currency, lot, shares, by_cap < by_risk, position, position * rate,
                position * rate / capital_idr * 100, loss, loss * rate, loss * rate / capital_idr * 100,
                per_share / entry * 100, risk_budget * rate, lot * per_share * rate, fractional, rate)
