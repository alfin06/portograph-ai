"""Push messages to your Telegram chat over the Bot HTTP API.

Plain synchronous HTTPS so scheduled jobs can send without running the bot's async
event loop. The interactive bot (core/telegram_bot.py) stays a separate process."""
import logging
import time

import requests

from core import config

log = logging.getLogger(__name__)

API_URL = "https://api.telegram.org/bot{token}/sendMessage"
MAX_LEN = 4096  # Telegram's hard limit per message


def split_message(text: str, limit: int = MAX_LEN) -> list[str]:
    """Split on paragraph boundaries so HTML tags like <pre> are never cut in half.
    A single paragraph longer than the limit is split on lines."""
    chunks, current = [], ""
    for para in text.split("\n\n"):
        pieces = [para] if len(para) <= limit else _split_lines(para, limit)
        for piece in pieces:
            candidate = f"{current}\n\n{piece}" if current else piece
            if len(candidate) <= limit:
                current = candidate
            else:
                chunks.append(current)
                current = piece
    if current:
        chunks.append(current)
    return chunks


def _split_lines(para: str, limit: int) -> list[str]:
    out, cur = [], ""
    for line in para.split("\n"):
        cand = f"{cur}\n{line}" if cur else line
        if len(cand) <= limit:
            cur = cand
        else:
            out.append(cur)
            cur = line[:limit]
    if cur:
        out.append(cur)
    return out


class TelegramUnreachable(RuntimeError):
    """Network trouble reaching Telegram (VPN drop, timeout, Telegram 5xx/429) after retries.
    The worker leaves such a digest unrecorded so its catch-up check sends it later."""


RETRY_WAITS = (5, 15, 40)  # seconds between attempts


class TelegramNotifier:
    def __init__(self, token: str | None = None, chat_id: str | None = None, session=None,
                 sleep=time.sleep):
        self.token = token or config.TELEGRAM_BOT_TOKEN
        self.chat_id = chat_id or config.TELEGRAM_CHAT_ID
        if not self.token or not self.chat_id:
            raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must both be set in .env")
        self._session = session or requests.Session()
        self._sleep = sleep

    def send(self, text: str, parse_mode: str = "HTML") -> None:
        for chunk in split_message(text):
            payload = {"chat_id": self.chat_id, "text": chunk, "disable_web_page_preview": True}
            if parse_mode:
                payload["parse_mode"] = parse_mode
            self._post(payload)

    def _post(self, payload: dict) -> None:
        problem = ""
        for attempt, wait in enumerate((0, *RETRY_WAITS)):
            if wait:
                log.warning("Telegram send failed (%s), retrying in %ss", problem, wait)
                self._sleep(wait)
            try:
                resp = self._session.post(API_URL.format(token=self.token), json=payload, timeout=30)
            except requests.RequestException as e:
                # requests puts the full URL (with the token) in its messages; drop it
                problem = type(e).__name__
                continue
            if resp.status_code == 200:
                return
            if resp.status_code == 429 or resp.status_code >= 500:   # rate limit / Telegram down
                problem = f"HTTP {resp.status_code}"
                continue
            # A real rejection (bad HTML, wrong chat id): retrying won't help.
            # Never log the URL: it contains the bot token
            raise RuntimeError(f"Telegram send failed ({resp.status_code}): {resp.text[:200]}")
        raise TelegramUnreachable(f"Telegram unreachable after {len(RETRY_WAITS) + 1} tries: {problem}")


class ConsoleNotifier:
    """Prints instead of sending; used by --dry-run."""
    def send(self, text: str, parse_mode: str = "HTML") -> None:
        print(text)
