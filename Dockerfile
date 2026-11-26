# PortoGraph AI: Telegram bot + market digest worker + web dashboard in one container.
# Used when moving to a server; on Windows you can run it natively instead (see README).
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/app/data \
    LOG_DIR=/app/data/logs

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
RUN useradd --uid 1000 --create-home portograph \
    && mkdir -p /app/data \
    && chown -R portograph /app/data
USER portograph
EXPOSE 8000

# The worker records every digest run in the DB, so a restart never double-sends
CMD ["python", "-m", "app.run"]
