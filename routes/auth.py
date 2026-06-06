"""Routes for auth blueprint."""

from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from models.database import get_db, hash_pw, check_pw, get_setting
from utils.auth_decorators import login_required
from utils.security import csrf_required
from utils.helpers import is_rate_limited

bp = Blueprint("auth", __name__)

@bp.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('auth.dashboard'))
    return redirect(url_for('auth.login'))



@bp.route('/login', methods=['GET', 'POST'])
@csrf_required
def login():
    if 'user_id' in session:
        return redirect(url_for('auth.dashboard'))
    if request.method == 'POST':
        ip    = request.remote_addr or 'unknown'
        if is_rate_limited(f'login:{ip}', max_calls=5, window=60):
            flash('Too many login attempts. Please wait a minute and try again.', 'error')
            return render_template('login.html')
        email = request.form.get('email', '').strip().lower()
        pw    = request.form.get('password', '')
        db    = get_db()
        user  = db.execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()
        if not user or not check_pw(pw, user['password']):
            flash('Invalid email or password.', 'error')
            return render_template('login.html')
        # Check if user is active (managers/admins can be deactivated by admin)
        if not user.get('is_active', 1):
            flash('Your account has been deactivated. Contact your administrator.', 'error')
            return render_template('login.html')
        # Transparent bcrypt upgrade: if stored hash is legacy sha256, re-hash now
        if BCRYPT_OK and user['password'] and not user['password'].startswith('$2'):
            db.execute('UPDATE users SET password=? WHERE id=?',
                       (hash_pw(pw), user['id']))
            db.commit()
        session.permanent = True
        session['user_id'] = user['id']
        session['email']   = user['email']
        session['name']    = user['name']
        session['role']    = user['role']
        return redirect(url_for('auth.dashboard'))
    return render_template('login.html')



@bp.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('auth.login'))



@bp.route('/dashboard')
@login_required
def dashboard():
    db = get_db()
    # Search + filter params
    q          = request.args.get('q', '').strip()
    f_status   = request.args.get('status', '')
    f_adjuster = request.args.get('adjuster_id', '')
    f_priority = request.args.get('priority', '')
    f_date_from= request.args.get('date_from', '')
    f_date_to  = request.args.get('date_to', '')

    base_sql = '''SELECT c.*, u.name as adjuster_name
        FROM claims c LEFT JOIN users u ON c.adjuster_id=u.id WHERE 1=1'''
    params = []
    if session['role'] != 'admin':
        base_sql += ' AND c.adjuster_id=?'
        params.append(session['user_id'])
    if q:
        base_sql += ' AND (c.client_name LIKE ? OR c.claim_number LIKE ? OR c.property_address LIKE ?)'
        like = f'%{q}%'
        params += [like, like, like]
    if f_status:
        base_sql += ' AND c.status=?'
        params.append(f_status)
    if f_adjuster and session['role'] == 'admin':
        base_sql += ' AND c.adjuster_id=?'
        params.append(f_adjuster)
    if f_priority:
        base_sql += ' AND c.priority=?'
        params.append(f_priority)
    if f_date_from:
        base_sql += ' AND c.flood_date >= ?'
        params.append(f_date_from)
    if f_date_to:
        base_sql += ' AND c.flood_date <= ?'
        params.append(f_date_to)
    base_sql += ' ORDER BY c.created_at DESC'
    claims = db.execute(base_sql, params).fetchall()

    # Stats always from full set (no filters)
    if session['role'] == 'admin':
        all_claims = db.execute('SELECT status, total_estimate FROM claims').fetchall()
    else:
        all_claims = db.execute('SELECT status, total_estimate FROM claims WHERE adjuster_id=?',
                                (session['user_id'],)).fetchall()
    stats = {
        'total':       len(all_claims),
        'new':         sum(1 for c in all_claims if c['status'] == 'New'),
        'in_progress': sum(1 for c in all_claims if c['status'] == 'In Progress'),
        'submitted':   sum(1 for c in all_claims if c['status'] == 'Submitted'),
        'closed':      sum(1 for c in all_claims if c['status'] == 'Closed'),
        'pipeline':    sum(c['total_estimate'] for c in all_claims if c['status'] != 'Closed'),
    }
    adjusters = db.execute('SELECT * FROM users ORDER BY name').fetchall() \
                if session['role'] == 'admin' else []
    return render_template('dashboard.html', claims=claims, stats=stats, adjusters=adjusters,
                           q=q, f_status=f_status, f_adjuster=f_adjuster,
                           f_priority=f_priority, f_date_from=f_date_from, f_date_to=f_date_to)


