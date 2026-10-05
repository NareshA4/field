#!/usr/bin/env python
"""Single-container supervisor for Render Free: Gunicorn (web) + Celery worker with embedded beat.

Fail-fast by design: if ANY child exits, every other child is stopped and this process exits non-zero, so the platform
restarts the whole container instead of leaving a silently half-dead service. SIGTERM/SIGINT are forwarded to all children
(graceful shutdown), and each child's output goes straight to stdout/stderr (visible in the Render log stream).
Memory-conscious (512 MB): one Gunicorn worker with threads and a solo Celery pool. Uses the project's only Celery app (`config`).
"""
import os
import signal
import subprocess
import sys
import time

PORT = os.environ.get("PORT", "8000")
PY = sys.executable

if os.environ.get("RUN_MIGRATIONS_ON_START", "").lower() in {"1", "true", "yes"}:
    subprocess.run([PY, "manage.py", "migrate", "--noinput"], check=True)

CHILDREN = {
    "web": [PY, "-m", "gunicorn", "config.wsgi:application", "--bind", f"0.0.0.0:{PORT}", "--workers", "1",
            "--worker-class", "gthread", "--threads", "4", "--timeout", "60", "--graceful-timeout", "20",
            "--access-logfile", "-", "--forwarded-allow-ips", "*"],
    "celery": [PY, "-m", "celery", "-A", "config", "worker", "-B", "--pool=solo", "--loglevel=info",
               "--schedule", "/tmp/celerybeat-schedule"],
}

procs: dict[str, subprocess.Popen] = {}
stopping = False


def stop_all(signum=None, _frame=None):
    global stopping
    stopping = True
    for p in procs.values():
        if p.poll() is None:
            p.terminate()


signal.signal(signal.SIGTERM, stop_all)
signal.signal(signal.SIGINT, stop_all)

for name, cmd in CHILDREN.items():
    procs[name] = subprocess.Popen(cmd)
    print(f"[supervisor] started {name} pid={procs[name].pid}", flush=True)

exit_code = 0
while True:
    dead = [n for n, p in procs.items() if p.poll() is not None]
    if dead:
        for n in dead:
            print(f"[supervisor] {n} exited with code {procs[n].returncode}", flush=True)
        exit_code = 0 if stopping else (procs[dead[0]].returncode or 1)
        stop_all()
        break
    time.sleep(1)

deadline = time.time() + 25
for p in procs.values():
    try:
        p.wait(timeout=max(1, deadline - time.time()))
    except subprocess.TimeoutExpired:
        p.kill()
sys.exit(exit_code)
