"""Portfolio against IHSG, the S&P 500 and a blend: period returns of today's holdings,
the time-weighted real record, the Telegram command, the dashboard card and the weekly
digest line. No network."""
from datetime import date

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import commands as c
from app.analytics import benchmark as bm
from app.analytics.portfolio import Position
from app.jobs.digest import weekly_bench
from app.reports.digest import format_bench, format_digest
from app.web.server import create_app


def series(values, start="2025-01-01", freq="B"):
    return pd.Series([float(v) for v in values], index=pd.date_range(start, periods=len(values), freq=freq))


def pos(sym, shares, market="IDX", ccy="IDR"):
    return Position(sym, market, ccy, "STOCK", shares=shares)


# --- the maths ---------------------------------------------------------------------------
def test_blend_rebalances_daily():
    i = series([100, 110, 99])        # +10%, -10%
    s = series([100, 100, 120])       # 0%, +20%
    b = bm.blend(i, s, 50)
    assert b.iloc[0] == 100
    assert b.iloc[1] == pytest.approx(105)              # half of +10%
    assert b.iloc[2] == pytest.approx(105 * 1.05)       # half of -10% plus half of +20%


def test_mix_converts_usd_and_skips_missing():
    fx = series([16000, 16000, 17000])
    prices = {"BBCA": series([9000, 9000, 9000]), "VOO": series([500, 500, 500])}
    v, missing = bm.mix_value([pos("BBCA", 100), pos("VOO", 2, "US", "USD"), pos("XXXX", 5)], prices, fx)
    assert missing == ["XXXX"]
    assert v.iloc[0] == pytest.approx(100 * 9000 + 2 * 500 * 16000)
    assert v.iloc[-1] == pytest.approx(100 * 9000 + 2 * 500 * 17000)   # rupiah weaker: US part worth more


def test_period_rows_need_history_at_the_start():
    days = pd.date_range("2026-06-01", "2026-11-25", freq="B")
    df = pd.DataFrame({"mine": range(100, 100 + len(days)), "ihsg": 100.0}, index=days, dtype=float)
    rows = bm.period_rows(df)
    assert [r.period for r in rows] == ["1m", "3m"]      # no 6m, YTD or 1y: history starts in June
    one = rows[0]
    assert one.bench["ihsg"] == 0 and one.mine > 0 and one.vs_blend is None


def test_track_record_ignores_new_money():
    def h(d, value, cost, realised=0.0, income=0.0):
        return {"date": d, "value_idr": value, "cost_idr": cost, "realised_idr": realised, "income_idr": income}
    hist = [
        h("2026-11-02", 100.0, 100.0),
        h("2026-11-03", 110.0, 100.0),          # +10%
        h("2026-11-04", 220.0, 210.0),          # bought 110 more, no gain that day
        h("2026-11-05", 198.0, 210.0),          # -10%
    ]
    bench = pd.DataFrame({"ihsg": [100.0, 101.0, 102.0, 105.0]},
                         index=pd.to_datetime(["2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05"]))
    t = bm.track_record(hist, bench)
    assert t.mine == pytest.approx((1.1 * 1.0 * 0.9 - 1) * 100)
    assert t.bench["ihsg"] == pytest.approx(5.0)
    assert t.days == 4
    assert bm.track_record(hist[:1], bench) is None


def test_compute_without_one_benchmark_has_no_blend():
    days = pd.date_range("2025-06-02", "2026-11-25", freq="B")
    flat = pd.Series(100.0, index=days)
    out = bm.compute([pos("BBCA", 100)], {"BBCA": flat, "^JKSE": flat}, flat * 160, [], 100.0)
    assert "SPY" in out.missing
    assert out.rows and "blend" not in out.rows[0].bench
    assert set(out.growth) == {"Your mix", "IHSG"}


# --- with the database -------------------------------------------------------------------
def year(values_from, values_to, end="2026-11-25"):
    days = pd.bdate_range(end=end, periods=300)
    closes = [values_from + (values_to - values_from) * i / 299 for i in range(300)]
    return pd.DataFrame({"Open": closes, "High": closes, "Low": closes, "Close": closes,
                         "Volume": [1.0] * 300}, index=days)


@pytest.fixture
def bdb(db):
    """BBCA up 10% and VOO flat over the stored year; IHSG down 20%, SPY up 20%; flat USD/IDR."""
    db.upsert_price_bars("BBCA", year(9000, 9900))
    db.upsert_price_bars("VOO", year(500, 500))
    db.upsert_price_bars("^JKSE", year(7000, 5600))
    db.upsert_price_bars("SPY", year(500, 600))
    db.upsert_fx("USDIDR", pd.Series(16000.0, index=pd.bdate_range(end="2026-11-25", periods=300)))
    c.record_transaction(db, "BUY", "BBCA", "10", "9900", "2026-11-25", "0", today=date(2026, 11, 25))
    c.record_transaction(db, "BUY", "VOO", "1", "500", "2026-11-25", "0", today=date(2026, 11, 25))
    return db


def test_from_db_compares_the_current_mix(bdb):
    out = bm.from_db(bdb)
    # 1,000 BBCA = 9.9 jt, 1 VOO = 8 jt: IDX is 55%
    assert out.idx_weight == pytest.approx(9.9 / 17.9 * 100, abs=0.2)
    assert out.end == pd.Timestamp("2026-11-25")
    one_y = {r.period: r for r in out.rows}["1y"]
    assert one_y.bench["sp500"] > 0 > one_y.bench["ihsg"]
    assert out.track is None                                  # no stored portfolio days yet


def test_benchmark_command(bdb):
    text = c.cmd_benchmark(bdb)
    assert "Portfolio vs market" in text and "1y" in text and "Blend = 55% IHSG" in text
    assert "starts once the digests" in text


def test_benchmark_command_without_holdings(db):
    assert "No holdings" in c.cmd_benchmark(db)


def test_portfolio_page_shows_the_comparison(bdb):
    r = TestClient(create_app(db=bdb, extra_hosts=["testserver"])).get("/portfolio")
    assert r.status_code == 200
    assert "Against the market" in r.text and 'id="bench-data"' in r.text and "Mix vs blend" in r.text


def test_weekly_line_only_in_fridays_us_digest(bdb):
    assert weekly_bench("US", date(2026, 11, 27))
    assert not weekly_bench("US", date(2026, 11, 26))
    assert not weekly_bench("IDX", date(2026, 11, 27))
    out = bm.from_db(bdb)
    line = format_bench(out)
    assert "Month: you" in line and "Year: you" in line and "/benchmark" in line
    from app.analytics.snapshot import build_snapshot
    from tests.test_digest import INST, bars
    s = build_snapshot(INST, bars([9000.0] * 70 + [9500.0]))
    assert "vs market" in format_digest("US", [s], s.date, bench=out)
    assert "vs market" not in format_digest("US", [s], s.date)
