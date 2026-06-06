"""Routes for schedule blueprint."""

from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify
from models.database import get_db
from utils.auth_decorators import login_required
from utils.security import csrf_required
from services.email import send_email
import json
import datetime

bp = Blueprint("schedule", __name__)

def _log_notification(claim_id, notif_type, recipient, message):
    """Log a sent notification to the DB."""
    try:
        db = get_db()
        db.execute(
            'INSERT INTO notifications_log (claim_id, type, recipient, message) VALUES (?,?,?,?)',
            (claim_id, notif_type, recipient, message)
        )
        db.commit()
    except Exception as e:
        print(f'_log_notification error: {e}')




@bp.route('/schedule')
@login_required
def schedule():
    db = get_db()
    if session['role'] == 'admin':
        slots = db.execute('''
            SELECT s.*, c.claim_number, c.client_name, c.property_address,
                   u.name as adjuster_name
            FROM inspection_slots s
            JOIN claims c ON s.claim_id = c.id
            LEFT JOIN users u ON s.adjuster_id = u.id
            ORDER BY s.slot_date ASC, s.slot_time ASC
        ''').fetchall()
        claims = db.execute('''
            SELECT id, claim_number, client_name, property_address, adjuster_id
            FROM claims WHERE status != 'Closed' ORDER BY created_at DESC
        ''').fetchall()
        adjusters = db.execute('SELECT id, name FROM users ORDER BY name').fetchall()
    else:
        slots = db.execute('''
            SELECT s.*, c.claim_number, c.client_name, c.property_address,
                   u.name as adjuster_name
            FROM inspection_slots s
            JOIN claims c ON s.claim_id = c.id
            LEFT JOIN users u ON s.adjuster_id = u.id
            WHERE s.adjuster_id = ?
            ORDER BY s.slot_date ASC, s.slot_time ASC
        ''', (session['user_id'],)).fetchall()
        claims = db.execute('''
            SELECT id, claim_number, client_name, property_address, adjuster_id
            FROM claims WHERE adjuster_id=? AND status != 'Closed'
            ORDER BY created_at DESC
        ''', (session['user_id'],)).fetchall()
        adjusters = []
    today = datetime.date.today().isoformat()
    return render_template('schedule.html', slots=slots, claims=claims,
                           adjusters=adjusters, today=today)




@bp.route('/schedule/add', methods=['POST'])
@login_required
@csrf_required
def schedule_add():
    db        = get_db()
    claim_id  = request.form.get('claim_id')
    slot_date = request.form.get('slot_date')
    slot_time = request.form.get('slot_time')
    notes     = request.form.get('notes', '')
    adj_id    = request.form.get('adjuster_id') or session['user_id']
    if not claim_id or not slot_date or not slot_time:
        flash('Claim, date and time are required.', 'error')
        return redirect(url_for('schedule.schedule'))
    db.execute('''
        INSERT INTO inspection_slots (claim_id, adjuster_id, slot_date, slot_time, notes)
        VALUES (?,?,?,?,?)
    ''', (claim_id, adj_id, slot_date, slot_time, notes))
    # Update claim's sched fields
    db.execute('UPDATE claims SET sched_date=?, sched_time=?, inspection_date=?, updated_at=CURRENT_TIMESTAMP WHERE id=?',
               (slot_date, slot_time, slot_date, claim_id))
    db.commit()
    _log_activity(claim_id, f'Inspection scheduled: {slot_date} at {slot_time}')
    claim = db.execute('SELECT * FROM claims WHERE id=?', (claim_id,)).fetchone()
    # Email adjuster
    adj = db.execute('SELECT * FROM users WHERE id=?', (adj_id,)).fetchone()
    if adj and adj['email']:
        send_email(adj['email'],
                   f'Inspection Scheduled — {claim["claim_number"]}',
                   f'<p>An inspection has been scheduled for claim <strong>{claim["claim_number"]}</strong> '
                   f'({claim["client_name"]}) on <strong>{slot_date} at {slot_time}</strong>.</p>'
                   f'<p>Property: {claim["property_address"]}</p>'
                   f'<p>Notes: {notes or "None"}</p>')
    flash(f'Inspection scheduled for {slot_date} at {slot_time}.', 'success')
    return redirect(url_for('schedule.schedule'))




@bp.route('/schedule/<int:slot_id>/status', methods=['POST'])
@login_required
def schedule_update_status(slot_id):
    new_status = request.form.get('status', 'pending')
    db = get_db()
    db.execute('UPDATE inspection_slots SET status=? WHERE id=?', (new_status, slot_id))
    db.commit()
    return redirect(url_for('schedule.schedule'))




@bp.route('/schedule/<int:slot_id>/delete', methods=['POST'])
@login_required
@csrf_required
def schedule_delete(slot_id):
    db = get_db()
    db.execute('DELETE FROM inspection_slots WHERE id=?', (slot_id,))
    db.commit()
    flash('Inspection removed.', 'success')
    return redirect(url_for('schedule.schedule'))


# ─────────────────────────────────────────────────────────────────────────────
# FEATURE 3: AUTOMATED NOTIFICATIONS
# ─────────────────────────────────────────────────────────────────────────────



@bp.route('/notifications')
@login_required
def notifications():
    db = get_db()
    if session['role'] == 'admin':
        logs = db.execute('''
            SELECT n.*, c.claim_number, c.client_name
            FROM notifications_log n
            LEFT JOIN claims c ON n.claim_id = c.id
            ORDER BY n.sent_at DESC LIMIT 200
        ''').fetchall()
    else:
        logs = db.execute('''
            SELECT n.*, c.claim_number, c.client_name
            FROM notifications_log n
            LEFT JOIN claims c ON n.claim_id = c.id
            WHERE c.adjuster_id = ?
            ORDER BY n.sent_at DESC LIMIT 200
        ''', (session['user_id'],)).fetchall()
    return render_template('notifications.html', logs=logs)




@bp.route('/notifications/send', methods=['POST'])
@login_required
@csrf_required
def notifications_send():
    """Manually send a status update email for a claim."""
    claim_id = request.form.get('claim_id')
    message  = request.form.get('message', '').strip()
    db = get_db()
    claim = db.execute('SELECT * FROM claims WHERE id=?', (claim_id,)).fetchone()
    if not claim:
        flash('Claim not found.', 'error')
        return redirect(url_for('schedule.notifications'))
    if not claim['client_email']:
        flash('No client email on this claim.', 'error')
        return redirect(url_for('schedule.notifications'))
    subject = f'Update on your FloodClaims — {claim["claim_number"]}'
    html = f'''<div style="font-family:sans-serif;max-width:600px;margin:0 auto">
        <h2 style="color:#0a1628">FloodClaims Pro — Claim Update</h2>
        <p>Hello {claim["client_name"]},</p>
        <p>{message}</p>
        <p style="background:#f0fdf4;padding:12px;border-radius:8px;border-left:4px solid #10b981">
            <strong>Claim #: {claim["claim_number"]}</strong><br>
            Status: {claim["status"]}
        </p>
        <hr style="margin:24px 0;border:none;border-top:1px solid #e2e8f0">
        <p style="font-size:12px;color:#94a3b8">FloodClaims Pro · Professional Flood Damage Assessment</p>
    </div>'''
    sent = send_email(claim['client_email'], subject, html)
    if sent:
        _log_notification(claim_id, 'manual', claim['client_email'], message)
        flash(f'Notification sent to {claim["client_email"]}.', 'success')
    else:
        flash('Email not sent — configure SendGrid in Settings.', 'error')
    return redirect(url_for('schedule.notifications'))


# ─────────────────────────────────────────────────────────────────────────────
# FEATURE 4: NFIP COMPLIANCE CHECKLIST
# ─────────────────────────────────────────────────────────────────────────────

NFIP_CHECKLIST = [
    ('policy',         'Policy number recorded'),
    ('policy_type',    'Policy type identified (Building/Contents/Both)'),
    ('coverage_bldg',  'Building coverage amount documented'),
    ('coverage_cont',  'Contents coverage amount documented'),
    ('deductible',     'Deductible amount confirmed'),
    ('flood_date',     'Date of loss (flood date) recorded'),
    ('flood_source',   'Flood source documented (river, storm, sewer, etc.)'),
    ('water_cat',      'Water category assigned (Cat 1/2/3)'),
    ('water_class',    'Water class assigned (Class 1–4)'),
    ('water_depth',    'Water depth documented (inches)'),
    ('water_removed',  'Date water removed recorded'),
    ('inspection',     'Inspection date scheduled/completed'),
    ('flood_zone',     'FEMA flood zone looked up'),
    ('fema_map',       'FEMA map panel number recorded'),
    ('photos',         'Damage photos uploaded'),
    ('rooms',          'Room-by-room scope documented'),
    ('estimate',       'AI estimate generated'),
    ('mortgage',       'Mortgage company/loan # recorded (if applicable)'),
]



