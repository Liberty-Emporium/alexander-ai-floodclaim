"""
Aquila's Built-In Cron Scheduler (Health Monitor)
==================================================
Runs INSIDE the Flask app process on Railway.
Aquila wakes up on schedule, runs health checks,
saves results to the DB, and attempts self-healing.

No SMS. No email. All results visible in-app.

Schedule:
  - Health check: every 30 minutes
  - Daily summary: every day at 9:00 AM (ET)
"""
import os
import sys
import json
import time
import threading
import datetime
import logging
import sqlite3

logger = logging.getLogger(__name__)

HEALTH_CHECK_INTERVAL = 30 * 60   # 30 minutes
DAILY_SUMMARY_HOUR = 9            # 9 AM ET
DAILY_SUMMARY_MINUTE = 0
ET_OFFSET = -5                    # EST = -5
DB_PATH = None                    # resolved lazily


def _db():
    """Lazily resolve DB_PATH."""
    global DB_PATH
    if DB_PATH is None:
        try:
            from models.database import DB_PATH as _dbp
            DB_PATH = _dbp
        except Exception:
            pass
    if DB_PATH is None:
        DB_PATH = os.environ.get('DB_PATH') or os.path.join(
            os.environ.get('RAILWAY_VOLUME_MOUNT_PATH', '/data'), 'floodclaim.db')
    return DB_PATH


def _get_setting_local(key, default=''):
    try:
        db = sqlite3.connect(_db())
        db.row_factory = sqlite3.Row
        row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        db.close()
        return row['value'] if row else default
    except Exception:
        return default


def _set_setting_local(key, value):
    try:
        db = sqlite3.connect(_db())
        db.execute(
            'INSERT INTO settings (key, value) VALUES (?,?) '
            'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
            (key, value))
        db.commit()
        db.close()
    except Exception:
        pass


def attempt_self_heal(report):
    """Try to auto-fix issues Aquila can handle. Returns list of healed items."""
    healed = []
    try:
        from routes.willie import _get_default_brain
        for check in report.get("checks", []):
            if check.get("ok"):
                continue
            if check["name"] == "Aquila Brain" and check.get("issues"):
                for key in ["brain_identity_md", "brain_soul_md", "brain_memory_md",
                            "brain_system_prompt", "brain_photo_prompt"]:
                    val = _get_default_brain(key)
                    if val:
                        _set_setting_local(key, val)
                healed.append("🧠 Aquila brain files reinitialized")
    except Exception as e:
        logger.error(f"[AQUILA_CRON] Self-heal error: {e}")
    return healed


def _get_et_hour():
    """Get current hour in Eastern Time."""
    utc_now = datetime.datetime.utcnow()
    return (utc_now.hour + ET_OFFSET) % 24, utc_now.minute


def run_health_cycle():
    """Run one health check cycle. Save results to DB."""
    from services.health_monitor import run_all_checks
    logger.info("[AQUILA_CRON] Running health check...")

    try:
        report = run_all_checks()
    except Exception as e:
        logger.error(f"[AQUILA_CRON] Health check failed: {e}")
        return

    total = len(report["checks"])
    failing = sum(1 for c in report["checks"] if not c.get("ok", True))
    logger.info(f"[AQUILA_CRON] {total - failing}/{total} passed, {failing} failing")

    # Save to settings (overwrites previous — health_monitor already does this)
    # Also log to activity log if there are issues
    if not report["all_ok"]:
        issues = report.get("critical_issues", [])
        try:
            db = sqlite3.connect(_db())
            db.execute(
                "INSERT INTO activity_log (claim_id, actor, action) VALUES (?, ?, ?)",
                (0, "Aquila Monitor",
                 f"Health alert: {len(issues)} issue(s) — {'; '.join(i[:100] for i in issues[:3])}")
            )
            db.commit()
            db.close()
        except Exception as e:
            logger.error(f"[AQUILA_CRON] Activity log error: {e}")

        # Attempt self-healing
        healed = attempt_self_heal(report)
        if healed:
            try:
                db = sqlite3.connect(_db())
                for h in healed:
                    db.execute(
                        "INSERT INTO activity_log (claim_id, actor, action) VALUES (?, ?, ?)",
                        (0, "Aquila Self-Heal", h)
                    )
                db.commit()
                db.close()
            except Exception:
                pass


def run_daily_summary():
    """Run daily morning summary. Save to DB."""
    from services.health_monitor import run_all_checks
    logger.info("[AQUILA_CRON] Running daily summary...")

    try:
        report = run_all_checks()
    except Exception as e:
        logger.error(f"[AQUILA_CRON] Daily summary failed: {e}")
        return

    checks = report.get("checks", [])
    ok_count = sum(1 for c in checks if c.get("ok", True))
    fail_count = len(checks) - ok_count
    issues = report.get("critical_issues", [])

    # Get claim stats
    try:
        db = sqlite3.connect(_db())
        claim_count = db.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
        open_count = db.execute("SELECT COUNT(*) FROM claims WHERE status != 'Closed'").fetchone()[0]
        pipeline = db.execute("SELECT COALESCE(SUM(total_estimate),0) FROM claims WHERE status != 'Closed'").fetchone()[0]
        photo_count = db.execute("SELECT COUNT(*) FROM photos WHERE deleted_at IS NULL").fetchone()[0]
        db.close()
    except Exception:
        claim_count = open_count = pipeline = photo_count = 0

    summary_parts = [
        f"🌅 Daily Summary — {datetime.datetime.now().strftime('%B %d, %Y')}",
        f"System: {ok_count}/{len(checks)} checks passing" + (f" ({fail_count} failing)" if fail_count else ""),
    ]
    if issues:
        summary_parts.append("Issues:")
        for i in issues[:5]:
            summary_parts.append(f"  • {i}")
    summary_parts.append(f"Claims: {claim_count} total, {open_count} open, pipeline ${pipeline:,.0f}")
    summary_parts.append(f"Photos: {photo_count}")

    summary = "\n".join(summary_parts)

    try:
        db = sqlite3.connect(_db())
        db.execute(
            "INSERT INTO activity_log (claim_id, actor, action) VALUES (?, ?, ?)",
            (0, "Aquila Daily", summary[:500])
        )
        db.commit()
        db.close()
    except Exception as e:
        logger.error(f"[AQUILA_CRON] Daily summary log error: {e}")


_last_daily_date = None


def scheduler_loop():
    """
    Main scheduler thread. Runs forever.
    Wakes every minute, checks if it's time for health check or daily summary.
    """
    global _last_daily_date

    logger.info(
        f"[AQUILA_CRON] Scheduler started — "
        f"health every {HEALTH_CHECK_INTERVAL // 60}min | "
        f"daily at {DAILY_SUMMARY_HOUR}:00 ET"
    )

    # Initial health check after 30s boot delay
    time.sleep(30)
    try:
        run_health_cycle()
    except Exception as e:
        logger.error(f"[AQUILA_CRON] Initial check failed: {e}")

    while True:
        try:
            time.sleep(60)

            # Health check interval
            now = time.time()
            last_run = float(_get_setting_local("aquila_health_last_run", "0"))
            if now - last_run >= HEALTH_CHECK_INTERVAL:
                _set_setting_local("aquila_health_last_run", str(int(now)))
                try:
                    run_health_cycle()
                except Exception as e:
                    logger.error(f"[AQUILA_CRON] Health check error: {e}")

            # Daily summary at ET time
            et_hour, et_min = _get_et_hour()
            today = datetime.date.today().isoformat()
            if (et_hour == DAILY_SUMMARY_HOUR and 0 <= et_min < 2
                    and _last_daily_date != today):
                _last_daily_date = today
                try:
                    run_daily_summary()
                except Exception as e:
                    logger.error(f"[AQUILA_CRON] Daily summary error: {e}")

        except Exception as e:
            logger.error(f"[AQUILA_CRON] Scheduler error: {e}", exc_info=True)
            time.sleep(60)


def start_scheduler():
    """Call from app.py to start Aquila's background scheduler."""
    t = threading.Thread(target=scheduler_loop, daemon=True, name="aquila-cron")
    t.start()
    logger.info("[AQUILA_CRON] Background scheduler thread started")
    return t
