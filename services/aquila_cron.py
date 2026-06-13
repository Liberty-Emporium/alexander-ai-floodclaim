"""
Aquila's Built-In Cron Scheduler
=================================
Runs INSIDE the Flask app process on Railway.
Aquila wakes up on schedule, runs health checks, self-heals,
and sends alerts — all with its own brain and personality.

No external cron. No HTTP calls. No dependencies.
Pure threading + time.sleep.

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

import requests as _req
from models.database import get_setting, set_setting, DB_PATH as _DB_PATH


def _db():
    """Lazily resolve DB_PATH at call time (after _set_paths has run)."""
    p = _DB_PATH
    if p is None:
        p = os.environ.get('DB_PATH') or os.path.join(
            os.environ.get('RAILWAY_VOLUME_MOUNT_PATH', '/data'), 'floodclaim.db')
    return p
from services.health_monitor import run_all_checks
from services.email import send_email

# ── Config ────────────────────────────────────────────────────────────────────

HEALTH_CHECK_INTERVAL = 30 * 60      # 30 minutes between health checks
DAILY_SUMMARY_HOUR = 9               # 9 AM daily summary (ET)
DAILY_SUMMARY_MINUTE = 0
ET_OFFSET = -5                       # Eastern Time UTC offset (EST = -5)
ALERT_COOLDOWN = 6 * 60 * 60  # 6 hours — don't re-alert for same issue

# ── Alert formatting ──────────────────────────────────────────────────────────


def _get_setting_local(key, default=''):
    try:
        db = sqlite3.connect(_db())
        db.row_factory = sqlite3.Row
        row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        db.close()
        return row['value'] if row else default
    except Exception:
        return default or os.environ.get(key.upper(), '')


def format_sms_alert(report):
    """Aquila-style SMS alert — short, clear, action-oriented."""
    issues = report.get("critical_issues", [])
    lines = ["🚨 FloodClaims Pro — Health Alert", ""]
    lines.append(f"Time: {report['timestamp'][:16]}")
    lines.append(f"Issues: {len(issues)}")
    lines.append("")
    for i, issue in enumerate(issues[:4], 1):
        # Strip emoji for SMS readability
        clean = issue.split(" ", 1)[-1] if " " in issue else issue
        lines.append(f"{i}. {clean[:100]}")
    if len(issues) > 4:
        lines.append(f"+ {len(issues) - 4} more")
    lines.append("")
    lines.append("Dashboard: billy-floods.up.railway.app/health-dashboard")
    return "\n".join(lines)


def format_email_alert(report):
    """Aquila-style HTML email alert — detailed but scannable."""
    issues = report.get("critical_issues", [])
    checks = report.get("checks", [])

    issue_rows = "".join(
        f'<tr style="background:#fef2f2;"><td style="padding:.4rem .75rem;border:1px solid #fecaca;color:#991b1b;">{i}</td></tr>'
        for i in issues
    )

    check_rows = "".join(
        f'<tr style="background:{"#f0fdf4" if c["ok"] else "#fef2f2"};">'
        f'<td style="padding:.4rem .75rem;border:1px solid #e2e8f0;">{c.get("emoji", "")} {c["name"]}</td>'
        f'<td style="padding:.4rem .75rem;border:1px solid #e2e8f0;text-align:center;">{"✅" if c["ok"] else "❌"}</td>'
        f'<td style="padding:.4rem .75rem;border:1px solid #e2e8f0;font-size:.82rem;">{c.get("message", "")}</td></tr>'
        for c in checks
    )

    return f'''<!DOCTYPE html>
<html><body style="font-family:system-ui,sans-serif;max-width:680px;margin:0 auto;padding:1rem;">
<div style="background:linear-gradient(135deg,#0a1628,#1e3a5f);color:#fff;padding:1.5rem;border-radius:12px 12px 0 0;text-align:center;">
  <h1 style="margin:0;font-size:1.3rem;">🚨 Aquila Health Alert</h1>
  <p style="margin:.3rem 0 0;opacity:.7;font-size:.82rem;">{report["timestamp"]}</p>
</div>
<div style="background:#fff;border:1px solid #e2e8f0;border-top:none;padding:1.5rem;border-radius:0 0 12px 12px;">
  <h2 style="color:#991b1b;font-size:1rem;margin-top:0;">{len(issues)} Issue(s) Require Attention</h2>
  <table style="width:100%;border-collapse:collapse;">{issue_rows}</table>

  <h3 style="margin-top:1.5rem;font-size:.95rem;">All System Checks</h3>
  <table style="width:100%;border-collapse:collapse;font-size:.85rem;">
    <tr style="background:#f8fafc;"><th style="padding:.4rem .75rem;border:1px solid #e2e8f0;text-align:left;">Service</th><th style="padding:.4rem .75rem;border:1px solid #e2e8f0;">Status</th><th style="padding:.4rem .75rem;border:1px solid #e2e8f0;text-align:left;">Detail</th></tr>
    {check_rows}
  </table>

  <div style="margin-top:1.5rem;text-align:center;">
    <a href="https://billy-floods.up.railway.app/health-dashboard/" style="display:inline-block;padding:.75rem 1.5rem;background:linear-gradient(135deg,#06D6C7,#3B7BFF);color:#fff;text-decoration:none;border-radius:8px;font-weight:700;">🏥 Open Health Dashboard</a>
  </div>
  <p style="font-size:.7rem;color:#94a3b8;margin-top:1.5rem;">Completed in {report.get("elapsed_s", "?")}s by Aquila's automated monitor</p>
</div></body></html>'''


def format_daily_summary(report):
    """Aquila's morning briefing — casual, informative, Aquila-voiced."""
    checks = report.get("checks", [])
    ok_count = sum(1 for c in checks if c.get("ok", True))
    fail_count = len(checks) - ok_count
    issues = report.get("critical_issues", [])

    # Get claim stats
    db = sqlite3.connect(DB_PATH)
    claim_count = db.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
    open_count = db.execute("SELECT COUNT(*) FROM claims WHERE status != 'Closed'").fetchone()[0]
    pipeline = db.execute("SELECT COALESCE(SUM(total_estimate),0) FROM claims WHERE status != 'Closed'").fetchone()[0]
    photo_count = db.execute("SELECT COUNT(*) FROM photos WHERE deleted_at IS NULL").fetchone()[0]
    db.close()

    status_emoji = "✅" if report["all_ok"] else "⚠️"
    status_text = "All systems healthy" if report["all_ok"] else f"{fail_count} issue(s) need attention"

    lines = [f"🌅 Good morning! Aquila here with your daily summary.", ""]
    lines.append(f"📊 System Status: {status_emoji} {status_text}")
    lines.append(f"   {ok_count}/{len(checks)} checks passing")

    if issues:
        lines.append("")
        lines.append("🚨 Issues:")
        for issue in issues[:5]:
            lines.append(f"   • {issue}")

    lines.append("")
    lines.append(f"📋 Claims: {claim_count} total | {open_count} open | Pipeline: ${pipeline:,.0f}")
    lines.append(f"📸 Photos: {photo_count}")
    lines.append(f"⏰ Report time: {report['timestamp'][:16]}")
    lines.append("")
    lines.append("Dashboard: billy-floods.up.railway.app/health-dashboard")

    return "\n".join(lines)


# ── Alert senders ─────────────────────────────────────────────────────────────


def send_sms(message):
    """Send SMS via Twilio."""
    sid = _get_setting_local("twilio_account_sid")
    token = _get_setting_local("twilio_auth_token")
    from_num = _get_setting_local("twilio_from_number")
    to_num = _get_setting_local("admin_phone") or _get_setting_local("twilio_admin_phone")

    if not all([sid, token, from_num, to_num]):
        return False, "Twilio not configured"
    try:
        r = _req.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
            auth=(sid, token),
            data={"From": from_num, "To": to_num, "Body": message[:1500]},
            timeout=15,
        )
        if r.status_code == 201:
            return True, f"SMS sent to {to_num}"
        return False, f"Twilio error {r.status_code}"
    except Exception as e:
        return False, str(e)


def send_alert_email(subject, html):
    """Send email via SendGrid."""
    to = _get_setting_local("admin_report_email") or _get_setting_local("admin_email")
    if not to:
        return False, "No admin email configured"
    try:
        ok = send_email(to, subject, html)
        return ok, "Email sent" if ok else "SendGrid failed"
    except Exception as e:
        return False, str(e)


# ── Self-healing actions ──────────────────────────────────────────────────────


def attempt_self_heal(report):
    """Try to auto-fix issues Aquila can handle."""
    healed = []

    for check in report.get("checks", []):
        if check.get("ok"):
            continue

        # Disk space:flag for cleanup
        if check["name"] == "Disk Space" and check.get("used_pct", 0) > 85:
            # Can't auto-clean but can flag
            healed.append("💾 Disk space critical — flagged for manual cleanup")

        # Aquila brain missing: reinitialize
        if check["name"] == "Aquila Brain" and check.get("issues"):
            try:
                # Import the default brain content inline (same as willie.py)
                from routes.willie import _get_default_brain
                for key in ["brain_identity_md", "brain_soul_md", "brain_memory_md",
                            "brain_system_prompt", "brain_photo_prompt"]:
                    val = _get_default_brain(key)
                    if val:
                        set_setting(key, val)
                healed.append("🧠 Aquila brain files reinitialized to defaults")
            except Exception:
                pass

    for h in healed:
        logger.info(f"[SELF-HEAL] {h}")
    return healed


# ── Main cron loop ─────────────────────────────────────────────────────────────

_last_daily_date = None  # track which day we last ran the daily summary
_last_alert_hash = None  # suppress duplicate alerts
_last_alert_time = 0


def _alert_hash(issues):
    """Simple hash of issues to detect repeats."""
    return "|".join(sorted(issues[:5]))


def run_health_cycle():
    """Run one health check cycle. Called by the scheduler thread."""
    global _last_alert_hash, _last_alert_time

    logger.info("[AQUILA_CRON] Running health check cycle...")
    report = run_all_checks()

    total = len(report["checks"])
    failing = sum(1 for c in report["checks"] if not c.get("ok", True))
    logger.info(f"[AQUILA_CRON] {total - failing}/{total} passed, {failing} failing, {report['elapsed_s']}s")

    # Attempt self-healing
    healed = attempt_self_heal(report)
    if healed:
        report["healed"] = healed
        logger.info(f"[AQUILA_CRON] Self-healed: {healed}")

    if report["all_ok"]:
        logger.info("[AQUILA_CRON] All systems healthy — no alerts needed")
        return

    # There are issues — decide whether to alert
    issues = report.get("critical_issues", [])
    current_hash = _alert_hash(issues)
    now = time.time()

    # Cooldown: don't re-alert for the same issues within cooldown period
    if current_hash == _last_alert_hash and (now - _last_alert_time) < ALERT_COOLDOWN:
        logger.info("[AQUILA_CRON] Same issues as last alert — skipping (cooldown)")
        return

    _last_alert_hash = current_hash
    _last_alert_time = now

    # Send alerts
    sms_msg = format_sms_alert(report)
    email_html = format_email_alert(report)
    subject = f'🚨 FloodClaims Pro — {len(issues)} Issue(s) Need Attention'

    sms_ok, sms_detail = send_sms(sms_msg)
    email_ok, email_detail = send_alert_email(subject, email_html)

    logger.info(f"[AQUILA_CRON] SMS: {sms_detail} | Email: {email_detail}")

    # Log to activity log
    try:
        db = sqlite3.connect(_db())
        db.execute(
            "INSERT INTO activity_log (claim_id, actor, action) VALUES (?, ?, ?)",
            (0, "Aquila Monitor", f"Alert: {len(issues)} issues. SMS: {'sent' if sms_ok else 'failed'}. Email: {'sent' if email_ok else 'failed'}")
        )
        db.commit()
        db.close()
    except Exception as e:
        logger.error(f"[AQUILA_CRON] Failed to log alert: {e}")


def run_daily_summary():
    """Run the daily morning summary."""
    logger.info("[AQUILA_CRON] Running daily summary...")
    report = run_all_checks()

    summary_text = format_daily_summary(report)

    # Log to activity log
    try:
        db = sqlite3.connect(_db())
        db.execute(
            "INSERT INTO activity_log (claim_id, actor, action) VALUES (?, ?, ?)",
            (0, "Aquila Daily", summary_text[:500])
        )
        db.commit()
        db.close()
    except Exception as e:
        logger.error(f"[AQUILA_CRON] Failed to log daily summary: {e}")

    # Send email summary
    subject = f'🌅 Aquila Daily Summary — {datetime.datetime.now().strftime("%B %d, %Y")}'
    email_html = format_email_alert(report)  # Reuse the email template
    ok, detail = send_alert_email(subject, email_html)
    logger.info(f"[AQUILA_CRON] Daily email: {detail}")


def _get_et_hour():
    """Get current hour in Eastern Time."""
    utc_now = datetime.datetime.utcnow()
    et_hour = (utc_now.hour + ET_OFFSET) % 24
    return et_hour, utc_now.minute


def scheduler_loop():
    """
    Main scheduler thread. Runs forever.
    - Health checks every HEALTH_CHECK_INTERVAL seconds
    - Daily summary at DAILY_SUMMARY_HOUR ET
    """
    global _last_daily_date

    logger.info(
        f"[AQUILA_CRON] Scheduler started — "
        f"health check every {HEALTH_CHECK_INTERVAL // 60}min | "
        f"daily summary at {DAILY_SUMMARY_HOUR}:00 ET"
    )

    # Run initial health check after 30s (let app finish booting)
    time.sleep(30)
    try:
        run_health_cycle()
    except Exception as e:
        logger.error(f"[AQUILA_CRON] Initial health check failed: {e}")

    while True:
        try:
            time.sleep(60)  # Wake up every minute to check time

            # Health check on interval
            now = time.time()
            last_health = float(get_setting("health_check_last_run", "0"))
            if now - last_health >= HEALTH_CHECK_INTERVAL:
                set_setting("health_check_last_run", str(int(now)))
                try:
                    run_health_cycle()
                except Exception as e:
                    logger.error(f"[AQUILA_CRON] Health check error: {e}")

            # Daily summary at the right ET time
            et_hour, et_minute = _get_et_hour()
            today = datetime.date.today().isoformat()

            if (
                et_hour == DAILY_SUMMARY_HOUR
                and 0 <= et_minute < 5
                and _last_daily_date != today
            ):
                _last_daily_date = today
                try:
                    run_daily_summary()
                except Exception as e:
                    logger.error(f"[AQUILA_CRON] Daily summary error: {e}")

        except Exception as e:
            logger.error(f"[AQUILA_CRON] Scheduler loop error: {e}", exc_info=True)
            time.sleep(60)  # Don't tight-loop on errors


# ── Public API ────────────────────────────────────────────────────────────────


def start_scheduler():
    """Call from app.py to start Aquila's background scheduler."""
    t = threading.Thread(target=scheduler_loop, daemon=True, name="aquila-cron")
    t.start()
    logger.info("[AQUILA_CRON] Background scheduler thread started")
    return t
