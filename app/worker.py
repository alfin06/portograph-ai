"""Scheduler process: runs the market digests on their own exchange clocks.

Usually started by `python -m app.run` (bot + worker together). On its own:
    python -m app.worker            # start the schedule
    python -m app.worker --list     # show the next run times and exit

Built for a computer that sleeps or shuts down: every outcome is recorded in job_runs,
and a catch-up check (at start and every CATCHUP_EVERY_MIN) sends a digest that was
missed, if it is less than CATCHUP_HOURS late. A digest is never sent twice.
"""
import argparse
import logging
import threading
from datetime import date, datetime, timedelta

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.jobs import digest
from core import config

log = logging.getLogger("worker")

RETRY_EVERY_MIN = 20   # Yahoo can post the closing bar late
MAX_RETRIES = 3
CATCHUP_EVERY_MIN = 15
BACKUP_AT = (3, 15)    # local time of the daily DB backup
MARKETS = ("IDX", "US")


def _hm(value: str) -> tuple[int, int]:
    h, m = value.split(":")
    return int(h), int(m)


def digest_time(market: str) -> tuple[int, int]:
    return _hm(config.DIGEST_IDX_TIME if market == "IDX" else config.DIGEST_US_TIME)


def due_session(market: str, now: datetime) -> tuple[date, datetime] | None:
    """The latest weekday whose digest time has passed, with that digest time,
    or None if it is more than CATCHUP_HOURS ago (too stale to be worth sending)."""
    tz = digest.MARKET_TZ[market]
    local = now.astimezone(tz)
    h, m = digest_time(market)
    d = local.date()
    while True:
        at = datetime(d.year, d.month, d.day, h, m, tzinfo=tz)
        if d.weekday() < 5 and at <= local:
            break
        d -= timedelta(days=1)
    return (d, at) if local - at <= timedelta(hours=config.CATCHUP_HOURS) else None


class Worker:
    def __init__(self, scheduler=None, notifier=None, db=None):
        self.scheduler = scheduler or BlockingScheduler(
            job_defaults={"coalesce": True, "misfire_grace_time": 3600, "max_instances": 1})
        self._notifier = notifier
        self._db = db
        self._lock = threading.Lock()
        self._busy: set[tuple[str, date]] = set()   # in progress, incl. waiting for a retry

    @property
    def db(self):
        if self._db is None:
            from database.db_manager import DBManager
            self._db = DBManager()
        return self._db

    @property
    def notifier(self):
        if self._notifier is None:
            from app.notify.telegram import TelegramNotifier
            self._notifier = TelegramNotifier()
        return self._notifier

    def schedule(self):
        for market in MARKETS:
            h, m = digest_time(market)
            self.scheduler.add_job(
                self.run_digest, CronTrigger(day_of_week="mon-fri", hour=h, minute=m,
                                             timezone=digest.MARKET_TZ[market]),
                args=[market], id=f"digest_{market}", name=f"{market} close digest",
                replace_existing=True)
        self.scheduler.add_job(self.catch_up, IntervalTrigger(minutes=CATCHUP_EVERY_MIN),
                               id="catch_up", name="missed-digest check", replace_existing=True,
                               next_run_time=datetime.now().astimezone())
        self.scheduler.add_job(self.run_backup, CronTrigger(hour=BACKUP_AT[0], minute=BACKUP_AT[1]),
                               id="backup", name="database backup", replace_existing=True)

    def catch_up(self, now: datetime | None = None):
        now = now or datetime.now().astimezone()
        for market in MARKETS:
            due = due_session(market, now)
            if due is None:
                continue
            session, at = due
            if now - at < timedelta(minutes=2):
                continue  # the regular cron job is firing right now
            with self._lock:
                busy = (market, session) in self._busy   # e.g. waiting for a retry
            if not busy and self.db.get_job_run(f"digest_{market}", session.isoformat()) is None:
                log.info("%s digest for %s was missed (computer off/asleep?), sending now",
                         market, session)
                self.run_digest(market, session)

    def run_digest(self, market: str, session_date=None, attempt: int = 0):
        session_date = session_date or digest.market_today(market)
        key, job = (market, session_date), f"digest_{market}"
        if attempt == 0:
            with self._lock:
                if key in self._busy or self.db.get_job_run(job, session_date.isoformat()):
                    log.info("%s digest for %s already handled, skipping", market, session_date)
                    return
                self._busy.add(key)
        try:
            result = digest.run(market, self.notifier, db=self._db, session_date=session_date)
        except Exception as e:
            log.exception("%s digest failed", market)
            self._finish(key, job, "failed")
            self._safe_send(f"⚠️ {market} digest failed: {type(e).__name__}: {e}")
            return

        if result.status == "sent":
            log.info("%s digest sent for %s", market, session_date)
            self._finish(key, job, "sent")
        elif attempt < MAX_RETRIES:
            when = datetime.now(tz=digest.MARKET_TZ[market]) + timedelta(minutes=RETRY_EVERY_MIN)
            log.info("%s: no data for %s yet, retry %d at %s", market, session_date, attempt + 1, when)
            self.scheduler.add_job(self.run_digest, DateTrigger(run_date=when),
                                   args=[market, session_date, attempt + 1],
                                   id=f"digest_{market}_retry", replace_existing=True)
        else:
            # Exchange holiday, or the feed never posted the close (Yahoo sometimes returns
            # a row with a blank close). Say so in one line rather than staying silent.
            log.info("%s: no session data on %s, digest skipped", market, session_date)
            self._finish(key, job, "no_data")
            self._safe_send(f"ℹ️ {market}: no closing prices for {session_date:%a %d %b} "
                            f"(holiday or data feed late). Try /digest {market.lower()} later.")

    def _finish(self, key, job: str, status: str):
        try:
            self.db.record_job_run(job, key[1].isoformat(), status)
        except Exception:
            log.exception("Could not record %s run", job)
        with self._lock:
            self._busy.discard(key)

    def run_backup(self):
        from app.backup import backup
        try:
            log.info("Database backup written: %s", backup(db_path=self.db.db_path))
        except Exception as e:
            log.exception("Backup failed")
            self._safe_send(f"⚠️ Database backup failed: {type(e).__name__}: {e}")

    def _safe_send(self, text: str):
        try:
            self.notifier.send(text, parse_mode=None)
        except Exception:
            log.exception("Could not send error notice to Telegram")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--list", action="store_true", help="print the schedule and exit")
    args = p.parse_args()

    w = Worker()
    w.schedule()
    if args.list:
        for job in w.scheduler.get_jobs():
            nxt = job.trigger.get_next_fire_time(None, datetime.now(tz=job.trigger.timezone))
            print(f"  {job.name:<20} next: {nxt:%a %d %b %H:%M %Z}")
        return

    config.require_telegram()
    w.notifier  # fail fast if TELEGRAM_CHAT_ID is missing
    log.info("Worker started; Ctrl+C to stop")
    w.scheduler.start()


if __name__ == "__main__":
    main()
