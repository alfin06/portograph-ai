# Security

PortoGraph is a single-user app: one person, one PC (or one server later). The goal is
that nobody else can read your holdings, capital or keys, or make the app act for them.

## What protects what

**Secrets**
- The bot token, chat ID and LLM key live only in `.env`, which git and Docker both
  ignore. They are never logged: request URLs (which contain the bot token) are not
  logged, and error messages drop them.
- `scripts/windows/lock-down-files.ps1` limits `.env`, the database, backups and logs
  to your Windows account (by default every account on the PC can read files on D:\).

**Telegram bot**
- Only your own private chat (the `TELEGRAM_CHAT_ID` in `.env`) can use it. Everyone
  else gets no reply at all.
- It fails closed: with no chat ID set, every command is off except `/start`, which
  only tells you your chat ID.
- Group chats are refused even if their ID matches.

**Web dashboard** (`app/web/security.py`)
- By default it listens on 127.0.0.1 only. It won't listen anywhere else without
  `WEB_PASSWORD` (12+ characters), and it locks a client out for 15 minutes after
  10 wrong passwords.
- It only answers to expected host names (localhost, or `WEB_ALLOWED_HOSTS`), which
  blocks DNS rebinding.
- Other websites can link to it but can't load it in the background, embed it in a
  frame, or submit its forms (Fetch Metadata plus an Origin check).
- A strict Content-Security-Policy allows no inline scripts, no `eval` and no outside
  resources. HTMX's `eval` features are off.
- Every input is validated: symbols match a strict pattern, redirects only go to its
  own pages, rules are capped at 300 characters, and request bodies at 64 KB.
- Pages with your data are never cached, and responses send no `Server` or
  `Referer` headers.

**Code and data**
- SQL always uses parameters. The only dynamic SQL builds column names from a fixed
  list.
- Screen rules are parsed against an allow-list of fields and comparisons and are
  never run as code.
- The LLM's rule suggestions (`/reflect`) are applied only when you press Approve.

**Dependencies**
- `requirements.lock` pins the exact versions that were tested, and Docker installs
  from it.
- The JavaScript libraries are copied into the repo (no CDN) with their licenses.
- Check for known vulnerabilities after upgrading (see below).

**Docker** (for a future server)
- The container runs as a non-root user with a read-only filesystem, no Linux
  capabilities and no privilege escalation.
- The dashboard is published on the server's localhost only; reach it through an
  SSH tunnel.

## What it doesn't protect against

- **Someone using your Windows account**, or malware running as you: they can read
  `.env` like the app does.
- **A password over home Wi-Fi.** Dashboard passwords travel unencrypted on the
  network (plain HTTP). On a server, use the SSH tunnel; for phone access at home,
  only use trusted Wi-Fi.
- **The local control port (127.0.0.1:47613).** Any program running on this PC can
  ask it for status or to stop. It can't do anything else.
- **Third parties.** Yahoo, Telegram and your LLM provider see what you ask them
  (symbols, messages, `/reflect` trade history).

## Routine
- After `pip install --upgrade ...`: run the tests, then regenerate the lock file with
  `python -m pip freeze > requirements.lock` (keep the header comment).
- Check for known vulnerabilities at https://osv.dev, or with `pip-audit -r requirements.lock`.
- Never commit `database/*.db` or `.env`; `.gitignore` already blocks both.
