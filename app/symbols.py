"""Maps between our plain symbols (BBCA, VOO) and each data provider's format."""
from dataclasses import dataclass

FX_YAHOO = {"USDIDR": "IDR=X", "USDCNY": "CNY=X"}


@dataclass(frozen=True)
class Instrument:
    symbol: str
    market: str      # 'IDX' | 'US'
    asset_type: str  # 'STOCK' | 'ETF'
    currency: str    # 'IDR' | 'USD'

    @property
    def yahoo(self) -> str:
        return to_yahoo(self.symbol, self.market)


def to_yahoo(symbol: str, market: str) -> str:
    symbol = symbol.strip().upper()
    if market == "IDX":
        return symbol if symbol.endswith(".JK") else f"{symbol}.JK"
    if market == "US":
        # Yahoo uses '-' for share classes: BRK.B -> BRK-B
        return symbol.replace(".", "-")
    raise ValueError(f"Unknown market: {market}")


def from_yahoo(yahoo_symbol: str) -> str:
    s = yahoo_symbol.upper()
    return s[:-3] if s.endswith(".JK") else s.replace("-", ".")
