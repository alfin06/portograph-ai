"""Consistent copies of the SQLite database, safe to take while the bot and worker run.

The worker takes one every day; run it by hand before moving to another machine:
    python -m app.backup                 # writes BACKUP_DIR/portograph-YYYYMMDD-HHMM.db
    python -m app.backup --out D:\\usb   # somewhere else

To restore: stop PortoGraph, copy a backup over DATA_DIR/portograph.db, start it again.
"""
import argparse
import sqlite3
from datetime import datetime
from pathlib import Path

from core import config

PREFIX = "portograph-"


def backup(db_path: str = config.DB_PATH, dest_dir: Path | str = config.BACKUP_DIR,
           keep: int = config.BACKUP_KEEP, now: datetime | None = None) -> Path:
    """Copy the live DB with SQLite's backup API (includes anything still in the WAL),
    then delete all but the newest `keep` backups in dest_dir."""
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    out = dest_dir / f"{PREFIX}{(now or datetime.now()):%Y%m%d-%H%M}.db"
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(out)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    if keep > 0:
        for old in sorted(dest_dir.glob(f"{PREFIX}*.db"))[:-keep]:
            old.unlink()
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out", help="folder to write to (default: BACKUP_DIR)")
    args = p.parse_args()
    path = backup(dest_dir=args.out or config.BACKUP_DIR, keep=0 if args.out else config.BACKUP_KEEP)
    print(f"Backup written: {path} ({path.stat().st_size / 1024:,.0f} KB)")


if __name__ == "__main__":
    main()
