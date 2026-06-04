"""Routes for pipeline blueprint."""

from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from models.database import get_db
from utils.auth_decorators import login_required

bp = Blueprint("pipeline", __name__)

@bp.route('/pipeline')
@login_required
def pipeline():
    db = get_db()
    if session['role'] == 'admin':
        claims = db.execute('''
            SELECT c.*, u.name as adjuster_name
            FROM claims c LEFT JOIN users u ON c.adjuster_id=u.id
            ORDER BY c.kanban_order ASC, c.created_at DESC
        ''').fetchall()
    else:
        claims = db.execute('''
            SELECT c.*, u.name as adjuster_name
            FROM claims c LEFT JOIN users u ON c.adjuster_id=u.id
            WHERE c.adjuster_id=?
            ORDER BY c.kanban_order ASC, c.created_at DESC
        ''', (session['user_id'],)).fetchall()
    cols = ['New', 'In Progress', 'Submitted', 'Closed']
    board = {col: [c for c in claims if c['status'] == col] for col in cols}
    return render_template('pipeline.html', board=board, cols=cols)




@bp.route('/pipeline/move', methods=['POST'])
@login_required
def pipeline_move():
    """AJAX: move a claim to a new status column."""
    data      = request.get_json() or {}
    claim_id  = data.get('claim_id')
    new_status = data.get('status')
    valid = ['New', 'In Progress', 'Submitted', 'Closed']
    if not claim_id or new_status not in valid:
        return jsonify({'ok': False, 'error': 'Invalid'}), 400
    db = get_db()
    claim = db.execute('SELECT * FROM claims WHERE id=?', (claim_id,)).fetchone()
    if not claim:
        return jsonify({'ok': False, 'error': 'Not found'}), 404
    if session['role'] != 'admin' and claim['adjuster_id'] != session['user_id']:
        return jsonify({'ok': False, 'error': 'Forbidden'}), 403
    old_status = claim['status']
    db.execute('UPDATE claims SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?',
               (new_status, claim_id))
    db.commit()
    # Fire status-change notification
    if old_status != new_status and claim['client_email']:
        notify_client_status_change(claim, new_status)
        _log_notification(claim_id, 'status_change',
                          claim['client_email'],
                          f'Claim {claim["claim_number"]} moved to {new_status}')
    return jsonify({'ok': True})


# ─────────────────────────────────────────────────────────────────────────────
# FEATURE 2: INSPECTION SCHEDULER
# ─────────────────────────────────────────────────────────────────────────────


