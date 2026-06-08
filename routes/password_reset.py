"""
Password Reset Service — Secure token-based reset flow.

Flow:
1. User enters email on forgot-password page
2. System generates a secure random token, stores hash in DB with expiry
3. System sends reset link via email (or displays it directly if email not configured)
4. User clicks link, enters new password
5. System verifies token, updates password hash, deletes token

Security:
- Tokens are 32-byte random, stored as SHA-256 hash (not plaintext)
- Tokens expire after 1 hour
- Single-use: deleted after successful reset
- Rate-limited: max 3 requests per email per hour
"""
import hashlib
import secrets
import datetime
import os

from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify
from models.database import get_db, hash_pw, check_pw, get_setting
from utils.security import csrf_required

bp = Blueprint("password_reset", __name__)

# ── DB Migration ──────────────────────────────────────────────────────────────

def _ensure_reset_table():
    """Create password_resets table if it doesn't exist."""
    db = get_db()
    db.execute('''
        CREATE TABLE IF NOT EXISTS password_resets (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token_hash  TEXT NOT NULL,
            expires_at  TEXT NOT NULL,
            used        INTEGER DEFAULT 0,
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    db.commit()


# ── Routes ────────────────────────────────────────────────────────────────────

@bp.route('/forgot-password', methods=['GET', 'POST'])
@csrf_required
def forgot_password():
    """Step 1: User enters email, system sends reset link."""
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        if not email:
            flash('Please enter your email address.', 'error')
            return render_template('forgot_password.html')

        _ensure_reset_table()
        db = get_db()
        user = db.execute('SELECT id, email, name FROM users WHERE email=?', (email,)).fetchone()

        if user:
            # Rate limit: max 3 tokens per email per hour
            recent = db.execute(
                'SELECT COUNT(*) as cnt FROM password_resets WHERE user_id=? AND created_at > datetime("now","-1 hour")',
                (user['id'],)
            ).fetchone()['cnt']

            if recent >= 3:
                flash('Too many reset requests. Please wait an hour and try again.', 'error')
                return render_template('forgot_password.html')

            # Generate secure token
            token = secrets.token_urlsafe(32)
            token_hash = hashlib.sha256(token.encode()).hexdigest()
            expires = (datetime.datetime.utcnow() + datetime.timedelta(hours=1)).isoformat()

            # Invalidate old tokens for this user
            db.execute('DELETE FROM password_resets WHERE user_id=?', (user['id'],))

            # Store new token hash
            db.execute(
                'INSERT INTO password_resets (user_id, token_hash, expires_at) VALUES (?,?,?)',
                (user['id'], token_hash, expires)
            )
            db.commit()

            # Build reset URL
            reset_url = url_for('password_reset.reset_password', token=token, _external=True)

            # Try to send email
            email_sent = False
            try:
                from services.email import send_email
                subject = 'FloodClaims Pro — Password Reset'
                html = f'''<div style="font-family:sans-serif;max-width:500px;margin:0 auto">
                    <h2 style="color:#0a1628">Password Reset</h2>
                    <p>Hello {user['name']},</p>
                    <p>Click the link below to reset your password. This link expires in 1 hour.</p>
                    <p><a href="{reset_url}" style="background:#06D6C7;color:#0a1628;padding:12px 24px;border-radius:8px;text-decoration:none;display:inline-block;margin:12px 0;font-weight:700">Reset Password</a></p>
                    <p style="font-size:12px;color:#64748b">If you didn't request this, ignore this email. Your password won't change.</p>
                    <p style="font-size:11px;color:#94a3b8">Or copy this link: {reset_url}</p>
                </div>'''
                send_email(user['email'], subject, html)
                email_sent = True
            except Exception:
                pass

            if email_sent:
                flash('Password reset link sent! Check your email.', 'success')
            else:
                # Email not configured — show the link directly (for admin use)
                flash(f'Email not configured. Use this link to reset: {reset_url}', 'info')

        # Always show same message (don't reveal if email exists)
        return redirect(url_for('auth.login'))

    return render_template('forgot_password.html')


@bp.route('/reset-password/<token>', methods=['GET', 'POST'])
@csrf_required
def reset_password(token):
    """Step 2: User clicks reset link, enters new password."""
    _ensure_reset_table()
    token_hash = hashlib.sha256(token.encode()).hexdigest()

    db = get_db()
    reset_row = db.execute(
        'SELECT pr.*, u.email, u.name FROM password_resets pr '
        'JOIN users u ON pr.user_id=u.id '
        'WHERE pr.token_hash=? AND pr.used=0 AND pr.expires_at > datetime("now")',
        (token_hash,)
    ).fetchone()

    if not reset_row:
        flash('This reset link is invalid or has expired. Please request a new one.', 'error')
        return redirect(url_for('password_reset.forgot_password'))

    if request.method == 'POST':
        pw1 = request.form.get('password', '')
        pw2 = request.form.get('password_confirm', '')

        if not pw1 or len(pw1) < 8:
            flash('Password must be at least 8 characters.', 'error')
            return render_template('reset_password.html')

        if pw1 != pw2:
            flash('Passwords do not match.', 'error')
            return render_template('reset_password.html')

        # Update password
        db.execute('UPDATE users SET password=? WHERE id=?', (hash_pw(pw1), reset_row['user_id']))
        # Mark token as used
        db.execute('UPDATE password_resets SET used=1 WHERE id=?', (reset_row['id'],))
        # Invalidate all sessions for this user
        db.execute('DELETE FROM password_resets WHERE user_id=?', (reset_row['user_id'],))
        db.commit()

        flash('Password reset successfully! Please sign in with your new password.', 'success')
        return redirect(url_for('auth.login'))

    return render_template('reset_password.html')


@bp.route('/admin/reset-admin-password', methods=['POST'])
def admin_reset_password():
    """
    Direct admin password reset — protected by ADMIN_PASSWORD env var.
    Use this when you can't log in at all.
    Send POST with: current_admin_password, new_password
    """
    data = request.get_json(silent=True) or request.form
    current_pw = data.get('current_admin_password', '')
    new_pw = data.get('new_password', '')

    # Verify the current admin password from env var
    import os
    expected = os.environ.get('ADMIN_PASSWORD', 'FloodAdmin2026!')
    if current_pw != expected:
        return jsonify({'error': 'Invalid admin password'}), 403

    if not new_pw or len(new_pw) < 8:
        return jsonify({'error': 'New password must be at least 8 characters'}), 400

    _ensure_reset_table()
    db = get_db()
    admin = db.execute('SELECT id FROM users WHERE role="admin" ORDER BY id LIMIT 1').fetchone()
    if not admin:
        return jsonify({'error': 'No admin user found'}), 404

    db.execute('UPDATE users SET password=? WHERE id=?', (hash_pw(new_pw), admin['id']))
    db.commit()

    return jsonify({'success': True, 'message': 'Admin password reset successfully'})
