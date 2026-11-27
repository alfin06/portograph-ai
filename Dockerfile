# PortoGraph AI: Telegram bot + market digest worker + web dashboard in one container.
# Used when moving to a server; on Windows you can run it natively instead (see README).
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/app/data \
    LOG_DIR=/app/data/logs \
    HOME=/tmp \
    XDG_CACHE_HOME=/tmp/cache

WORKDIR /app
# Exact tested versions (requirements.lock), with a pip that has no known vulnerabilities
COPY requirements.lock .
RUN pip install --upgrade "pip>=26.1" && pip install -r requirements.lock

# Code stays owned by root, so the running app can't modify itself; only data/ is writable
COPY . .
RUN useradd --uid 1000 --no-create-home --shell /usr/sbin/nologin portograph \
    && mkdir -p /app/data \
    && chown -R portograph /app/data \
    && chmod 700 /app/data
USER portograph
EXPOSE 8000
# yfinance keeps a small cache under XDG_CACHE_HOME; /tmp is the only writable place
# besides data/ when docker-compose runs the container read-only

# The worker records every digest run in the DB, so a restart never double-sends
CMD ["python", "-m", "app.run"]
