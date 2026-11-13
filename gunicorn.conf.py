"""
Gunicorn configuration automatically loaded from the working directory
even when invoked simply as ``gunicorn app:app`` without ``--config``.
"""
import os

bind = f"0.0.0.0:{os.environ.get('PORT', '10000')}"
workers = int(os.environ.get("GUNICORN_WORKERS", "1"))
worker_class = "gthread"
threads = int(os.environ.get("GUNICORN_THREADS", "8"))
timeout = 300
graceful_timeout = 60
keepalive = 5
preload_app = False


def post_worker_init(worker):
    """Start ClickHouse warm-up inside the live worker process after fork."""
    try:
        import app as _app_mod
        _app_mod.start_background_prewarm()
    except Exception as exc:
        worker.log.warning("post_worker_init prewarm failed to start: %s", exc)

