from datetime import date

import numpy as np
import pandas as pd
import pytest

from app import commands as cmd
from app.analytics.screener import RuleError, parse_rule, run_screens
from app.analytics.snapshot import market_snapshots
from app.jobs import digest, update_fundamentals
from app.providers.yahoo_fundamentals import YahooFundamentals, normalize_info
from app.symbols import Instrument

USDIDR = 18000.0


def bars(closes, start="2026-01-01"):
    idx = pd.bdate_range(start, periods=len(closes))
    c = np.asarray(closes, dtype=float)
    return pd.DataFrame({"Open": c, "High": c + 1, "Low": c - 1, "Close": c, "Volume": 1000.0}, index=idx)


# Shapes copied from real yfinance output (Nov 2026)
ITMG_INFO = {"quoteType": "EQUITY", "currency": "IDR", "financialCurrency": "USD",
             "marketCap": 26_352_365_338_624, "trailingPE": 7.15, "priceToBook": 13887.3,
             "returnOnEquity": 0.1195, "debtToEquity": 6.527, "dividendYield": 7.08,
             "profitMargins": 0.10566, "revenueGrowth": 0.336, "earningsGrowth": 0.494,
             "bookValue": 1.775, "currentPrice": 24650.0, "sector": "Energy"}
BBCA_INFO = {"quoteType": "EQUITY", "currency": "IDR", "financialCurrency": "IDR",
             "marketCap": 7.2e14, "trailingPE": 12.06, "priceToBook": 2.52, "returnOnEquity": 0.212,
             "debtToEquity": None, "dividendYield": 6.57, "profitMargins": 0.533,
             "currentPrice": 5825.0, "bookValue": 2307.0}


class FakeFundamentals:
    name = "fake"

    def __init__(self, by_symbol):
        self.by_symbol = by_symbol
        self.calls = []

    def get_fundamentals(self, instruments, usdidr):
        self.calls.append([i.symbol for i in instruments])
        return {i.symbol: self.by_symbol[i.symbol] for i in instruments if i.symbol in self.by_symbol}


class FakePrices:
    def __init__(self, frames):
        self.frames = frames

    def get_history(self, instruments, period):
        return {i.symbol: self.frames[i.symbol] for i in instruments if i.symbol in self.frames}

    def get_fx(self, pair, period):
        return bars([USDIDR, USDIDR])["Close"]


# --- provider normalization ------------------------------------------------------
def test_normalize_units():
    f = normalize_info(BBCA_INFO, "IDR", USDIDR)
    assert f["roe"] == pytest.approx(21.2)
    assert f["net_margin"] == pytest.approx(53.3)
    assert f["div_yield"] == 6.57          # already percent
    assert f["pb"] == 2.52                 # same currency: Yahoo's value is kept
    assert f["der"] is None
    assert f["market_cap_usd"] == pytest.approx(7.2e14 / USDIDR)


def test_normalize_fixes_pb_when_reporting_currency_differs():
    f = normalize_info(ITMG_INFO, "IDR", USDIDR)
    assert f["pb"] == pytest.approx(24650 / (1.775 * USDIDR))  # ~0.77, not 13,887
    assert f["der"] == pytest.approx(0.06527)
    assert normalize_info(ITMG_INFO, "IDR", None)["pb"] is None  # no FX -> unknown, not wrong


def test_provider_skips_failing_symbols():
    def fetch(sym):
        if sym == "BAD.JK":
            raise ConnectionError("x")
        return {} if sym == "EMPTY.JK" else BBCA_INFO
    out = YahooFundamentals(info_fetcher=fetch).get_fundamentals(
        [Instrument(s, "IDX", "STOCK", "IDR") for s in ("BBCA", "BAD", "EMPTY")], USDIDR)
    assert list(out) == ["BBCA"]


# --- rule engine -------------------------------------------------------------------
@pytest.mark.parametrize("rule", [
    "__import__('os').system('x')", "pe.real > 1", "pe < 'a'", "nope > 1", "pe", "pe + 1 > 2",
    "not pe > 1", "pe in [1]", "", "pe <",
])
def test_rule_rejects_anything_but_comparisons(rule):
    with pytest.raises(RuleError):
        parse_rule(rule)


def test_rule_evaluation():
    m = {"pe": 10.0, "pb": 1.5, "roe": 20.0, "der": None, "chg_1m": -12.0}
    assert parse_rule("PE < 12 AND roe > 15").matches(m)
    assert parse_rule("5 < pe < 15").matches(m)
    assert parse_rule("chg_1m < -10").matches(m)
    assert not parse_rule("der < 1").matches(m)            # missing value -> no match
    assert parse_rule("der < 1 or pb < 2").matches(m)
    assert not parse_rule("pe < 12 and (pb > 2 or roe > 30)").matches(m)


# --- screens + hits ----------------------------------------------------------------
def _seed_idx(db):
    db.upsert_price_bars("BBCA", bars([5000.0] * 60))
    db.upsert_price_bars("ITMG", bars([24000.0] * 60))
    db.upsert_fundamentals("BBCA", "2026-11-20", "fake", normalize_info(BBCA_INFO, "IDR", USDIDR))
    db.upsert_fundamentals("ITMG", "2026-11-20", "fake", normalize_info(ITMG_INFO, "IDR", USDIDR))


def test_screen_hits_new_then_known_then_reentry(db):
    _seed_idx(db)
    db.save_screen("cheap", "pb < 1", "IDX")
    snaps = market_snapshots(db, "IDX")
    first = {r.name: r for r in run_screens(db, "IDX", snaps, "2026-11-23")}
    assert first["cheap"].hits == ["ITMG"] and first["cheap"].new == ["ITMG"]
    second = {r.name: r for r in run_screens(db, "IDX", snaps, "2026-11-24")}
    assert second["cheap"].hits == ["ITMG"] and second["cheap"].new == []

    db.save_screen("cheap", "pb < 0.5", "IDX")             # changed rule: ITMG drops out
    assert {r.name: r for r in run_screens(db, "IDX", snaps, "2026-11-25")}["cheap"].hits == []
    db.save_screen("cheap", "pb < 1", "IDX")               # back in -> reported as new again
    assert {r.name: r for r in run_screens(db, "IDX", snaps, "2026-11-26")}["cheap"].new == ["ITMG"]


def test_global_screen_hits_are_tracked_per_market(db):
    _seed_idx(db)
    db.upsert_price_bars("VOO", bars([500.0] * 60))
    db.save_screen("any", "close > 0")
    run_screens(db, "IDX", market_snapshots(db, "IDX"), "2026-11-23")
    us = {r.name: r for r in run_screens(db, "US", market_snapshots(db, "US"), "2026-11-23")}
    assert us["any"].new == ["VOO"]
    # the US run must not have removed the IDX hits
    again = {r.name: r for r in run_screens(db, "IDX", market_snapshots(db, "IDX"), "2026-11-24")}
    assert again["any"].new == []


def test_seeded_screens_parse(db):
    for s in db.get_screens(active_only=False):
        parse_rule(s["rule"])


def test_digest_shows_screen_section_with_new_marker(db):
    db.save_screen("cheap", "pb < 1", "IDX")
    frames = {"BBCA": bars([5000.0] * 60), "ITMG": bars([24000.0] * 60)}
    fund = FakeFundamentals({"ITMG": normalize_info(ITMG_INFO, "IDR", USDIDR)})

    class Rec:
        sent = []
        def send(self, text, parse_mode="HTML"):
            self.sent.append(text)
    rec = Rec()
    digest.run("IDX", rec, db=db, provider=FakePrices(frames), fund_provider=fund)
    assert "<b>Screens</b>" in rec.sent[0]
    assert "• cheap: <b>ITMG</b> 🆕" in rec.sent[0]


# --- fundamentals job ---------------------------------------------------------------
def test_update_fundamentals_only_fetches_stale(db):
    fresh = normalize_info(BBCA_INFO, "IDR", USDIDR)
    db.upsert_fundamentals("BBCA", "2026-11-24", "fake", fresh)
    db.upsert_fundamentals("BBRI", "2026-11-10", "fake", fresh)
    fake = FakeFundamentals({"BBRI": fresh})
    s = update_fundamentals.run(db=db, provider=fake, market="IDX", today=date(2026, 11, 25))
    assert "BBCA" not in fake.calls[0] and "BBRI" in fake.calls[0]
    assert s["updated"] == ["BBRI"]
    assert db.get_latest_fundamentals("BBRI")["as_of"] == "2026-11-25"


# --- commands -----------------------------------------------------------------------
def test_watch_validates_symbol_before_adding(db):
    out = cmd.cmd_watch(db, ["XXXX", "idx"], provider=FakePrices({}), fund_provider=FakeFundamentals({}))
    assert "No price data" in out and db.get_instrument("XXXX") is None
    out = cmd.cmd_watch(db, ["bbni", "idx", "hold"], provider=FakePrices({"BBNI": bars([4000.0] * 30)}),
                        fund_provider=FakeFundamentals({}))
    assert "Added <b>BBNI</b>" in out
    inst = db.get_instrument("BBNI")
    assert inst["is_holding"] == 1 and inst["currency"] == "IDR"
    assert "Usage" in cmd.cmd_watch(db, ["BBNI"])
    assert "Usage" in cmd.cmd_watch(db, ["BBNI", "idx", "bogus"])


def test_unwatch(db):
    assert "Stopped" in cmd.cmd_unwatch(db, ["bbca"])
    assert all(r["symbol"] != "BBCA" for r in db.get_instruments())
    assert "isn't on your watchlist" in cmd.cmd_fund(db, ["BBCA"])


def test_fund_output(db):
    _seed_idx(db)
    out = cmd.cmd_fund(db, ["itmg"], fund_provider=FakeFundamentals({}))
    assert "<b>ITMG</b> · IDX stock · holding" in out
    assert "P/B 0.77" in out and "ROE 11.9%" in out and "D/E 0.07x" in out
    assert "Energy" in out


def test_addscreen_screen_delscreen(db):
    _seed_idx(db)
    assert "❌" in cmd.cmd_addscreen(db, ["bad", "idx", "roe", ">>", "1"])
    assert "Screen names" in cmd.cmd_addscreen(db, ["Bad-Name", "pe", "<", "1"])
    assert "Saved <b>cheap</b> [IDX]" in cmd.cmd_addscreen(db, ["cheap", "idx", "pb", "<", "1"])
    out = cmd.cmd_screen(db, ["cheap"])
    assert "1 of 2 watched symbols match" in out and "ITMG" in out
    adhoc = cmd.cmd_screen(db, ["roe", ">", "20"])
    assert "BBCA" in adhoc and "ITMG" not in adhoc.split("<pre>")[1]
    assert "No saved screen" in cmd.cmd_screen(db, ["nothing"])
    assert "cheap" in cmd.cmd_screens(db)
    assert "Deleted" in cmd.cmd_delscreen(db, ["cheap"])
    assert "No screen" in cmd.cmd_delscreen(db, ["cheap"])
