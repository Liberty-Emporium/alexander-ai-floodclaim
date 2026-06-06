"""Routes for rooms blueprint."""

from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify
from models.database import get_db
from utils.auth_decorators import login_required
from utils.security import csrf_required

bp = Blueprint("rooms", __name__)

@bp.route('/claims/<int:claim_id>/room/add', methods=['POST'])
@login_required
@csrf_required
def add_room(claim_id):
    db    = get_db()
    claim = db.execute('SELECT id FROM claims WHERE id=?', (claim_id,)).fetchone()
    if not claim:
        flash('Claim not found.', 'error')
        return redirect(url_for('auth.dashboard'))
    name = request.form.get('room_name', '').strip()
    if name:
        db.execute('INSERT INTO rooms (claim_id, name) VALUES (?,?)', (claim_id, name))
        db.commit()
        _log_activity(claim_id, f'Room added: {name}')
    return redirect(url_for('claim_detail', claim_id=claim_id))



@bp.route('/rooms/<int:room_id>/delete', methods=['POST'])
@login_required
@csrf_required
def delete_room(room_id):
    db   = get_db()
    room = db.execute('SELECT * FROM rooms WHERE id=?', (room_id,)).fetchone()
    if not room:
        return redirect(url_for('auth.dashboard'))
    claim_id = room['claim_id']
    # Soft-delete room and its line items
    db.execute('UPDATE rooms SET deleted_at=CURRENT_TIMESTAMP WHERE id=?', (room_id,))
    db.execute('UPDATE line_items SET deleted_at=CURRENT_TIMESTAMP WHERE room_id=?', (room_id,))
    db.execute('UPDATE photos SET room_id=NULL WHERE room_id=?', (room_id,))
    db.commit()
    recalc_claim(claim_id)
    _log_activity(claim_id, f'Room soft-deleted: {room["name"]}')
    return redirect(url_for('claim_detail', claim_id=claim_id))



@bp.route('/rooms/<int:room_id>/item/add', methods=['POST'])
@login_required
@csrf_required
def add_item(room_id):
    db        = get_db()
    room      = db.execute('SELECT * FROM rooms WHERE id=?', (room_id,)).fetchone()
    if not room:
        return redirect(url_for('auth.dashboard'))
    desc      = request.form.get('description', '')
    qty       = float(request.form.get('quantity', 1) or 1)
    unit      = request.form.get('unit', 'ea')
    unit_cost = float(request.form.get('unit_cost', 0) or 0)
    total     = qty * unit_cost
    db.execute(
        'INSERT INTO line_items (room_id, description, quantity, unit, unit_cost, total) '
        'VALUES (?,?,?,?,?,?)',
        (room_id, desc, qty, unit, unit_cost, total))
    db.commit()
    recalc_claim(room['claim_id'])
    _log_activity(room['claim_id'], f'Line item added: {desc} x{qty} {unit} @${unit_cost:.2f}')
    return redirect(url_for('claim_detail', claim_id=room['claim_id']))



@bp.route('/items/<int:item_id>/delete', methods=['POST'])
@login_required
@csrf_required
def delete_item(item_id):
    db   = get_db()
    item = db.execute(
        'SELECT r.claim_id FROM line_items li JOIN rooms r ON li.room_id=r.id WHERE li.id=?',
        (item_id,)).fetchone()
    db.execute('UPDATE line_items SET deleted_at=CURRENT_TIMESTAMP WHERE id=?', (item_id,))
    db.commit()
    if item:
        recalc_claim(item['claim_id'])
        _log_activity(item['claim_id'], 'Line item soft-deleted')
    return jsonify({'ok': True})


# ── Feedback: Dashboard API & Client Portal ────────────────────────────────────


