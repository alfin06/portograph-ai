"""Start everything: the Telegram bot, the digest worker and the web dashboard, each in
its own process.

    python -m app.run            # start (refuses if one is already running)
    python -m app.run --status   # is it running? which processes are up?
    python -m app.run --stop     # ask the running copy to shut down cleanly

The same command runs on this PC (started at logon by scripts/windows/install-autostart.ps1)
and on a server (the Docker image's command). It:
  * restarts a process that crashes, waiting longer after each quick crash (max 5 min)
  * writes each process's output to LOG_DIR/bot.log and LOG_DIR/worker.log (rotated)
  * refuses to start a second copy, since two bots polling Telegram conflict
  * stops both cleanly on Ctrl+C, logoff or `docker stop`
"""
import logging
import os
import signal
import socket
import subprocess
import sys
import threading
import time
from logging.handlers import RotatingFileHandler

from core import config

log = logging.getLogger("run")

SERVICES = {"bot": "core.telegram_bot", "worker": "app.worker", "web": "app.web"}


def services_to_run() -> dict[str, str]:
    """The web dashboard is skipped (with a reason in run.log) when it's switched off or
    its settings would expose it without a password; the bot and worker still start."""
    out = dict(SERVICES)
    if not config.WEB_ENABLED:
        out.pop("web")
    elif config.web_problem():
        log.error("Web dashboard not started: %s", config.web_problem())
        out.pop("web")
    return out
LOCK_PORT = 47613            # localhost-only; held while running = single instance
STABLE_AFTER_S = 120         # a run this long resets the crash backoff
MAX_BACKOFF_S = 300


def _rotating(path, fmt="%(asctime)s %(message)s"):
    h = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=5, encoding="utf-8")
    h.setFormatter(logging.Formatter(fmt))
    return h


def acquire_single_instance():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", LOCK_PORT))
    except OSError:
        s.close()
        return None
    s.listen(2)
    return s


def control(cmd: str) -> str | None:
    """Send a command to the running copy; None if nothing is running."""
    try:
        with socket.create_connection(("127.0.0.1", LOCK_PORT), timeout=5) as c:
            c.sendall(cmd.encode())
            c.shutdown(socket.SHUT_WR)
            return c.recv(4096).decode(errors="replace")
    except OSError:
        return None


def _serve_control(lock: socket.socket, services, shutdown):
    """Answer --status / --stop from the same machine (the port is bound to 127.0.0.1)."""
    while True:
        try:
            conn, _ = lock.accept()
        except OSError:
            return  # socket closed on exit
        with conn:
            conn.settimeout(5)
            try:
                req = conn.recv(64).decode(errors="replace").strip()
            except OSError:
                continue
            if req == "status":
                conn.sendall("\n".join(
                    f"{s.name}: {'running, pid ' + str(s.proc.pid) if s.proc and s.proc.poll() is None else 'restarting'}"
                    for s in services).encode())
            elif req == "stop":
                conn.sendall(b"stopping")
                threading.Thread(target=shutdown, daemon=True).start()


class Service:
    def __init__(self, name: str, module: str, stopping: threading.Event):
        self.name, self.module, self.stopping = name, module, stopping
        self.proc: subprocess.Popen | None = None
        self.out = logging.getLogger(f"svc.{name}")
        self.out.propagate = False
        self.out.setLevel(logging.INFO)
        self.out.addHandler(_rotating(config.LOG_DIR / f"{name}.log", "%(message)s"))

    def _pump(self, proc):
        for line in proc.stdout:
            self.out.info(line.rstrip())

    def supervise(self):
        backoff = 5
        while not self.stopping.is_set():
            started = time.monotonic()
            log.info("starting %s", self.name)
            self.proc = subprocess.Popen(
                [sys.executable, "-u", "-m", self.module], cwd=config.PROJECT_ROOT,
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            pump = threading.Thread(target=self._pump, args=(self.proc,), daemon=True)
            pump.start()
            code = self.proc.wait()
            pump.join(timeout=5)
            if self.stopping.is_set():
                break
            ran = time.monotonic() - started
            backoff = 5 if ran > STABLE_AFTER_S else min(backoff * 2, MAX_BACKOFF_S)
            log.warning("%s exited with code %s after %.0fs; restarting in %ss",
                        self.name, code, ran, backoff)
            self.stopping.wait(backoff)

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def main():
    if len(sys.argv) > 1 and sys.argv[1] in ("--status", "--stop"):
        reply = control(sys.argv[1][2:])
        print(reply if reply is not None else "PortoGraph is not running.")
        return
    config.LOG_DIR.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(_rotating(config.LOG_DIR / "run.log", "%(asctime)s %(levelname)s %(name)s: %(message)s"))
    if sys.stdout is not None:  # pythonw has no console
        root.addHandler(logging.StreamHandler(sys.stdout))

    lock = acquire_single_instance()
    if lock is None:
        # exit 0 so Task Scheduler doesn't treat it as a crash and keep retrying
        log.warning("PortoGraph is already running on this computer; not starting a second copy.")
        return
    config.require_telegram()

    stopping = threading.Event()
    services = [Service(n, m, stopping) for n, m in services_to_run().items()]

    def shutdown(*_):
        if not stopping.is_set():
            log.info("stopping…")
            stopping.set()
            for s in services:
                s.stop()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    if hasattr(signal, "SIGBREAK"):  # Windows console close / logoff
        signal.signal(signal.SIGBREAK, shutdown)

    threading.Thread(target=_serve_control, args=(lock, services, shutdown), daemon=True).start()
    threads = [threading.Thread(target=s.supervise, name=s.name, daemon=True) for s in services]
    for t in threads:
        t.start()
    names = " + ".join(s.name for s in services)
    log.info("PortoGraph running (%s). Logs in %s", names, config.LOG_DIR)
    if any(s.name == "web" for s in services):
        log.info("Dashboard: http://%s:%s", "127.0.0.1" if config.WEB_HOST in config.LOOPBACK_HOSTS
                 else config.WEB_HOST, config.WEB_PORT)
    try:
        while any(t.is_alive() for t in threads):
            time.sleep(1)
    except KeyboardInterrupt:
        shutdown()
    for t in threads:
        t.join(timeout=20)
    lock.close()
    log.info("stopped")


if __name__ == "__main__":
    main()
