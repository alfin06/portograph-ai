"""Dividend calendar, the macro calendar file, the digest lines and the Calendar page. No network."""
import json
from datetime import date, datetime, timezone

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app import commands as c
from app.analytics import dividends as dv
from app.analytics import macro_calendar as mc
from app.providers.yahoo_dividends import normalize
from app.web.server import create_app

TODAY = date(2026, 11, 28)
SAME = {"origin": "http://testserver"}


class FakeDivs:
    """Yahoo stand-in: symbol (Yahoo form) -> rows."""
    def __init__(self, data):
        self.data, self.calls = data, []

    def get(self, yahoo_symbol, today):
        self.calls.append(yahoo_symbol)
        if yahoo_symbol not in self.data:
            raise ConnectionError("offline")
        return self.data[yahoo_symbol]


@pytest.fixture
def hdb(db):
    """Holdings bought before the dates below: 10 lots BBCA, 20 lots BBRI, 2 VOO."""
    db.upsert_fx("USDIDR", pd.Series([16000.0], index=pd.to_datetime(["2026-10-01"])))
    c.cmd_buy(db, ["BBCA", "10", "8000", "2026-10-05", "fee=0"], today=TODAY)
    c.cmd_buy(db, ["BBRI", "20", "4000", "2026-10-05", "fee=0"], today=TODAY)
    c.cmd_buy(db, ["VOO", "2", "500", "2026-10-05", "fee=0"], today=TODAY)
    for sym in ("ADRO", "ASII", "BMRI", "ITMG", "ULTJ", "QQQM"):   # seeded as held; not here
        db.set_holding(sym, False)
    return db


# --- provider ---------------------------------------------------------------------------
def test_normalize_keeps_history_and_announced_amount():
    ts = int(datetime(2026, 11, 30, tzinfo=timezone.utc).timestamp())
    raw = {"history": [("2020-01-02", 10.0), ("2025-12-03", 55.0), ("2026-08-31", 25.0)],
           "calendar": {"Ex-Dividend Date": date(2026, 11, 30)},
           "info": {"lastDividendDate": ts, "lastDividendValue": 35.0}}
    rows = normalize(raw, TODAY)
    assert rows == [("2025-12-03", 55.0), ("2026-08-31", 25.0), ("2026-11-30", 35.0)]   # 2020 too old


def test_normalize_announced_date_without_amount():
    raw = {"history": [], "calendar": {"Ex-Dividend Date": date(2026, 12, 15)},
           "info": {"lastDividendDate": 1700000000, "lastDividendValue": 9.0}}
    assert normalize(raw, TODAY) == [("2026-12-15", None)]
    assert normalize({"calendar": {"Ex-Dividend Date": date(2026, 1, 1)}}, TODAY) == []   # past: ignored


def test_moved_announcement_is_replaced(db):
    db.save_dividends("BBCA", [("2025-12-03", 55.0), ("2026-12-01", None)], "yahoo", "2026-11-20")
    db.save_dividends("BBCA", [("2025-12-03", 55.0), ("2026-12-08", 35.0)], "yahoo", "2026-11-28")
    assert [r["ex_date"] for r in db.get_dividends("BBCA")] == ["2025-12-03", "2026-12-08"]
    db.save_dividends("BBCA", [("2026-12-08", None)], "yahoo", "2026-11-29")
    assert db.get_dividends("BBCA", since="2026-12-01")[0]["amount"] == 35.0   # known amount kept


# --- calendar -----------------------------------------------------------------------------
def test_refresh_only_holdings_once_a_day(hdb):
    fake = FakeDivs({"BBCA.JK": [("2025-12-03", 55.0)], "BBRI.JK": [], "VOO": []})
    assert dv.refresh(hdb, provider=fake, today=TODAY) == []
    assert sorted(fake.calls) == ["BBCA.JK", "BBRI.JK", "VOO"]          # ASII etc. aren't held
    dv.refresh(hdb, provider=fake, today=TODAY)
    assert len(fake.calls) == 3                                         # not again today
    assert dv.refresh(hdb, provider=FakeDivs({}), today=date(2026, 11, 29)) == ["BBCA", "BBRI", "VOO"]


def test_announced_expected_and_estimate_replaced(hdb):
    hdb.save_dividends("BBCA", [("2025-12-03", 55.0), ("2026-11-30", 35.0)], "yahoo", "2026-11-28")
    hdb.save_dividends("BBRI", [("2025-12-30", 137.0), ("2026-04-21", 209.0)], "yahoo", "2026-11-28")
    hdb.save_dividends("VOO", [("2025-12-22", 1.771)], "yahoo", "2026-11-28")
    up = dv.calendar(hdb, today=TODAY)["upcoming"]
    got = {(e.symbol, e.status, e.ex_date) for e in up}
    assert ("BBCA", "announced", date(2026, 11, 30)) in got
    assert not any(e.symbol == "BBCA" and e.status == "expected" for e in up)   # announcement wins
    assert ("BBRI", "expected", date(2026, 12, 30)) in got
    bbca = next(e for e in up if e.symbol == "BBCA")
    assert bbca.gross == pytest.approx(35 * 1000) and bbca.net == pytest.approx(35 * 1000 * 0.9)
    assert bbca.cum_date == date(2026, 11, 27)                           # Friday before a Monday ex-date
    voo = next(e for e in up if e.symbol == "VOO")
    assert voo.net == pytest.approx(1.771 * 2 * 0.85)


def test_to_record_needs_shares_before_ex_date_and_clears(hdb):
    hdb.save_dividends("BBCA", [("2026-10-05", 20.0), ("2026-11-10", 25.0)], "yahoo", "2026-11-28")
    rec = dv.calendar(hdb, today=TODAY)["to_record"]
    assert [e.ex_date for e in rec] == [date(2026, 11, 10)]             # bought ON 5 Oct: not entitled
    c.cmd_div(hdb, ["BBCA", "22500", "2026-11-25"], today=TODAY)
    assert dv.calendar(hdb, today=TODAY)["to_record"] == []


def test_yearly_income(hdb):
    hdb.save_dividends("BBRI", [("2025-12-30", 137.0), ("2026-04-21", 209.0), ("2024-04-01", 999.0)],
                       "yahoo", "2026-11-28")
    r = next(x for x in dv.yearly_income(hdb, today=TODAY) if x["symbol"] == "BBRI")
    assert r["per_share"] == pytest.approx(346.0)
    assert r["gross_idr"] == pytest.approx(346 * 2000)
    assert r["yield_on_cost"] == pytest.approx(346 * 2000 / 8_000_000 * 100)


def test_dividends_command(hdb):
    fake = FakeDivs({"BBCA.JK": [("2025-12-03", 55.0), ("2026-11-30", 35.0)], "BBRI.JK": [("2025-12-30", 137.0)],
                     "VOO": []})
    out = c.cmd_dividends(hdb, [], provider=fake, today=TODAY)
    assert "BBCA" in out and "Mon 30 Nov" in out and "own it by the close of Fri 27 Nov" in out
    assert "~30 Dec?" in out and "estimate" in out
    assert "a year" in out


# --- macro calendar ------------------------------------------------------------------------
def test_calendar_file_is_valid():
    raw = json.loads(mc.CAL_PATH.read_text(encoding="utf-8"))
    events, meta = mc.load()
    assert len(events) == len(raw["events"]) > 50                       # nothing dropped as invalid
    assert [e.day for e in events] == sorted(e.day for e in events)
    assert all(e.day.weekday() < 5 for e in events)                     # every date is a weekday
    assert meta["sources"] and meta["notes"]


def test_calendar_loader_is_forgiving(tmp_path):
    p = tmp_path / "cal.json"
    p.write_text(json.dumps({"events": [
        {"date": "2027-01-27", "kind": "fomc", "title": "FOMC", "market": "US"},
        {"date": "nope", "kind": "fomc", "title": "x", "market": "US"},
        {"date": "2027-01-28", "kind": "party", "title": "x", "market": "US"}]}))
    events, _ = mc.load(p)
    assert [e.title for e in events] == ["FOMC"]
    assert mc.load(tmp_path / "missing.json") == ([], {})
    assert mc.needs_update(events, date(2027, 1, 1)) and not mc.needs_update(events, date(2026, 11, 1))
    assert mc.closed(events, "US", date(2027, 1, 27)) is None


def test_calendar_command():
    out = c.cmd_calendar(_NoHoldings(), ["30"], today=TODAY)
    assert "FOMC" in out and "Wed 09 Dec" in out
    assert "Usage" in c.cmd_calendar(_NoHoldings(), ["999"], today=TODAY)


class _NoHoldings:
    """Minimal DB for /calendar when nothing is held."""
    def get_latest_fx(self, pair): return None
    def get_transactions(self, symbol=None): return []
    def get_instruments(self, active_only=True, market=None): return []


# --- digest -------------------------------------------------------------------------------
def test_digest_dividend_lines(hdb):
    from app.jobs.digest import coming_up
    from app.reports.digest import format_coming_up

    hdb.save_dividends("BBCA", [("2026-11-30", 35.0)], "yahoo", "2026-11-28")
    before = format_coming_up(coming_up(hdb, "IDX", date(2026, 11, 27)))
    assert "BBCA" in before and "ex Mon 30 Nov" in before
    on = format_coming_up(coming_up(hdb, "IDX", date(2026, 11, 30)))
    assert "went ex Mon 30 Nov" in on and "/div BBCA 31500" in on
    us = coming_up(hdb, "US", date(2026, 11, 27))
    assert us is None or "BBCA" not in format_coming_up(us)


def test_digest_macro_lines(hdb):
    from app.jobs.digest import coming_up
    from app.reports.digest import format_coming_up
    text = format_coming_up(coming_up(hdb, "US", date(2026, 12, 7)))
    assert "FOMC" in text and "US CPI (Nov)" in text and "BI-Rate" not in text   # 16 Dec is too far


# --- dashboard -------------------------------------------------------------------------------
def test_calendar_page_and_refresh(hdb):
    fake = FakeDivs({"BBCA.JK": [("2026-11-30", 35.0)], "BBRI.JK": [], "VOO": []})
    client = TestClient(create_app(db=hdb, extra_hosts=["testserver"], div_provider=fake))
    assert "Nothing announced" in client.get("/calendar").text
    r = client.post("/calendar/refresh", headers=SAME, follow_redirects=False)
    assert r.status_code == 303 and "refreshed" in r.headers["location"]
    html = client.get("/calendar").text
    assert "BBCA" in html and "announced" in html and "FOMC" in html
    assert client.post("/calendar/refresh").status_code == 403         # cross-site guard still applies
