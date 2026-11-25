from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from app.analytics.snapshot import build_snapshot, market_snapshots
from app.jobs import digest
from app.notify.telegram import TelegramNotifier, split_message
from app.reports.digest import format_digest
from app.worker import Worker, MAX_RETRIES


def bars(closes, start="2026-01-01", volume=None):
    idx = pd.bdate_range(start, periods=len(closes))
    c = np.asarray(closes, dtype=float)
    v = np.full(len(c), 1000.0) if volume is None else np.asarray(volume, dtype=float)
    return pd.DataFrame({"Open": c, "High": c + 1, "Low": c - 1, "Close": c, "Volume": v}, index=idx)


INST = {"symbol": "BBCA", "market": "IDX", "currency": "IDR", "is_holding": 1}


class Recorder:
    def __init__(self):
        self.sent = []

    def send(self, text, parse_mode="HTML"):
        self.sent.append(text)


class NoFundamentals:
    name = "fake"

    def get_fundamentals(self, instruments, usdidr):
        return {}


class FakeProvider:
    """Returns the same frame for every US instrument; IDX gets nothing."""
    def __init__(self, frame):
        self.frame = frame
        self.periods = []

    def get_history(self, instruments, period):
        self.periods.append(period)
        return {i.symbol: self.frame for i in instruments if i.market == "US"}

    def get_fx(self, pair, period):
        return bars([16000, 16160])["Close"]


# --- snapshot ---------------------------------------------------------------
def test_snapshot_returns_and_big_move():
    closes = [100.0] * 70 + [106.0]
    s = build_snapshot(INST, bars(closes))
    assert s.close == 106
    assert s.chg_1d == pytest.approx(6.0)
    assert s.chg_1m == pytest.approx(6.0)
    assert "up 6.0% today" in s.signals
    assert "new 52-week high" in s.signals


def test_snapshot_volume_surge_and_oversold():
    closes = list(np.linspace(200, 100, 80))
    vol = [1000.0] * 79 + [5000.0]
    s = build_snapshot(INST, bars(closes, volume=vol))
    assert any("oversold" in x for x in s.signals)
    assert any("volume 5.0×" in x for x in s.signals)
    assert "new 52-week low" in s.signals


def test_snapshot_short_history_has_no_52w_or_rsi_signals():
    s = build_snapshot(INST, bars([100, 101, 102]))
    assert s.chg_1m is None
    assert s.signals == []


def test_snapshot_empty():
    assert build_snapshot(INST, pd.DataFrame()) is None


# --- formatting -------------------------------------------------------------
def test_format_digest_contains_table_signals_and_fx():
    s = build_snapshot(INST, bars([9000.0] * 70 + [9500.0]))
    text = format_digest("IDX", [s], s.date, fx=(16160.0, 1.0))
    assert "IDX close" in text and "<pre>" in text
    assert "•BBCA" in text and "9,500" in text
    assert "<b>BBCA</b> up 5.6% today" in text
    assert "USD/IDR 16,160 (+1.0%)" in text
    assert "not investment advice" in text


def test_format_marks_stale_symbols():
    fresh = build_snapshot(INST, bars([100.0] * 30))
    old = build_snapshot({**INST, "symbol": "ULTJ"}, bars([100.0] * 29))
    text = format_digest("IDX", [fresh, old], fresh.date)
    assert "ULTJ†" in text and "no bar for this session" in text


def test_format_escapes_html():
    s = build_snapshot({**INST, "symbol": "A<B"}, bars([1.0] * 30))
    assert "A&lt;B" in format_digest("IDX", [s], s.date)


# --- telegram ---------------------------------------------------------------
def test_split_message_respects_limit_and_paragraphs():
    text = "\n\n".join(["x" * 30] * 10)
    chunks = split_message(text, limit=100)
    assert all(len(c) <= 100 for c in chunks)
    assert "\n\n".join(chunks) == text
    long_para = "\n".join(["y" * 40] * 5)
    assert all(len(c) <= 100 for c in split_message(long_para, limit=100))


def test_notifier_posts_and_hides_token_on_error():
    import requests

    class Session:
        def __init__(self, fail=False):
            self.calls, self.fail = [], fail

        def post(self, url, json, timeout):
            if self.fail:
                raise requests.ConnectionError(f"Max retries exceeded with url: {url}")
            self.calls.append(json)
            return type("R", (), {"status_code": 200, "text": "ok"})()

    ok = Session()
    TelegramNotifier("SECRET", "42", session=ok).send("hi")
    assert ok.calls == [{"chat_id": "42", "text": "hi", "disable_web_page_preview": True,
                         "parse_mode": "HTML"}]

    with pytest.raises(RuntimeError) as e:
        TelegramNotifier("SECRET", "42", session=Session(fail=True)).send("hi")
    assert "SECRET" not in str(e.value)


def test_notifier_requires_chat_id(monkeypatch):
    monkeypatch.setattr("core.config.TELEGRAM_CHAT_ID", "")
    with pytest.raises(RuntimeError):
        TelegramNotifier(token="t", chat_id="")


# --- digest job -------------------------------------------------------------
def test_digest_sends_for_available_session(db):
    frame = bars([500.0] * 60 + [510.0])
    provider, rec = FakeProvider(frame), Recorder()
    res = digest.run("US", rec, db=db, provider=provider, session_date=frame.index[-1].date(),
                     fund_provider=NoFundamentals())
    assert res.status == "sent" and len(rec.sent) == 1
    assert "VOO" in rec.sent[0] and "QQQM" in rec.sent[0]
    assert provider.periods[0] == "2y"  # empty DB -> full backfill (later calls: sentiment inputs)
    assert len(market_snapshots(db, "US")) == 2


def test_digest_skips_when_no_bar_for_session(db):
    frame = bars([500.0] * 30)
    rec = Recorder()
    res = digest.run("US", rec, db=db, provider=FakeProvider(frame), session_date=date(2030, 1, 1),
                     fund_provider=NoFundamentals())
    assert res.status == "no_session_data" and rec.sent == []


def test_digest_uses_short_refresh_once_history_exists(db):
    frame = bars([500.0] * 300)
    db.upsert_price_bars("VOO", frame)
    db.upsert_price_bars("QQQM", frame)
    provider = FakeProvider(frame)
    digest.run("US", Recorder(), db=db, provider=provider, fund_provider=NoFundamentals())
    assert provider.periods[0] == "1mo"


def test_market_today_uses_exchange_timezone():
    from zoneinfo import ZoneInfo
    # 03:00 WIB Wednesday is still Tuesday afternoon in New York
    now = datetime(2026, 11, 25, 3, 0, tzinfo=ZoneInfo("Asia/Jakarta"))
    assert digest.market_today("US", now) == date(2026, 11, 24)
    assert digest.market_today("IDX", now) == date(2026, 11, 25)


# --- worker -----------------------------------------------------------------
class FakeScheduler:
    def __init__(self):
        self.jobs = []

    def add_job(self, func, trigger, **kw):
        self.jobs.append((trigger, kw))


def test_worker_schedules_both_markets_in_exchange_time():
    sched = FakeScheduler()
    Worker(scheduler=sched).schedule()
    by_id = {kw["id"]: trig for trig, kw in sched.jobs}
    assert set(by_id) == {"digest_IDX", "digest_US"}
    assert str(by_id["digest_IDX"].timezone) == "Asia/Jakarta"
    assert str(by_id["digest_US"].timezone) == "America/New_York"


def test_worker_retries_then_gives_up(monkeypatch):
    sched, rec = FakeScheduler(), Recorder()
    w = Worker(scheduler=sched, notifier=rec)
    monkeypatch.setattr(digest, "run", lambda *a, **k: digest.DigestResult("no_session_data"))
    w.run_digest("IDX", date(2026, 11, 25))
    assert len(sched.jobs) == 1 and sched.jobs[0][1]["args"][2] == 1
    assert rec.sent == []
    w.run_digest("IDX", date(2026, 11, 25), attempt=MAX_RETRIES)
    assert len(sched.jobs) == 1  # no further retry
    assert rec.sent == ["ℹ️ IDX: no closing prices for Wed 25 Nov (holiday or data feed late). "
                        "Try /digest idx later."]


def test_worker_reports_failures_to_telegram(monkeypatch):
    rec = Recorder()
    w = Worker(scheduler=FakeScheduler(), notifier=rec)

    def boom(*a, **k):
        raise ValueError("feed broke")
    monkeypatch.setattr(digest, "run", boom)
    w.run_digest("US", date(2026, 11, 24))
    assert rec.sent == ["⚠️ US digest failed: ValueError: feed broke"]
