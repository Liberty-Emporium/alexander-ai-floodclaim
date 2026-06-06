"""Routes for admin blueprint."""

from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify, abort
from models.database import get_db, get_setting, set_setting, hash_pw, check_pw
from utils.auth_decorators import login_required, admin_required
from utils.security import csrf_required
from services.email import send_email
import json
import datetime
import os
import requests as _req

bp = Blueprint("admin", __name__)

@bp.route('/admin/settings', methods=['GET', 'POST'])
@login_required
@admin_required
@csrf_required
def settings():
    if request.method == 'POST':
        # API key is managed via Railway env var OPENROUTER_API_KEY only — not stored in DB
        # Aquila Chat is always locked to OWL Alpha — not user-configurable
        # Vision model selection
        ai_vision_model = request.form.get('ai_vision_model', '').strip()
        if ai_vision_model:
            set_setting('ai_vision_model', ai_vision_model)
        # Fallback model for chat (also locked to OWL Alpha compatible models)
        fallback_model = request.form.get('ai_fallback_model', '').strip()
        if fallback_model:
            set_setting('ai_fallback_model', fallback_model)
        # Integration keys (SendGrid, Stripe, etc. — these are safe in DB)
        for key in ['sendgrid_api_key', 'from_email', 'stripe_secret_key',
                    'stripe_publishable_key', 'google_maps_api_key',
                    'twilio_account_sid', 'twilio_auth_token', 'twilio_from_number',
                    'admin_report_email']:
            val = request.form.get(key, '').strip()
            if val:
                set_setting(key, val)
        flash('Settings saved!', 'success')
        return redirect(url_for('admin.settings'))

    env_key_set       = bool(OPENROUTER_KEY)
    current_model     = get_setting('ai_model', 'openai/gpt-4o-mini')
    current_vision_model = get_setting('ai_vision_model', 'openrouter/auto')
    current_chat_model   = get_setting('ai_chat_model') or get_setting('ai_model', 'openrouter/owl-alpha')
    current_fallback  = get_setting('ai_fallback_model', 'anthropic/claude-sonnet-4-5')
    return render_template('settings.html',
                           env_key_set=env_key_set,
                           current_model=current_model,
                           current_vision_model=current_vision_model,
                           current_chat_model=current_chat_model,
                           current_fallback=current_fallback)

# ── Free Models API ─────────────────────────────────────────────────────────────



@bp.route('/admin/api/free-models')
@login_required
@admin_required
def api_free_models():
    """Fetch latest free models from OpenRouter with 1024+ context."""
    import urllib.request as _req
    import json as _json
    try:
        req = _req.Request('https://openrouter.ai/api/v1/models', headers={
            'User-Agent': 'FloodClaims-Pro/1.0'
        })
        resp = _req.urlopen(req, timeout=10)
        data = _json.loads(resp.read())
        models = data.get('data', data) if isinstance(data, dict) else data
        free_models = []
        for m in models:
            mid = m.get('id', '')
            pricing = m.get('pricing', {})
            prompt_price = pricing.get('prompt', '0')
            # Check if free (prompt price is 0 or very close to 0)
            try:
                is_free = float(prompt_price) <= 0
            except (ValueError, TypeError):
                is_free = False
            if not is_free:
                continue
            # Check context length >= 1024
            ctx = m.get('context_length', 0)
            try:
                ctx = int(ctx)
            except (ValueError, TypeError):
                ctx = 0
            if ctx < 1024:
                continue
            # Check if vision-capable
            architecture = m.get('architecture', {})
            modality = architecture.get('modality', m.get('modality', ''))
            input_mods = architecture.get('input_modalities', [])
            is_vision = ('image' in str(modality).lower() or 
                        'image' in str(input_mods).lower() or
                        'vision' in mid.lower())
            free_models.append({
                'id': mid,
                'name': mid.split('/')[-1].replace('-', ' ').title(),
                'provider': mid.split('/')[0] if '/' in mid else 'Unknown',
                'context': ctx,
                'vision': is_vision,
                'prompt_price': prompt_price,
                'completion_price': pricing.get('completion', '0'),
            })
        # Sort by context length descending
        free_models.sort(key=lambda x: x['context'], reverse=True)
        return jsonify({'ok': True, 'models': free_models, 'count': len(free_models)})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500




@bp.route('/admin/api/init-brain', methods=['POST'])
@login_required
@admin_required
def api_init_brain():
    """Initialize brain files with default content."""
    import os

    identity_path = os.path.join(os.path.dirname(__file__), '..', 'brain', 'IDENTITY.md')
    soul_path = os.path.join(os.path.dirname(__file__), '..', 'brain', 'SOUL.md')
    memory_path = os.path.join(os.path.dirname(__file__), '..', 'brain', 'MEMORY.md')

    # Read from files if they exist, otherwise use built-in defaults
    identity = _read_brain_file(identity_path, 'brain_identity_md')
    soul = _read_brain_file(soul_path, 'brain_soul_md')
    memory = _read_brain_file(memory_path, 'brain_memory_md')
    system = _get_default_brain('brain_system_prompt')
    photo = _get_default_brain('brain_photo_prompt')

    set_setting('brain_identity_md', identity)
    set_setting('brain_soul_md', soul)
    set_setting('brain_memory_md', memory)
    set_setting('brain_system_prompt', system)
    set_setting('brain_photo_prompt', photo)

    return jsonify({
        'ok': True,
        'message': 'Brain files initialized',
        'sizes': {
            'identity': len(identity),
            'soul': len(soul),
            'memory': len(memory),
            'system': len(system),
            'photo_prompt': len(photo)
        }
    })




@bp.route('/admin/api/test-photo-analysis', methods=['POST'])
@login_required
@admin_required
def api_test_photo_analysis():
    """Test photo analysis with custom prompt from brain training."""
    if 'photo' not in request.files:
        return jsonify({'ok': False, 'error': 'No photo uploaded'}), 400
    file = request.files['photo']
    test_prompt = request.form.get('test_prompt', '')
    if not test_prompt:
        test_prompt = None  # Will use default in ai_describe_photo_detailed

    import tempfile, os
    suffix = '.' + file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else '.jpg'
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    file.save(tmp.name)
    tmp.close()

    key = get_setting('openrouter_api_key') or OPENROUTER_KEY
    if not key:
        os.unlink(tmp.name)
        return jsonify({'ok': False, 'error': 'OpenRouter API key not configured'}), 400

    model = get_setting('ai_vision_model') or get_setting('ai_model', 'openrouter/auto')
    text_only = {'openrouter/owl-alpha', 'openrouter/owl', 'openai/o3-mini', 'deepseek/deepseek-r1'}
    if model in text_only:
        model = 'openrouter/auto'

    try:
        # Use custom prompt if provided
        if test_prompt:
            import base64 as _b64
            with open(tmp.name, 'rb') as f:
                img_b64 = _b64.b64encode(f.read()).decode()
            ext = suffix.replace('.', '')
            mime = f'image/{ext}' if ext != 'jpg' else 'image/jpeg'
            r = _req.post(
                'https://openrouter.ai/api/v1/chat/completions',
                headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
                json={
                    'model': model,
                    'messages': [{
                        'role': 'user',
                        'content': [
                            {'type': 'text', 'text': test_prompt},
                            {'type': 'image_url', 'image_url': {'url': f'data:{mime};base64,{img_b64}'}}
                        ]
                    }],
                    'max_tokens': 2000
                }, timeout=60)
            result = r.json()['choices'][0]['message']['content']
        else:
            result = ai_describe_photo_detailed(tmp.name, key, model)
        os.unlink(tmp.name)
        return jsonify({'ok': True, 'analysis': result})
    except Exception as e:
        try: os.unlink(tmp.name)
        except: pass
        return jsonify({'ok': False, 'error': str(e)}), 500


# ── Admin: Team Management ────────────────────────────────────────────────────



@bp.route('/admin/team')
@login_required
@admin_required
def team():
    db = get_db()
    users = db.execute(
        'SELECT u.*, (SELECT COUNT(*) FROM claims WHERE adjuster_id=u.id) as claim_count '
        'FROM users u ORDER BY u.name').fetchall()
    adjusters = db.execute(
        '''SELECT u.*,
           (SELECT COUNT(*) FROM claims WHERE adjuster_id=u.id AND status NOT IN ('Closed','Submitted')) as active_claims,
           (SELECT COUNT(*) FROM claims WHERE adjuster_id=u.id AND status IN ('Closed','Submitted')) as completed_claims,
           COALESCE(u.is_active, 1) as is_active
           FROM users u WHERE u.role='adjuster' ORDER BY u.name''').fetchall()
    return render_template('team.html', users=users, adjusters=adjusters)



@bp.route('/admin/team/add', methods=['POST'])
@login_required
@admin_required
@csrf_required
def add_team_member():
    db    = get_db()
    email = request.form.get('email', '').strip().lower()
    name  = request.form.get('name', '').strip()
    pw    = request.form.get('password', '').strip()
    role  = request.form.get('role', 'adjuster')
    if role not in ('adjuster', 'manager', 'admin'):
        role = 'adjuster'
    if not email or not pw:
        flash('Email and password required.', 'error')
        return redirect(url_for('admin.team'))
    ok, err = _validate_password(pw)
    if not ok:
        flash(err, 'error')
        return redirect(url_for('admin.team'))
    try:
        db.execute('INSERT INTO users (email, name, password, role, is_active) VALUES (?,?,?,?,1)',
                   (email, name, hash_pw(pw), role))
        db.commit()
        flash(f'Team member {name} added as {role}!', 'success')
    except sqlite3.IntegrityError:
        flash('Email already exists.', 'error')
    return redirect(url_for('admin.team'))



@bp.route('/admin/team/<int:user_id>/edit', methods=['POST'])
@login_required
@admin_required
@csrf_required
def edit_team_member(user_id):
    db    = get_db()
    email = request.form.get('email', '').strip().lower()
    name  = request.form.get('name', '').strip()
    pw    = request.form.get('password', '').strip()
    role  = request.form.get('role', 'adjuster')
    if role not in ('adjuster', 'manager', 'admin'):
        role = 'adjuster'
    if not email:
        flash('Email is required.', 'error')
        return redirect(url_for('admin.team'))
    if pw:
        ok, err = _validate_password(pw)
        if not ok:
            flash(err, 'error')
            return redirect(url_for('admin.team'))
    # Only admin can change role to/from admin
    if session.get('role') != 'admin':
        # Managers can't create/edit admins — preserve existing role if trying to set admin
        if role == 'admin':
            role = 'manager'
    try:
        if pw:
            db.execute('UPDATE users SET email=?, name=?, password=?, role=? WHERE id=?',
                       (email, name, hash_pw(pw), role, user_id))
        else:
            db.execute('UPDATE users SET email=?, name=?, role=? WHERE id=?',
                       (email, name, role, user_id))
        db.commit()
        flash(f'Team member {name} updated!', 'success')
    except sqlite3.IntegrityError:
        flash('Email already exists.', 'error')
    return redirect(url_for('admin.team'))



@bp.route('/admin/team/<int:user_id>/delete', methods=['POST'])
@login_required
@admin_required
@csrf_required
def delete_team_member(user_id):
    if user_id == session['user_id']:
        flash("Can't delete yourself.", 'error')
        return redirect(url_for('admin.team'))
    db = get_db()
    # Don't allow deleting the last admin
    target = db.execute('SELECT role FROM users WHERE id=?', (user_id,)).fetchone()
    if target and target['role'] == 'admin':
        admin_count = db.execute("SELECT COUNT(*) as c FROM users WHERE role='admin'").fetchone()['c']
        if admin_count <= 1:
            flash("Can't delete the last admin.", 'error')
            return redirect(url_for('admin.team'))
    db.execute('UPDATE claims SET adjuster_id=NULL WHERE adjuster_id=?', (user_id,))
    db.execute('DELETE FROM willie_conversations WHERE user_id=?', (user_id,))
    db.execute('DELETE FROM users WHERE id=?', (user_id,))
    db.commit()
    flash('Team member removed.', 'success')
    return redirect(url_for('admin.team'))



@bp.route('/admin/team/<int:user_id>/deactivate', methods=['POST'])
@login_required
@admin_required
@csrf_required
def deactivate_adjuster(user_id):
    db = get_db()
    user = db.execute('SELECT name, role FROM users WHERE id=?', (user_id,)).fetchone()
    if user and user['role'] == 'adjuster':
        db.execute('UPDATE users SET is_active=0 WHERE id=?', (user_id,))
        db.commit()
        flash(f'Adjuster {user["name"] or user_id} deactivated.', 'success')
    return redirect(url_for('admin.team'))



@bp.route('/admin/team/<int:user_id>/reactivate', methods=['POST'])
@login_required
@admin_required
@csrf_required
def reactivate_adjuster(user_id):
    db = get_db()
    user = db.execute('SELECT name FROM users WHERE id=?', (user_id,)).fetchone()
    db.execute('UPDATE users SET is_active=1 WHERE id=?', (user_id,))
    db.commit()
    flash(f'Adjuster {user["name"] if user else user_id} reactivated.', 'success')
    return redirect(url_for('admin.team'))


# _migrate_feedback_tables() now called lazily in get_db() via _ensure_db_initialized()

# ── Willie AI Chat ───────────────────────────────────────────────────────────



# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC: Become a Flood Adjuster — Training, Exams & Application
# ═══════════════════════════════════════════════════════════════════════════════



@bp.route('/admin/recruit', methods=['GET', 'POST'])
@login_required
@admin_required
def recruit():
    db = get_db()
    if request.method == 'POST':
        app_type = request.form.get('app_type', '')
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip().lower()
        phone = request.form.get('phone', '').strip()
        state = request.form.get('state', '').strip()
        if not name or not email:
            flash('Name and email are required.', 'error')
            return redirect(url_for('admin.recruit'))
        if app_type == 'adjuster':
            license_number = request.form.get('license_number', '').strip()
            if not license_number:
                flash('License number is required for adjuster applications.', 'error')
                return redirect(url_for('admin.recruit'))
            try:
                db.execute(
                    'INSERT INTO adjuster_applications (name, email, phone, license_number, state, status) VALUES (?,?,?,?,?,?)',
                    (name, email, phone, license_number, state, 'pending'))
                db.commit()
                flash(f'Adjuster application submitted for {name}. Review and approve below.', 'success')
            except Exception as e:
                flash(f'Error submitting application: {e}', 'error')
        elif app_type == 'contractor':
            license_type = request.form.get('license_type', '').strip()
            license_number = request.form.get('license_number', '').strip()
            experience = request.form.get('experience_years', '').strip()
            try:
                db.execute(
                    'INSERT INTO contractor_applications (name, email, phone, license_type, license_number, state, experience_years, status) VALUES (?,?,?,?,?,?,?,?)',
                    (name, email, phone, license_type, license_number, state, experience, 'pending'))
                db.commit()
                flash(f'Contractor application submitted for {name}. They will need training and certification.', 'success')
            except Exception as e:
                flash(f'Error submitting application: {e}', 'error')
        return redirect(url_for('admin.recruit'))

    adjuster_apps = db.execute(
        'SELECT * FROM adjuster_applications ORDER BY created_at DESC').fetchall()
    contractor_apps = db.execute(
        'SELECT * FROM contractor_applications ORDER BY created_at DESC').fetchall()
    return render_template('recruit.html', adjuster_apps=adjuster_apps, contractor_apps=contractor_apps)




@bp.route('/admin/recruit/adjuster/<int:app_id>/approve', methods=['POST'])
@login_required
@admin_required
@csrf_required
def approve_adjuster_application(app_id):
    db = get_db()
    app = db.execute('SELECT * FROM adjuster_applications WHERE id=?', (app_id,)).fetchone()
    if not app:
        flash('Application not found.', 'error')
        return redirect(url_for('admin.recruit'))
    # Check if user already exists
    existing = db.execute('SELECT id FROM users WHERE email=?', (app['email'],)).fetchone()
    if existing:
        flash('A user with this email already exists.', 'error')
        return redirect(url_for('admin.recruit'))
    # Create user account
    import secrets as _secrets
    temp_pw = _secrets.token_urlsafe(10)
    db.execute(
        'INSERT INTO users (email, name, password, role) VALUES (?,?,?,?)',
        (app['email'], app['name'], hash_pw(temp_pw), 'adjuster'))
    # Mark application approved
    db.execute(
        'UPDATE adjuster_applications SET status=?, reviewed_at=CURRENT_TIMESTAMP, reviewed_by=? WHERE id=?',
        ('approved', session['user_id'], app_id))
    db.commit()
    flash(f'✅ {app["name"]} approved and added to team as Adjuster. Temp password: {temp_pw}', 'success')
    return redirect(url_for('admin.recruit'))




@bp.route('/admin/recruit/contractor/<int:app_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def contractor_detail(app_id):
    db = get_db()
    if request.method == 'POST':
        action = request.form.get('action', '')
        if action == 'approve_training':
            db.execute("UPDATE contractor_applications SET status='training', reviewed_at=CURRENT_TIMESTAMP, reviewed_by=? WHERE id=?",
                       (session['user_id'], app_id))
            db.commit()
            flash('Contractor approved for training.', 'success')
        elif action == 'update_progress':
            progress = int(request.form.get('progress', 0))
            db.execute('UPDATE contractor_applications SET progress=? WHERE id=?', (progress, app_id))
            db.commit()
            flash(f'Progress updated to {progress}%.', 'success')
        elif action == 'certify':
            # Convert contractor to adjuster
            app = db.execute('SELECT * FROM contractor_applications WHERE id=?', (app_id,)).fetchone()
            if app:
                existing = db.execute('SELECT id FROM users WHERE email=?', (app['email'],)).fetchone()
                if not existing:
                    import secrets as _secrets
                    temp_pw = _secrets.token_urlsafe(10)
                    db.execute(
                        'INSERT INTO users (email, name, password, role) VALUES (?,?,?,?)',
                        (app['email'], app['name'], hash_pw(temp_pw), 'adjuster'))
                db.execute("UPDATE contractor_applications SET status='certified', progress=100, reviewed_at=CURRENT_TIMESTAMP, reviewed_by=? WHERE id=?",
                           (session['user_id'], app_id))
                db.commit()
                flash(f'✅ {app["name"]} certified and added to team as Adjuster!', 'success')
        return redirect(url_for('contractor_detail', app_id=app_id))

    app = db.execute('SELECT * FROM contractor_applications WHERE id=?', (app_id,)).fetchone()
    if not app:
        flash('Application not found.', 'error')
        return redirect(url_for('admin.recruit'))
    return render_template('contractor_detail.html', app=app)



# ── Recruitment Invitations ─────────────────────────────────────────────────────



@bp.route('/admin/recruit/send-invite', methods=['POST'])
@login_required
@admin_required
@csrf_required
def send_recruit_invite():
    """Send a recruitment invitation email to a prospective adjuster."""
    to_email = request.form.get('invite_email', '').strip().lower()
    invite_name = request.form.get('invite_name', '').strip()
    if not to_email:
        flash('Email address is required.', 'error')
        return redirect(url_for('admin.recruit'))

    sg_key = get_setting('sendgrid_api_key') or os.environ.get('SENDGRID_API_KEY', '')
    if not sg_key or not SENDGRID_OK:
        flash('⚠️ SendGrid not configured. Set your API key in AI Integration settings first.', 'error')
        return redirect(url_for('admin.recruit'))

    from_email = get_setting('from_email') or os.environ.get('FROM_EMAIL', '')
    if not from_email:
        flash('⚠️ No "From" email set. Set it below before sending invitations.', 'error')
        return redirect(url_for('admin.recruit'))

    join_url = request.host_url.rstrip('/') + url_for('become_agent')
    name_greeting = f"Hi {invite_name}," if invite_name else "Hi there,"

    html_body = f'''<div style="font-family:'Plus Jakarta Sans',sans-serif;max-width:600px;margin:0 auto;color:#1e293b">
        <div style="background:linear-gradient(135deg,#06D6C7,#3B7BFF);padding:28px;border-radius:16px 16px 0 0;text-align:center;">
            <div style="font-size:40px;margin-bottom:8px;">🌊</div>
            <h1 style="color:#fff;margin:0;font-size:1.6rem;font-weight:800;">FloodClaims Pro</h1>
            <p style="color:rgba(255,255,255,.85);margin:4px 0 0;font-size:.85rem;">Professional Flood Damage Assessment</p>
        </div>
        <div style="padding:28px;background:#fff;border:1px solid #e2e8f0;border-top:none;border-radius:0 0 16px 16px;">
            <p style="font-size:1rem;margin:0 0 12px;">{name_greeting}</p>
            <p style="font-size:.9rem;line-height:1.7;color:#475569;margin:0 0 16px;">
                You\'ve been invited to join a flood damage adjustment team on <strong>FloodClaims Pro</strong>.
                Whether you\'re an experienced adjuster or looking to get licensed, we make it easy to get started.
            </p>
            <div style="background:#f0f9ff;border:1px solid #bae6fd;border-radius:10px;padding:14px 18px;margin-bottom:20px;">
                <div style="font-size:.75rem;font-weight:700;text-transform:uppercase;letter-spacing:.04em;color:#0369a1;margin-bottom:8px;">What you can do</div>
                <ul style="font-size:.82rem;color:#475569;margin:0;padding-left:18px;line-height:1.8;">
                    <li>Manage flood damage claims end-to-end</li>
                    <li>AI-powered photo damage analysis</li>
                    <li>NFIP &amp; FEMA compliance tools</li>
                    <li>Free training & certification pathway</li>
                    <li>Work from anywhere</li>
                </ul>
            </div>
            <div style="text-align:center;margin-bottom:20px;">
                <a href="{join_url}" style="display:inline-block;padding:14px 32px;background:linear-gradient(135deg,#06D6C7,#3B7BFF);color:#fff;text-decoration:none;border-radius:10px;font-weight:700;font-size:.95rem;">🚀 Get Started — It\'s Free</a>
            </div>
            <p style="font-size:.78rem;color:#94a3b8;margin:0;line-height:1.6;">
                If the button doesn\'t work, copy this link: <br>
                <a href="{join_url}" style="color:#3B7BFF;word-break:break-all;">{join_url}</a>
            </p>
            <hr style="margin:20px 0;border:none;border-top:1px solid #e2e8f0;">
            <p style="font-size:.72rem;color:#94a3b8;margin:0;">
                FloodClaims Pro · Professional Flood Damage Assessment Platform<br>
                You received this email because an admin invited you to join their team.
            </p>
        </div>
    </div>'''

    sent = send_email(to_email, "🌊 You\'re Invited — Join FloodClaims Pro", html_body)
    if sent:
        flash(f'✅ Invitation sent to {to_email}', 'success')
    else:
        flash(f'❌ Failed to send invitation to {to_email}. Check SendGrid configuration.', 'error')
    return redirect(url_for('admin.recruit'))


# ── Aquila Chat ────────────────────────────────────────────────────────────────



@bp.route('/admin/willie/brain', methods=['GET'])
@login_required
@admin_required
def willie_brain_get():
    """Fetch Aquila's brain files from local database settings."""
    db = get_db()
    keys = ['brain_identity_md', 'brain_soul_md', 'brain_memory_md', 'brain_system_prompt', 'brain_photo_prompt']
    result = {}
    for k in keys:
        row = db.execute('SELECT value FROM settings WHERE key=?', (k,)).fetchone()
        result[k] = row['value'] if row else ''
    return jsonify(result)




@bp.route('/admin/willie/brain/update', methods=['POST'])
@login_required
@admin_required
@csrf_required
def willie_brain_update():
    """Save Aquila's brain files to local database settings."""
    brain_keys = ['brain_identity_md', 'brain_soul_md', 'brain_memory_md', 'brain_system_prompt']
    for key in brain_keys:
        val = request.form.get(key, '')
        set_setting(key, val)
    # Photo analysis training prompt
    photo_prompt = request.form.get('brain_photo_prompt', '')
    if photo_prompt:
        set_setting('brain_photo_prompt', photo_prompt)
    return jsonify({'ok': True, 'message': 'Brain files saved!', 'updated': brain_keys})


# ── Admin: Chat Bubble Settings ───────────────────────────────────────────────



@bp.route('/admin/settings/data')
@login_required
@admin_required
def settings_data():
    """Return all settings as JSON (for AJAX loading of brain files etc.)."""
    db = get_db()
    rows = db.execute('SELECT key, value FROM settings').fetchall()
    return jsonify({r['key']: r['value'] for r in rows})




@bp.route('/admin/settings/save', methods=['POST'])
@login_required
@admin_required
@csrf_required
def save_setting():
    """Generic single-setting save endpoint."""
    key = request.form.get('setting_key', '').strip()
    value = request.form.get('setting_value', '').strip()
    if not key:
        flash('Setting key missing.', 'error')
        return redirect(request.referrer or url_for('admin.settings'))
    set_setting(key, value)
    flash(f'Setting "{key}" saved.', 'success')
    return redirect(request.referrer or url_for('admin.settings'))




@bp.route('/admin/settings/chat-bubble', methods=['POST'])
@login_required
@admin_required
@csrf_required
def save_chat_bubble():
    """Save chat bubble appearance settings."""
    set_setting('bubble_bot_name', request.form.get('bubble_bot_name', 'Aquila').strip())
    set_setting('bubble_greeting', request.form.get('bubble_greeting', '').strip())
    set_setting('bubble_emoji_icon', request.form.get('bubble_emoji_icon', '🌊').strip())

    # Handle icon upload
    icon_file = request.files.get('bubble_icon_upload')
    if icon_file and icon_file.filename:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(icon_file.read()))
        img = img.convert('RGBA')
        img.thumbnail((64, 64), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        buf.seek(0)
        import base64
        b64 = base64.b64encode(buf.read()).decode()
        set_setting('bubble_icon_data', f'data:image/png;base64,{b64}')
        set_setting('bubble_icon_type', 'upload')
    else:
        # If no upload, check if emoji was selected
        emoji = request.form.get('bubble_emoji_icon', '').strip()
        if emoji and not get_setting('bubble_icon_data'):
            set_setting('bubble_icon_type', 'emoji')
            set_setting('bubble_emoji_icon', emoji)

    flash('Chat bubble settings saved!', 'success')
    return redirect(url_for('admin.settings'))




@bp.route('/admin/feedback')
@login_required
def feedback_studio():
    """Client Feedback Studio — AI-powered requirement gathering."""
    if session.get('role') not in ('admin', 'manager'):
        abort(403)
    db = get_db()
    convs = db.execute(
        'SELECT * FROM feedback_conversations ORDER BY updated_at DESC LIMIT 100'
    ).fetchall()
    return render_template('feedback_studio.html', conversations=convs)



@bp.route('/admin/feedback/conversations/list')
@login_required
def feedback_list_conversations():
    db = get_db()
    convs = db.execute(
        'SELECT id, title, client_name, client_email, created_at FROM feedback_conversations ORDER BY updated_at DESC LIMIT 100'
    ).fetchall()
    return jsonify([dict(c) for c in convs])



@bp.route('/admin/feedback/conversations', methods=['POST'])
@login_required
def feedback_new_conversation():
    if session.get('role') not in ('admin', 'manager'):
        abort(403)
    db = get_db()
    cur = db.execute(
        'INSERT INTO feedback_conversations (user_id, client_name, client_email) VALUES (?,?,?)',
        (session['user_id'], '', '')
    )
    db.commit()
    return jsonify({'id': cur.lastrowid, 'title': 'Feedback Session'})



@bp.route('/admin/feedback/conversations/<int:conv_id>')
@login_required
def feedback_get_conversation(conv_id):
    db = get_db()
    conv = db.execute('SELECT * FROM feedback_conversations WHERE id=?', (conv_id,)).fetchone()
    if not conv:
        return jsonify({'error': 'not found'}), 404
    msgs = db.execute(
        'SELECT role,content,created_at FROM feedback_messages WHERE conversation_id=? ORDER BY id',
        (conv_id,)
    ).fetchall()
    return jsonify({'conversation': dict(conv), 'messages': [dict(m) for m in msgs]})



@bp.route('/admin/feedback/conversations/<int:conv_id>', methods=['DELETE'])
@login_required
def feedback_delete_conversation(conv_id):
    db = get_db()
    db.execute('DELETE FROM feedback_messages WHERE conversation_id=?', (conv_id,))
    db.execute('DELETE FROM feedback_conversations WHERE id=?', (conv_id,))
    db.commit()
    return jsonify({'ok': True})



@bp.route('/admin/feedback/conversations/<int:conv_id>/meta', methods=['POST'])
@login_required
def feedback_update_meta(conv_id):
    """Update client name, email, title for a feedback conversation."""
    if session.get('role') not in ('admin', 'manager'):
        abort(403)
    data = request.get_json(silent=True) or {}
    db = get_db()
    if 'client_name' in data:
        db.execute('UPDATE feedback_conversations SET client_name=?, updated_at=CURRENT_TIMESTAMP WHERE id=?',
                   (data['client_name'], conv_id))
    if 'client_email' in data:
        db.execute('UPDATE feedback_conversations SET client_email=?, updated_at=CURRENT_TIMESTAMP WHERE id=?',
                   (data['client_email'], conv_id))
    if 'title' in data:
        db.execute('UPDATE feedback_conversations SET title=?, updated_at=CURRENT_TIMESTAMP WHERE id=?',
                   (data['title'], conv_id))
    if 'summary' in data:
        db.execute('UPDATE feedback_conversations SET summary=?, updated_at=CURRENT_TIMESTAMP WHERE id=?',
                   (data['summary'], conv_id))
    db.commit()
    return jsonify({'ok': True})



@bp.route('/admin/feedback/conversations/<int:conv_id>/messages', methods=['POST'])
@login_required
def feedback_save_message(conv_id):
    db = get_db()
    data = request.get_json(silent=True) or {}
    role = data.get('role', 'user')
    content = data.get('content', '').strip()
    if not content:
        return jsonify({'error': 'content required'}), 400
    db.execute('INSERT INTO feedback_messages (conversation_id, role, content) VALUES (?,?,?)',
               (conv_id, role, content))
    if role == 'user':
        title = content[:60] + ('...' if len(content) > 60 else '')
        db.execute('UPDATE feedback_conversations SET title=?, updated_at=CURRENT_TIMESTAMP WHERE id=?',
                   (title, conv_id))
    else:
        db.execute('UPDATE feedback_conversations SET updated_at=CURRENT_TIMESTAMP WHERE id=?', (conv_id,))
    db.commit()
    return jsonify({'ok': True})



@bp.route('/admin/feedback/chat', methods=['POST'])
@login_required
def feedback_chat():
    """AI chat endpoint for feedback studio."""
    data = request.get_json(silent=True) or {}
    message = data.get('message', '').strip()
    history = data.get('history', [])
    conv_id = data.get('conversation_id')

    if not message:
        return jsonify({'error': 'message required'}), 400

    # Build messages for OpenRouter
    messages = [{'role': 'system', 'content': FEEDBACK_SYSTEM_PROMPT}]

    # Add conversation history
    for msg in history:
        role = msg.get('role', 'user')
        if role in ('user', 'assistant'):
            messages.append({'role': role, 'content': msg.get('content', '')})

    # Add current message
    messages.append({'role': 'user', 'content': message})

    # Call OpenRouter
    api_key = os.environ.get('OPENROUTER_API_KEY', '') or get_setting('openrouter_api_key')
    if not api_key:
        return jsonify({'error': 'OpenRouter API key not configured. Please contact Jay to set it up.'}), 500

    try:
        response = _req.post(
            'https://openrouter.ai/api/v1/chat/completions',
            headers={
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json',
            },
            json={
                'model': 'openrouter/auto',
                'messages': messages,
                'max_tokens': 800,
            },
            timeout=30,
        )
        result = response.json()
        reply = result['choices'][0]['message']['content']
    except Exception as e:
        return jsonify({'error': f'AI service unavailable: {str(e)}'}), 500

    return jsonify({'reply': reply})



@bp.route('/admin/feedback/report/<int:conv_id>')
@login_required
def feedback_report(conv_id):
    """Generate a structured report from a feedback conversation."""
    if session.get('role') not in ('admin', 'manager'):
        abort(403)
    db = get_db()
    conv = db.execute('SELECT * FROM feedback_conversations WHERE id=?', (conv_id,)).fetchone()
    if not conv:
        abort(404)
    msgs = db.execute(
        'SELECT role,content,created_at FROM feedback_messages WHERE conversation_id=? ORDER BY id',
        (conv_id,)
    ).fetchall()
    conversation_text = '\n\n'.join([f"[{m['role']}]: {m['content']}" for m in msgs])

    api_key = os.environ.get('OPENROUTER_API_KEY', '') or get_setting('openrouter_api_key')
    if not api_key:
        return jsonify({'error': 'OpenRouter API key not configured.'}), 500

    report_prompt = f"""Based on this client feedback conversation, create a structured requirements document.

CONVERSATION:
{conversation_text}

OUTPUT FORMAT:
# Client Requirements Report
**Client:** {conv['client_name'] or 'Not specified'} ({conv['client_email'] or 'No email'})
**Date:** {conv['created_at']}

## Who They Are
[Describe their business, role, and size]

## What They Want
[List all specific features and capabilities requested]

## Why They Need It
[For each major feature, explain the problem it solves]

## Priorities
- **Must Have:** [Critical features]
- **Nice to Have:** [Would be good but not essential]
- **Future:** [Can wait]

## Concerns & Constraints
[Any worries, limitations, or special requirements mentioned]

## Recommended Next Steps
[What Jay should do first based on this feedback]

## Raw Notes
[Any other useful details from the conversation]
"""

    try:
        response = _req.post(
            'https://openrouter.ai/api/v1/chat/completions',
            headers={
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json',
            },
            json={
                'model': 'openrouter/auto',
                'messages': [{'role': 'user', 'content': report_prompt}],
                'max_tokens': 1500,
            },
            timeout=30,
        )
        result = response.json()
        report = result['choices'][0]['message']['content']
    except Exception as e:
        return jsonify({'error': f'AI service unavailable: {str(e)}'}), 500

    # Save report as summary
    db.execute('UPDATE feedback_conversations SET summary=? WHERE id=?', (report, conv_id))
    db.commit()

    return jsonify({'report': report, 'conversation': dict(conv)})


# ── Willie: Schedule Inspection ────────────────────────────────────────────────


@bp.route('/admin/weekly-report', methods=['POST'])
@login_required
@admin_required
@csrf_required
def send_weekly_report():
    db     = get_db()
    claims = db.execute('SELECT * FROM claims').fetchall()
    week_ago = (datetime.datetime.now() - datetime.timedelta(days=7)).isoformat()
    new_this_week    = [c for c in claims if c['created_at'] >= week_ago]
    closed_this_week = [c for c in claims if c['status'] == 'Closed' and c['updated_at'] >= week_ago]
    open_claims      = [c for c in claims if c['status'] != 'Closed']
    pipeline         = sum(c['total_estimate'] for c in open_claims)
    admin_email      = get_setting('admin_report_email') or ADMIN_EMAIL
    html = f'''
    <div style="font-family:sans-serif;max-width:640px;margin:0 auto">
      <div style="background:#0a1628;color:#fff;padding:1.5rem 2rem;border-radius:12px 12px 0 0">
        <h2 style="margin:0;font-size:1.3rem">FloodClaims Pro — Weekly Summary</h2>
        <p style="margin:.25rem 0 0;opacity:.7;font-size:.85rem">{datetime.datetime.now().strftime("%B %d, %Y")}</p>
      </div>
      <div style="background:#f8fafc;padding:1.5rem 2rem;border:1px solid #e2e8f0;border-top:none">
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:1rem;margin-bottom:1.5rem">
          <div style="background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:1rem;text-align:center">
            <div style="font-size:1.8rem;font-weight:800;color:#3b82f6">{len(new_this_week)}</div>
            <div style="font-size:.75rem;color:#64748b;font-weight:700;text-transform:uppercase">New This Week</div>
          </div>
          <div style="background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:1rem;text-align:center">
            <div style="font-size:1.8rem;font-weight:800;color:#10b981">{len(closed_this_week)}</div>
            <div style="font-size:.75rem;color:#64748b;font-weight:700;text-transform:uppercase">Closed This Week</div>
          </div>
          <div style="background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:1rem;text-align:center">
            <div style="font-size:1.8rem;font-weight:800;color:#f59e0b">{len(open_claims)}</div>
            <div style="font-size:.75rem;color:#64748b;font-weight:700;text-transform:uppercase">Open Claims</div>
          </div>
          <div style="background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:1rem;text-align:center">
            <div style="font-size:1.8rem;font-weight:800;color:#6366f1">${pipeline:,.0f}</div>
            <div style="font-size:.75rem;color:#64748b;font-weight:700;text-transform:uppercase">Pipeline Value</div>
          </div>
        </div>
        <p style="font-size:.85rem;color:#64748b">Log in to <a href="https://billy-floods.up.railway.app">FloodClaims Pro</a> to view full details.</p>
      </div>
    </div>'''
    sent = send_email(admin_email, f'FloodClaims Pro — Weekly Report ({datetime.datetime.now().strftime("%b %d")})', html)
    if sent:
        flash(f'📧 Weekly report sent to {admin_email}.', 'success')
    else:
        flash('Email not sent — configure SendGrid in Settings.', 'error')
    return redirect(url_for('admin.settings'))


# ─────────────────────────────────────────────────────────────────────────────
# FEATURE 1: KANBAN PIPELINE VIEW
# ─────────────────────────────────────────────────────────────────────────────



@bp.route('/sales')
def sales_page():
    """Hidden sales/pitch page — no login required, no nav link.
    Remove this route when Billy buys."""
    return render_template('sales.html')


