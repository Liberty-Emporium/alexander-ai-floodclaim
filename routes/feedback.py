"""Routes for feedback blueprint."""

from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify
from models.database import get_db
from utils.auth_decorators import login_required, admin_required

bp = Blueprint("feedback", __name__)

@bp.route('/api/health/feedback-tables')
def feedback_tables_health():
    """Check if feedback tables exist (no auth required for monitoring)."""
    import sqlite3
    db = sqlite3.connect(DB_PATH)
    tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'feedback_%'").fetchall()]
    return jsonify({'tables': tables, 'ok': len(tables) == 4})



@bp.route('/api/dashboard/feedback')
@login_required
def dashboard_feedback_api():
    """Return all feedback sessions for the dashboard widget."""
    db = get_db()
    sessions = db.execute('''
        SELECT fc.id, fc.client_name, fc.client_email, fc.title, fc.status,
               fc.priority, fc.admin_read, fc.created_at, fc.updated_at,
               (SELECT COUNT(*) FROM feedback_messages fm WHERE fm.conversation_id=fc.id AND fm.role='user') as message_count,
           (SELECT fm.content FROM feedback_messages fm WHERE fm.conversation_id=fc.id AND fm.role='user' ORDER BY fm.id DESC LIMIT 1) as last_message
        FROM feedback_conversations fc
        ORDER BY fc.updated_at DESC
        LIMIT 50
    ''').fetchall()
    # Get poll state
    poll = db.execute('SELECT last_check FROM feedback_poll_state WHERE id=1').fetchone()
    last_check = poll['last_check'] if poll else ''
    result = {
        'sessions': [dict(s) for s in sessions],
        'last_check': last_check,
        'unread_count': sum(1 for s in sessions if not s['admin_read'])
    }
    return jsonify(result)



@bp.route('/admin/feedback/conversations/<int:conv_id>/read', methods=['POST'])
@login_required
def feedback_mark_read(conv_id):
    """Mark a conversation as read by admin."""
    if session.get('role') not in ('admin', 'manager'):
        abort(403)
    db = get_db()
    db.execute('UPDATE feedback_conversations SET admin_read=1 WHERE id=?', (conv_id,))
    db.commit()
    return jsonify({'ok': True})



@bp.route('/admin/feedback/clients', methods=['GET', 'POST'])
@login_required
def feedback_clients():
    """List or create feedback clients."""
    if session.get('role') != 'admin':
        abort(403)
    db = get_db()
    if request.method == 'POST':
        name = request.json.get('name', '').strip()
        email = request.json.get('email', '').strip()
        app_name = request.json.get('app_name', '').strip()
        token = secrets.token_urlsafe(24)
        db.execute('INSERT INTO feedback_clients (name, email, token, app_name) VALUES (?,?,?,?)',
                   (name, email, token, app_name))
        db.commit()
        return jsonify({'ok': True, 'token': token, 'url': f'/feedback/{token}'})
    clients = db.execute('SELECT * FROM feedback_clients ORDER BY created_at DESC').fetchall()
    return jsonify([dict(c) for c in clients])



@bp.route('/feedback/<token>')
def feedback_client_portal(token):
    """Client-facing feedback portal — no login required."""
    db = get_db()
    client = db.execute('SELECT * FROM feedback_clients WHERE token=? AND status="active"', (token,)).fetchone()
    if not client:
        return 'Invalid or expired link. Please contact Jay Alexander for a new link.', 404
    # Get or create a conversation for this client
    conv = db.execute('SELECT * FROM feedback_conversations WHERE client_token=? ORDER BY updated_at DESC LIMIT 1',
                      (token,)).fetchone()
    conv_id = conv['id'] if conv else None
    return render_template('feedback_portal.html', client=client, conv_id=conv_id)



