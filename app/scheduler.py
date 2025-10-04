# app/scheduler.py
import os
import logging
from datetime import date

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import text
from sqlalchemy.engine import Engine

from .extensions import db
from .models import User
from .services.recurring import run_due_rules_for_user

logger = logging.getLogger(__name__)

PG_LOCK_KEY = 0x6C5E_1234_0000_0002  # any int < 2^63

def _is_postgres(engine: Engine) -> bool:
    try:
        return engine.dialect.name.startswith("postgres")
    except Exception:
        return False

def _acquire_lock() -> bool:
    """Use advisory lock on Postgres; on other DBs, just proceed."""
    engine = db.session.get_bind()
    if not _is_postgres(engine):
        logger.info("[recurring] Non-Postgres DB detected; skipping advisory lock.")
        return True
    try:
        got = db.session.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": PG_LOCK_KEY}).scalar()
        logger.info(f"[recurring] Acquire lock -> {bool(got)}")
        return bool(got)
    except Exception as e:
        logger.error(f"[recurring] Lock acquire failed: {e}")
        db.session.rollback()
        # Fall back to running anyway (useful in dev)
        return True

def _release_lock():
    engine = db.session.get_bind()
    if not _is_postgres(engine):
        return
    try:
        db.session.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": PG_LOCK_KEY})
        db.session.commit()
        logger.info("[recurring] Lock released")
    except Exception as e:
        logger.error(f"[recurring] Lock release failed: {e}")
        db.session.rollback()

def _run_all_due_today(app):
    with app.app_context():
        if not _acquire_lock():
            logger.info("[recurring] Skipped — another instance holds the lock.")
            return
        try:
            today = date.today()
            user_ids = [uid for (uid,) in db.session.query(User.id).all()]
            logger.info(f"[recurring] Catch-up start (today={today}, users={len(user_ids)})")
            total_created = total_errors = 0
            for uid in user_ids:
                summary = run_due_rules_for_user(uid, today=today)
                c = summary.get("created", 0)
                e = len(summary.get("errors", []))
                total_created += c
                total_errors  += e
                logger.info(f"[recurring] user={uid} created={c} errors={e}")
                if e:
                    for rid, msg in summary["errors"]:
                        logger.warning(f"[recurring]  - rule={rid}: {msg}")
            logger.info(f"[recurring] Catch-up done: created={total_created}, errors={total_errors}")
        finally:
            _release_lock()

def start_scheduler(app):
    """
    Start APScheduler exactly once.
    - Daily run at 01:10 Asia/Seoul (you set 1:10; keep or change).
    - Boot catch-up run immediately.
    - Optional hourly pulse (commented).
    """
    # Avoid double-start with Flask reloader / multiple imports
    if app.config.get("APSCHEDULER_STARTED"):
        return
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true" or not app.debug:
        sched = BackgroundScheduler(timezone="Asia/Seoul")
        sched.add_job(lambda: _run_all_due_today(app), CronTrigger(hour=1, minute=10))
        # Optional: hourly safety pulse
        # sched.add_job(lambda: _run_all_due_today(app), CronTrigger(minute=5))
        sched.start()
        app.config["APSCHEDULER_STARTED"] = True
        logger.info("[recurring] Scheduler started (Asia/Seoul @ 01:10)")

        # Boot catch-up once
        _run_all_due_today(app)
    else:
        logger.info("[recurring] Skipping scheduler in reloader child process.")
