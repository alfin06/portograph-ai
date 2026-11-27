"""Market calendar: rate decisions, US data releases and market holidays.

The dates live in app/reference/calendar.json, copied by hand from the official schedules
(the Fed, Bank Indonesia, the US Bureau of Labor Statistics, IDX and NYSE). Those sites
publish once a year and several block automated reading, so the file is updated about
once a year; /calendar says when it is about to run out.
"""
import json
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

CAL_PATH = Path(__file__).resolve().parent.parent / "reference" / "calendar.json"
KINDS = {
    "fomc": "🏦", "bi_rate": "🏦", "us_cpi": "📊", "us_jobs": "📊",
    "idx_closed": "🚫", "nyse_closed": "🚫", "nyse_early_close": "⏰",
}
WARN_DAYS_BEFORE_END = 30


@dataclass(frozen=True)
class Event:
    day: date
    kind: str
    title: str
    market: str            # US | IDX

    @property
    def icon(self) -> str:
        return KINDS.get(self.kind, "•")


def load(path: Path | str = CAL_PATH) -> tuple[list[Event], dict]:
    """(events sorted by date, metadata). A missing or broken file gives no events
    rather than an error, so digests still go out."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], {}
    events = []
    for e in raw.get("events", []):
        try:
            if e["kind"] not in KINDS or e.get("market") not in ("US", "IDX"):
                continue
            events.append(Event(date.fromisoformat(e["date"]), e["kind"], str(e["title"])[:80], e["market"]))
        except (KeyError, ValueError, TypeError):
            continue
    meta = {k: v for k, v in raw.items() if k != "events"}
    return sorted(events, key=lambda e: (e.day, e.market, e.kind)), meta


def upcoming(events: list[Event], today: date, days: int) -> list[Event]:
    end = today + timedelta(days=days)
    return [e for e in events if today <= e.day <= end]


def covered_until(events: list[Event]) -> date | None:
    return max((e.day for e in events), default=None)


def needs_update(events: list[Event], today: date) -> bool:
    end = covered_until(events)
    return end is None or (end - today).days < WARN_DAYS_BEFORE_END


def closed(events: list[Event], market: str, day: date) -> Event | None:
    """The holiday event if that market is shut on that day."""
    kind = "idx_closed" if market == "IDX" else "nyse_closed"
    return next((e for e in events if e.day == day and e.kind == kind), None)
