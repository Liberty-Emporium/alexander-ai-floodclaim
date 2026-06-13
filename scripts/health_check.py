#!/usr/bin/env python3
"""
FloodClaims Pro — Automated Health Check + Alert Script
=========================================================
Runs all health checks and sends alerts if anything is broken.

Used by: Hermes cron job (every 5 minutes)
Sends: Twilio SMS + SendGrid email on failure
"""
import os
import sys
import json
import datetime

# ── Bootstrap Flask app context ──────────────────────────────────────────────
DATA_DIR = os.environ.get('RAILWAY_VOLUME_MOUNT_PATH', '/data')
DB_PATH = os.environ.join(DATA_DIR, 'floodclaim.db') if False else os.path.join(DATA_DIR, 'floodclaim.db')
os.environ['DB_PATH'] = DB_PATH

# Add project to path
PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_DIR)

from services.health_monitor import run_all_checks
from services.email import send_email


def _get_setting(key, default=''):
    import sqlite3
    try:
        db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
        row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        db.close()
        return row['value'] if row else default
    except Exception:
        return default


def send_twilio_alert(message):
    """Send SMS alert via Twilio."""
    import base64 as _b64
    import requests as _req

    sid = _get_setting('twilio_account_sid')
    token = _get_setting('twilio_auth_token')
    from_num = _get_setting('twilio_from_number')
    to_num = _get_setting('admin_phone') or _get_setting('twilio_admin_phone')

    if not sid or not token or not from_num or not to_num:
        print('[ALERT] Twilio not configured — skipping SMS')
        return False

    try:
        r = _req.post(
            f'https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json',
            auth=(sid, token),
            data={'From': from_num, 'To': to_num, 'Body': message[:1500]},
            timeout=15,
        )
        if r.status_code == 201:
            print(f'[ALERT] SMS sent to {to_num}')
            return True
        else:
            print(f'[ALERT] Twilio error: {r.status_code} — {r.text[:100]}')
            return False
    except Exception as e:
        print(f'[ALERT] Twilio send error: {e}')
        return False


def send_email_alert(subject, body_html):
    """Send email alert via SendGrid."""
    to_email = _get_setting('admin_report_email') or _get_setting('admin_email')
    if not to_email:
        print('[ALERT] No admin email configured — skipping email alert')
        return False
    return send_email(to_email, subject, body_html)


def format_alert_message(report):
    """Format a concise alert message for SMS."""
    issues = report.get('critical_issues', [])
    lines = ['🚨 FloodClaims Pro Health Alert', '']
    lines.append(f'Time: {report["timestamp"]}')
    lines.append(f'Issues: {len(issues)}')
    lines.append('')
    for issue in issues[:5]:  # Max 5 issues in SMS
        lines.append(f'• {issue[:120]}')
    if len(issues) > 5:
        lines.append(f'...and {len(issues) - 5} more')
    lines.append('')
    lines.append('Dashboard: https://billy-floods.up.railway.app/health-dashboard/')
    return '\n'.join(lines)


def format_alert_email(report):
    """Format a detailed HTML email alert."""
    issues = report.get('critical_issues', [])
    checks = report.get('checks', [])

    issues_html = ''.join(f'<li style="margin:.3rem 0;color:#991b1b;">{i}</li>' for i in issues)
    checks_html = ''.join(
        f'<tr style="background:{"#f0fdf4" if c["ok"] else "#fef2f2"};">'
        f'<td style="padding:.5rem .75rem;border:1px solid #e2e8f0;">{c.get("emoji","")} {c["name"]}</td>'
        f'<td style="padding:.5rem .75rem;border:1px solid #e2e8f0;">{"✅ OK" if c["ok"] else "❌ FAIL"}</td>'
        f'<td style="padding:.5rem .75rem;border:1px solid #e2e8f0;font-size:.82rem;">{c.get("message","")}</td>'
        f'</tr>'
        for c in checks
    )

    return f'''<!DOCTYPE html>
<html>
<body style="font-family:system-ui,sans-serif;max-width:700px;margin:0 auto;padding:1rem;">
<div style="background:linear-gradient(135deg,#0a1628,#1e3a5f);color:#fff;padding:1.5rem;border-radius:12px 12px 0 0;text-align:center;">
  <h1 style="margin:0;font-size:1.3rem;">🚨 FloodClaims Pro Health Alert</h1>
  <p style="margin:.5rem 0 0;opacity:.8;font-size:.85rem;">{report["timestamp"]}</p>
</div>
<div style="background:#fff;border:1px solid #e2e8f0;border-top:none;padding:1.5rem;border-radius:0 0 12px 12px;">
  <h2 style="color:#991b1b;font-size:1.1rem;">{len(issues)} Issue(s) Found</h2>
  <ul style="padding-left:1.25rem;">{issues_html}</ul>

  <h3 style="margin-top:1.5rem;font-size:.95rem;">All Health Checks</h3>
  <table style="width:100%;border-collapse:collapse;font-size:.85rem;">
    <tr style="background:#f8fafc;">
      <th style="padding:.5rem .75rem;border:1px solid #e2e8f0;text-align:left;">Service</th>
      <th style="padding:.5rem .75rem;border:1px solid #e2e8f0;text-align:left;">Status</th>
      <th style="padding:.5rem .75rem;border:1px solid #e2e8f0;text-align:left;">Detail</th>
    </tr>
    {checks_html}
  </table>

  <div style="margin-top:1.5rem;text-align:center;">
    <a href="https://billy-floods.up.railway.app/health-dashboard/"
       style="display:inline-block;padding:.75rem 1.5rem;background:linear-gradient(135deg,#06D6C7,#3B7BFF);color:#fff;text-decoration:none;border-radius:8px;font-weight:700;">
      🏥 Open Health Dashboard
    </a>
  </div>

  <hr style="margin:1.5rem 0;border:none;border-top:1px solid #e2e8f0;">
  <p style="font-size:.72rem;color:#94a3b8;">FloodClaims Pro · Automated Health Monitor · Completed in {report.get("elapsed_s", "?")}s</p>
</div>
</body>
</html>'''


def run():
    """Main entry: run checks, alert if issues found."""
    print(f"🏥 FloodClaims Pro Health Check — {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    report = run_all_checks()

    total = len(report['checks'])
    failing = sum(1 for c in report['checks'] if not c.get('ok', True))
    print(f"   {total - failing}/{total} checks passed | {failing} failing | {report['elapsed_s']}s")

    if report['all_ok']:
        print("   ✅ All systems healthy — no alerts sent")
        return True

    # There are issues — send alerts
    print(f"   ❌ {len(report['critical_issues'])} critical issue(s) — sending alerts")

    sms_msg = format_alert_message(report)
    email_html = format_alert_email(report)
    subject = f'🚨 FloodClaims Pro — {len(report["critical_issues"])} Issue(s) Found'

    # Send both channels
    sms_ok = send_twilio_alert(sms_msg)
    email_ok = send_email_alert(subject, email_html)

    if sms_ok or email_ok:
        print("   📬 Alert sent successfully")
    else:
        print("   ⚠️ Failed to send any alerts — check Twilio/SendGrid config")

    # Also save alert to activity log
    try:
        import sqlite3
        db = sqlite3.connect(DB_PATH)
        db.execute(
            "INSERT INTO activity_log (claim_id, actor, action) VALUES (?, ?, ?)",
            (0, 'Health Monitor', f'Alert: {len(report["critical_issues"])} issues — {"; ".join(i[:80] for i in report["critical_issues"][:3])}')
        )
        db.commit()
        db.close()
    except Exception as e:
        print(f"   ⚠️ Could not save to activity log: {e}")

    return False


if __name__ == '__main__':
    success = run()
    exit(0 if success else 1)
