"""Push messages to your Telegram chat over the Bot HTTP API.

Plain synchronous HTTPS so scheduled jobs can send without running the bot's async
event loop. The interactive bot (core/telegram_bot.py) stays a separate process."""
import logging

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


class TelegramNotifier:
    def __init__(self, token: str | None = None, chat_id: str | None = None, session=None):
        self.token = token or config.TELEGRAM_BOT_TOKEN
        self.chat_id = chat_id or config.TELEGRAM_CHAT_ID
        if not self.token or not self.chat_id:
            raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must both be set in .env")
        self._session = session or requests.Session()

    def send(self, text: str, parse_mode: str = "HTML") -> None:
        for chunk in split_message(text):
            payload = {"chat_id": self.chat_id, "text": chunk, "disable_web_page_preview": True}
            if parse_mode:
                payload["parse_mode"] = parse_mode
            try:
                resp = self._session.post(API_URL.format(token=self.token), json=payload, timeout=20)
            except requests.RequestException as e:
                # requests puts the full URL (with the token) in its messages; drop it
                raise RuntimeError(f"Telegram unreachable: {type(e).__name__}") from None
            if resp.status_code != 200:
                # Never log the URL: it contains the bot token
                raise RuntimeError(f"Telegram send failed ({resp.status_code}): {resp.text[:200]}")


class ConsoleNotifier:
    """Prints instead of sending; used by --dry-run."""
    def send(self, text: str, parse_mode: str = "HTML") -> None:
        print(text)
