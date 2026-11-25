from datetime import date

import numpy as np
import pandas as pd
import pytest

from app import commands as cmd
from app.analytics import etf
from app.analytics.fx_returns import currency_return, period_start
from app.providers.yahoo_funds import YahooFunds, normalize_fund


def series(values, start="2025-01-01", freq="B"):
    return pd.Series(np.asarray(values, dtype=float), index=pd.date_range(start, periods=len(values), freq=freq))


# Shape copied from real yfinance output (Nov 2026), trimmed
VOO_RAW = {
    "info": {"quoteType": "ETF", "longName": "Vanguard S&P 500 ETF", "fundFamily": "Vanguard",
             "category": "Large Blend", "totalAssets": 1.7357e12, "netExpenseRatio": 0.03,
             "yield": 0.0107, "fundInceptionDate": 1283817600},
    "ops": {"Annual Report Expense Ratio": 0.00029999999, "Annual Holdings Turnover": 0.02},
    "top": [("AAPL", "Apple Inc", 0.077), ("NVDA", "NVIDIA Corp", 0.0758), ("MSFT", "Microsoft Corp", 0.0593),
            ("JPM", "JPMorgan", 0.015)],
    "sectors": {"technology": 0.38, "financial_services": 0.12, "healthcare": 0.10, "realestate": 0.02},
}
QQQM_RAW = {
    "info": {"quoteType": "ETF", "longName": "Invesco NASDAQ 100 ETF", "netExpenseRatio": 0.15},
    "ops": {"Annual Report Expense Ratio": 0.0015},
    "top": [("AAPL", "Apple Inc", 0.0816), ("NVDA", "NVIDIA Corp", 0.08), ("MU", "Micron", 0.0448)],
    "sectors": {"technology": 0.55, "healthcare": 0.05, "communication_services": 0.15},
}


class FakeFunds:
    name = "fake"

    def __init__(self, raw_by_symbol):
        self.raw = raw_by_symbol
        self.calls = 0

    def get_profile(self, symbol):
        self.calls += 1
        return normalize_fund(self.raw.get(symbol))


class FakePrices:
    def __init__(self, closes):
        self.closes = closes

    def get_history(self, instruments, period):
        self.asked = [i.symbol for i in instruments]
        out = {}
        for i in instruments:
            c = self.closes.get(i.symbol)
            if c is not None:
                out[i.symbol] = pd.DataFrame({"Open": c, "High": c, "Low": c, "Close": c, "Volume": 0.0})
        return out

    def get_fx(self, pair, period):
        return pd.Series(dtype=float)


# --- provider --------------------------------------------------------------------
def test_normalize_fund_units():
    p = normalize_fund(VOO_RAW)
    assert p["expense_ratio"] == pytest.approx(0.03)
    assert p["yield"] == pytest.approx(1.07)
    assert p["turnover"] == pytest.approx(2.0)
    assert p["inception"] == "2010-09-07"
    assert p["top_holdings"][0] == {"symbol": "AAPL", "name": "Apple Inc", "weight": pytest.approx(7.7)}
    assert p["sectors"]["technology"] == pytest.approx(38)


def test_normalize_fund_rejects_non_etf_and_falls_back_for_expense():
    assert normalize_fund({"info": {"quoteType": "EQUITY"}}) is None
    assert normalize_fund(None) is None
    no_ops = {**QQQM_RAW, "ops": {}}
    assert normalize_fund(no_ops)["expense_ratio"] == 0.15


def test_provider_swallows_errors():
    def boom(sym):
        raise ConnectionError("x")
    assert YahooFunds(fetcher=boom).get_profile("VOO") is None


# --- overlap ---------------------------------------------------------------------
def test_holdings_and_sector_overlap():
    a, b = normalize_fund(VOO_RAW), normalize_fund(QQQM_RAW)
    total, ca, cb, shared = etf.holdings_overlap(a["top_holdings"], b["top_holdings"])
    assert total == pytest.approx(7.7 + 7.58)               # min weights of AAPL and NVDA
    assert [s for s, _, _ in shared] == ["AAPL", "NVDA"]
    assert ca == pytest.approx(22.71) and cb == pytest.approx(20.64)
    assert etf.sector_overlap(a["sectors"], b["sectors"]) == pytest.approx(38 + 5)
    assert etf.sector_overlap({}, b["sectors"]) is None


def test_correlation_and_labels():
    rng = np.random.default_rng(1)
    base = rng.normal(0, 0.01, 300)
    a = series(100 * np.cumprod(1 + base))
    twin = series(100 * np.cumprod(1 + base + rng.normal(0, 0.001, 300)))
    other = series(100 * np.cumprod(1 + rng.normal(0, 0.01, 300)))
    c, n = etf.return_correlation(a, twin)
    assert c > 0.98 and n == 252
    assert etf.return_correlation(a, other)[0] < 0.3
    assert etf.return_correlation(a.tail(30), twin.tail(30)) == (None, 29)

    ov = etf.Overlap(30, 37, 47, sector_pct=90, correlation=0.97)
    assert "little diversification" in etf.similarity_label(ov)
    ov.correlation = 0.2
    assert "diversify" in etf.similarity_label(ov)


def test_fees():
    assert etf.annual_fee(0.03, 100_000_000) == pytest.approx(30_000)
    drag = etf.fee_drag(0.15, 100_000_000)
    assert 2_500_000 < drag < 3_000_000
    assert etf.fee_drag(None, 1) is None


def test_profile_cache(db):
    funds = FakeFunds({"VOO": VOO_RAW})
    etf.get_profile(db, "VOO", provider=funds, today=date(2026, 11, 20))
    etf.get_profile(db, "voo", provider=funds, today=date(2026, 11, 25))
    assert funds.calls == 1                                   # fresh for 7 days
    etf.get_profile(db, "VOO", provider=funds, today=date(2026, 11, 28))
    assert funds.calls == 2
    broken = FakeFunds({})
    stale = etf.get_profile(db, "VOO", provider=broken, today=date(2027, 1, 1))
    assert stale["as_of"] == "2026-11-28"                     # falls back to the stored copy


# --- currency returns ------------------------------------------------------------
def test_currency_return_usd_asset():
    close = series(np.linspace(100, 110, 300))               # +10% in USD
    fx = series(np.linspace(16000, 17600, 500), start="2024-12-01", freq="D")
    r = currency_return("VOO", "USD", close, fx, "1y")
    assert r.usd == r.local
    assert r.idr == pytest.approx(((1 + r.local / 100) * (1 + r.fx / 100) - 1) * 100)
    assert r.fx > 0


def test_currency_return_idr_asset_loses_in_usd_when_rupiah_weakens():
    close = series(np.full(300, 5000.0))                      # flat in IDR
    fx = series(np.linspace(16000, 18000, 500), start="2024-12-01", freq="D")
    r = currency_return("BBCA", "IDR", close, fx, "6m")
    assert r.idr == 0 and r.usd < 0


def test_currency_return_needs_history():
    close = series(np.full(30, 1.0))
    fx = series(np.full(30, 1.0))
    assert currency_return("X", "USD", close, fx, "1y") is None
    assert currency_return("X", "USD", close, pd.Series(dtype=float), "1m") is None


def test_ytd_start():
    assert period_start(pd.Timestamp("2026-11-24"), "ytd") == pd.Timestamp("2025-12-31")


# --- commands ---------------------------------------------------------------------
def test_etf_and_overlap_commands(db):
    funds = FakeFunds({"VOO": VOO_RAW, "QQQM": QQQM_RAW})
    out = cmd.cmd_etf(db, ["voo"], funds_provider=funds)
    assert "Vanguard S&amp;P 500 ETF" in out and "Expense 0.03%/yr: Rp 30 rb" in out
    assert "Technology 38%" in out and "AAPL" in out
    assert "No ETF data" in cmd.cmd_etf(db, ["BBCA"], funds_provider=funds)

    rng = np.random.default_rng(2)
    base = rng.normal(0, 0.01, 300)
    prices = FakePrices({"VOO": series(100 * np.cumprod(1 + base)),
                         "QQQM": series(100 * np.cumprod(1 + base * 1.2))})
    out = cmd.cmd_overlap(db, ["VOO", "QQQM"], funds_provider=funds, provider=prices)
    assert "Return correlation" in out and "<b>1.00</b>" in out
    assert "Top-holdings overlap: <b>15.3%</b>" in out
    assert "highly similar" in out          # correlated, but sectors only 43% alike
    assert "Usage" in cmd.cmd_overlap(db, ["VOO"])


def test_compare_command_aligns_markets(db):
    idx = pd.bdate_range("2025-01-01", periods=300)
    db.upsert_price_bars("BBCA", pd.DataFrame({"Open": 1.0, "High": 1.0, "Low": 1.0,
                                              "Close": np.full(300, 5000.0), "Volume": 0.0}, index=idx))
    db.upsert_price_bars("VOO", pd.DataFrame({"Open": 1.0, "High": 1.0, "Low": 1.0,
                                             "Close": np.linspace(500, 550, 299), "Volume": 0.0},
                                            index=idx[:-1]))  # US one day behind
    db.upsert_fx("USDIDR", series(np.linspace(16000, 17600, 450), start="2024-12-01", freq="D"))
    out = cmd.cmd_compare(db, ["BBCA", "VOO", "6m"], provider=FakePrices({}), now=NOW_AFTER)
    assert f"to {idx[-2]:%d %b %Y}" in out                   # common end date
    assert "rupiah weakened" in out and "VOO in IDR" in out
    rows = out.split("<pre>")[1].split("</pre>")[0].splitlines()
    assert rows[1].startswith("BBCA") and "+0.0" in rows[1]
    assert "isn't on your watchlist" in cmd.cmd_compare(db, ["NOPE", "VOO"], provider=FakePrices({}),
                                                        now=NOW_AFTER)
    assert "Usage" in cmd.cmd_compare(db, ["1y"])


# --- price top-up ------------------------------------------------------------------
from datetime import datetime, timezone

from app.jobs.update_prices import last_closed_session, top_up
from app.symbols import Instrument

NOW_AFTER = datetime(2025, 3, 1, tzinfo=timezone.utc)  # long after the test bars end


def test_last_closed_session():
    # Wed 25 Nov 2026, 14:00 New York = US session still open -> Tue 24 Nov
    wed_open = datetime(2026, 11, 25, 19, 0, tzinfo=timezone.utc)
    assert last_closed_session("US", wed_open) == date(2026, 11, 24)
    # same instant is 02:00 Thu in Jakarta -> IDX's last finished session is Wed 25
    assert last_closed_session("IDX", wed_open) == date(2026, 11, 25)
    # Monday morning in New York -> previous Friday
    assert last_closed_session("US", datetime(2026, 11, 30, 14, 0, tzinfo=timezone.utc)) == date(2026, 11, 27)


def test_top_up_fetches_stale_and_drops_unfinished_session(db):
    days = pd.bdate_range("2026-11-16", "2026-11-25")       # Mon 16 .. Wed 25
    c = pd.Series(np.arange(len(days), dtype=float) + 100, index=days)
    db.upsert_price_bars("VOO", pd.DataFrame({"Open": c, "High": c, "Low": c, "Close": c, "Volume": 0.0})
                         .loc[:"2026-11-23"])               # stored only to Mon 23
    prices = FakePrices({"VOO": c})
    wed_open = datetime(2026, 11, 25, 19, 0, tzinfo=timezone.utc)
    voo = Instrument("VOO", "US", "ETF", "USD")
    assert top_up(db, [voo], prices, wed_open) == ["VOO"]
    assert db.get_price_history("VOO").index[-1] == pd.Timestamp("2026-11-24")  # 25th is intraday
    prices.asked = None
    assert top_up(db, [voo], prices, wed_open) == [] and prices.asked is None   # now fresh: no fetch
