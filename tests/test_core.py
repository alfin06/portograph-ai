from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from app.symbols import to_yahoo, from_yahoo, Instrument
from app.providers.base import normalize_ohlcv
from app.providers.yahoo import YahooProvider
from app.jobs import update_prices
from data_pipeline.indicators import TechnicalIndicators
from modules.crypto.price_watcher import check_trade


def make_bars(n=60, start=100.0, step=1.0, tz=None):
    idx = pd.date_range("2026-01-01", periods=n, freq="B", tz=tz)
    close = start + np.arange(n) * step
    return pd.DataFrame({"Open": close, "High": close + 1, "Low": close - 1,
                         "Close": close, "Volume": 1000.0}, index=idx)


# --- symbols ---------------------------------------------------------------
def test_symbol_mapping():
    assert to_yahoo("bbca", "IDX") == "BBCA.JK"
    assert to_yahoo("BBCA.JK", "IDX") == "BBCA.JK"
    assert to_yahoo("BRK.B", "US") == "BRK-B"
    assert from_yahoo("BBCA.JK") == "BBCA"
    with pytest.raises(ValueError):
        to_yahoo("X", "HK")


# --- provider ----------------------------------------------------------------
def test_normalize_strips_timezone_and_duplicates():
    df = make_bars(5, tz="Asia/Jakarta")
    df = pd.concat([df, df.tail(1)])
    out = normalize_ohlcv(df)
    assert out.index.tz is None
    assert len(out) == 5


def test_yahoo_provider_parses_multiindex_without_network():
    bbca, voo = make_bars(30, 9000), make_bars(30, 500)

    def fake_download(tickers, **kw):
        frames = {"BBCA.JK": bbca, "VOO": voo}
        return pd.concat({t: frames[t] for t in tickers if t in frames}, axis=1)

    p = YahooProvider(downloader=fake_download)
    insts = [Instrument("BBCA", "IDX", "STOCK", "IDR"), Instrument("VOO", "US", "ETF", "USD"),
             Instrument("NOPE", "US", "STOCK", "USD")]
    out = p.get_history(insts)
    assert set(out) == {"BBCA", "VOO"}
    assert out["BBCA"]["Close"].iloc[0] == 9000


def test_yahoo_provider_survives_download_error():
    def boom(**kw):
        raise ConnectionError("blocked")
    assert YahooProvider(downloader=boom).get_history([Instrument("VOO", "US", "ETF", "USD")]) == {}


# --- indicators -------------------------------------------------------------
def test_rsi_is_100_when_price_only_rises():
    df = TechnicalIndicators.add_all_indicators(make_bars(60, step=1.0))
    assert df["RSI"].iloc[-1] == 100


def test_rsi_low_when_price_only_falls():
    df = TechnicalIndicators.add_all_indicators(make_bars(60, start=200, step=-1.0))
    assert df["RSI"].iloc[-1] < 5


# --- database ---------------------------------------------------------------
def test_seed_watchlist(db):
    symbols = {r["symbol"] for r in db.get_instruments()}
    assert {"BBCA", "VOO", "QQQM"} <= symbols
    assert all(r["market"] == "US" for r in db.get_instruments(market="US"))


def test_price_upsert_is_idempotent(db):
    bars = make_bars(10)
    db.upsert_price_bars("BBCA", bars)
    db.upsert_price_bars("BBCA", bars)
    hist = db.get_price_history("BBCA")
    assert len(hist) == 10
    assert list(hist.columns) == ["Open", "High", "Low", "Close", "Volume"]


def test_update_prices_job_with_fake_provider(db):
    class Fake:
        def get_history(self, instruments, period):
            return {i.symbol: make_bars(5) for i in instruments if i.market == "US"}
        def get_fx(self, pair, period):
            return make_bars(5, start=16000)["Close"]
    s = update_prices.run(provider=Fake(), db=db)
    assert set(s["updated"]) == {"VOO", "QQQM"}
    assert "BBCA" in s["missing"]
    assert db.get_latest_fx("USDIDR") == 16004


# --- alerts -----------------------------------------------------------------
def test_alert_cooldown(db):
    t0 = datetime(2026, 11, 23, 9, 0)
    key = "trade:1:STOP_LOSS"
    assert db.should_fire_alert(key, 12, now=t0)
    db.mark_alert_fired(key, 100, now=t0)
    assert not db.should_fire_alert(key, 12, now=t0 + timedelta(hours=2))
    assert db.should_fire_alert(key, 12, now=t0 + timedelta(hours=12))


def test_check_trade():
    trade = {"stop_loss": 90, "target_price": 110}
    assert check_trade(trade, 89) == "STOP_LOSS"
    assert check_trade(trade, 111) == "TAKE_PROFIT"
    assert check_trade(trade, 100) is None
