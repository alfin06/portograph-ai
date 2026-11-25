"""Running on a PC that sleeps: catch-up digests, no double sends, backups, status."""
import sqlite3
from datetime import date, datetime, timezone

import pandas as pd

from app import commands as cmd
from app.backup import backup
from app.jobs import digest, update_prices
from app.worker import Worker, due_session

UTC = timezone.utc


class FakeScheduler:
    def __init__(self):
        self.jobs = []

    def add_job(self, func, trigger, **kw):
        self.jobs.append((trigger, kw))


class Recorder:
    def __init__(self):
        self.sent = []

    def send(self, text, parse_mode="HTML"):
        self.sent.append(text)


def test_due_session():
    # Thu 26 Nov 2026 08:00 WIB = Wed 25 Nov 20:00 New York
    now = datetime(2026, 11, 26, 1, 0, tzinfo=UTC)
    assert due_session("US", now)[0] == date(2026, 11, 25)       # 17:00 NY passed 3h ago
    assert due_session("IDX", now)[0] == date(2026, 11, 25)      # 16:30 WIB, 15.5h ago
    # Saturday noon Jakarta: Friday's IDX digest is 19.5h old (inside the window)
    assert due_session("IDX", datetime(2026, 11, 28, 5, 0, tzinfo=UTC))[0] == date(2026, 11, 27)
    # Monday morning Jakarta: Friday's digests are too stale to send
    assert due_session("IDX", datetime(2026, 11, 30, 1, 0, tzinfo=UTC)) is None


def test_catch_up_sends_missed_digest_once(monkeypatch, db):
    calls = []

    def fake_run(market, notifier, db=None, session_date=None, **kw):
        calls.append((market, session_date))
        notifier.send(f"{market} {session_date}")
        return digest.DigestResult("sent")

    monkeypatch.setattr(digest, "run", fake_run)
    rec = Recorder()
    w = Worker(scheduler=FakeScheduler(), notifier=rec, db=db)
    now = datetime(2026, 11, 26, 1, 0, tzinfo=UTC)               # PC woke Thu 08:00 WIB
    w.catch_up(now)
    assert sorted(calls) == [("IDX", date(2026, 11, 25)), ("US", date(2026, 11, 25))]
    w.catch_up(now)                                               # next check: nothing new
    w.run_digest("US", date(2026, 11, 25))                        # nor from the cron job
    assert len(calls) == 2
    assert db.get_job_run("digest_US", "2026-11-25")["status"] == "sent"


def test_catch_up_leaves_on_time_runs_to_cron(monkeypatch, db):
    monkeypatch.setattr(digest, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran")))
    w = Worker(scheduler=FakeScheduler(), notifier=Recorder(), db=db)
    w.catch_up(datetime(2026, 11, 25, 9, 31, tzinfo=UTC))        # 16:31 WIB, cron is on it
    db.record_job_run("digest_IDX", "2026-11-25", "no_data")
    w.catch_up(datetime(2026, 11, 25, 12, 0, tzinfo=UTC))        # holiday already handled


def test_update_prices_drops_unfinished_session(db, monkeypatch):
    days = pd.bdate_range("2026-11-20", "2026-11-25")
    c = pd.Series(range(len(days)), index=days, dtype=float)

    class Fake:
        def get_history(self, instruments, period):
            return {i.symbol: pd.DataFrame({"Open": c, "High": c, "Low": c, "Close": c, "Volume": 0.0})
                    for i in instruments}

        def get_fx(self, pair, period):
            return pd.Series(dtype=float)

    db.add_instrument("VOO", "US", "ETF", "USD")
    monkeypatch.setattr(update_prices, "last_closed_session", lambda market, now=None: date(2026, 11, 24))
    update_prices.run(period="1mo", provider=Fake(), db=db)
    assert db.get_price_history("VOO").index[-1] == pd.Timestamp("2026-11-24")


def test_backup_is_readable_and_pruned(db, tmp_path):
    db.record_job_run("digest_IDX", "2026-11-25", "sent")
    out = tmp_path / "bk"
    for minute in range(4):
        path = backup(db.db_path, out, keep=2, now=datetime(2026, 11, 26, 3, minute))
    assert sorted(p.name for p in out.iterdir()) == ["portograph-20261126-0302.db", "portograph-20261126-0303.db"]
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT status FROM job_runs").fetchone()[0] == "sent"


def test_status_command(db):
    db.record_job_run("digest_US", "2026-11-24", "sent")
    out = cmd.cmd_status(db, [], now=datetime.now(UTC))
    assert "US digest: sent for 2026-11-24" in out and "IDX digest: no scheduled run" in out
    assert cmd.cmd_status(db).startswith("<b>PortoGraph status</b>")  # how the bot calls it


def test_bot_calls_match_command_signatures():
    """_text_command(fn, needs_args=False) calls fn(db); every other command gets fn(db, args)."""
    import inspect
    import re
    from pathlib import Path

    src = Path(__file__).parents[1].joinpath("core", "telegram_bot.py").read_text(encoding="utf-8")
    for name, opts in re.findall(r"_text_command\(cmd\.(\w+)([^)]*)\)", src):
        params = list(inspect.signature(getattr(cmd, name)).parameters.values())
        required = [p for p in params if p.default is inspect.Parameter.empty]
        n_passed = 1 if "needs_args=False" in opts else 2
        assert len(required) <= n_passed <= len(params), f"{name} can't be called with {n_passed} args"
