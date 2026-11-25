"""Scheduler process: runs the market digests on their own exchange clocks.

Run from the project root and keep it running (alongside the bot):
    python -m app.worker            # start the schedule
    python -m app.worker --list     # show the next run times and exit
"""
import argparse
import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

from app.jobs import digest
from core import config

log = logging.getLogger("worker")

RETRY_EVERY_MIN = 20   # Yahoo can post the closing bar late
MAX_RETRIES = 3


def _hm(value: str) -> tuple[int, int]:
    h, m = value.split(":")
    return int(h), int(m)


class Worker:
    def __init__(self, scheduler=None, notifier=None, db=None):
        self.scheduler = scheduler or BlockingScheduler(
            job_defaults={"coalesce": True, "misfire_grace_time": 3600, "max_instances": 1})
        self._notifier = notifier
        self._db = db

    @property
    def notifier(self):
        if self._notifier is None:
            from app.notify.telegram import TelegramNotifier
            self._notifier = TelegramNotifier()
        return self._notifier

    def schedule(self):
        for market, at in (("IDX", config.DIGEST_IDX_TIME), ("US", config.DIGEST_US_TIME)):
            h, m = _hm(at)
            self.scheduler.add_job(
                self.run_digest, CronTrigger(day_of_week="mon-fri", hour=h, minute=m,
                                             timezone=digest.MARKET_TZ[market]),
                args=[market], id=f"digest_{market}", name=f"{market} close digest",
                replace_existing=True)

    def run_digest(self, market: str, session_date=None, attempt: int = 0):
        session_date = session_date or digest.market_today(market)
        try:
            result = digest.run(market, self.notifier, db=self._db, session_date=session_date)
        except Exception as e:
            log.exception("%s digest failed", market)
            self._safe_send(f"⚠️ {market} digest failed: {type(e).__name__}: {e}")
            return

        if result.status == "sent":
            log.info("%s digest sent for %s", market, session_date)
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
            self._safe_send(f"ℹ️ {market}: no closing prices for {session_date:%a %d %b} "
                            f"(holiday or data feed late). Try /digest {market.lower()} later.")

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
