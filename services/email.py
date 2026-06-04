"""Email service — SendGrid integration.

Extracted from app.py Phase 2 (lines 676-707).
"""
import os
import sys

SENDGRID_OK = False
try:
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import Mail
    SENDGRID_OK = True
except Exception:
    pass


def _get_setting(key, default=''):
    """Read a setting from the DB, falling back to default."""
    import sqlite3
    DB_PATH = os.environ.get('DB_PATH') or '/data/floodclaims.db'
    try:
        db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
        row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        db.close()
        return row['value'] if row else default
    except Exception:
        return default


def send_email(to_email, subject, html_body):
    """Send email via SendGrid if configured, else log."""
    sg_key = _get_setting('sendgrid_api_key') or os.environ.get('SENDGRID_API_KEY', '')
    from_email = _get_setting('from_email') or os.environ.get('FROM_EMAIL', 'noreply@floodclaimpro.com')
    if not sg_key or not SENDGRID_OK:
        print(f'[EMAIL] To: {to_email} | Subject: {subject} | (SendGrid not configured)')
        return False
    try:
        msg = Mail(from_email=from_email, to_emails=to_email, subject=subject, html_content=html_body)
        SendGridAPIClient(sg_key).send(msg)
        return True
    except Exception as e:
        print(f'SendGrid error: {e}')
        return False


def notify_client_status_change(claim, new_status):
    """Email client when claim status changes."""
    if not claim.get('client_email'):
        return
    subject = f'FloodClaims Pro — Your Claim {claim["claim_number"]} Update'
    html = f'''<div style="font-family:sans-serif;max-width:600px;margin:0 auto">
        <h2 style="color:#0a1628">FloodClaims Pro Update</h2>
        <p>Hello {claim["client_name"]},</p>
        <p>Your flood damage claim <strong>{claim["claim_number"]}</strong> has been updated.</p>
        <p style="background:#f0fdf4;padding:12px;border-radius:8px;border-left:4px solid #10b981">
            <strong>New Status: {new_status}</strong></p>
        <p>If you have questions, please contact your adjuster directly.</p>
        <hr style="margin:24px 0;border:none;border-top:1px solid #e2e8f0">
        <p style="font-size:12px;color:#94a3b8">FloodClaims Pro · Professional Flood Damage Assessment</p>
    </div>'''
    send_email(claim['client_email'], subject, html)
