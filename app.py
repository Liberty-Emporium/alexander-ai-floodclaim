"""
FloodClaims Pro — Modular Flask App
====================================
Refactored from monolith (7,356 lines) to modular blueprint architecture.

Phase 1 ✅ — utils extracted (security, auth_decorators, helpers, settings)
Phase 2 ✅ — services extracted (ai, email, fema, claims, willie)
Phase 3 ✅ — models/DB layer extracted
Phase 4 ✅ — routes extracted into 15 blueprints
Phase 5 ✅ — app.py slimmed to < 150 lines

Branch: refactor/modular (DO NOT push to main without Mingo + Jay approval)
"""
import os, secrets, hashlib, json, datetime, pathlib
from datetime import timedelta, timezone
from functools import wraps
from flask import Flask, render_template, request, session, abort

# ── Models (Phase 3: extracted to models package) ───────────────────────────
from models.database import (
    _set_app, _set_paths,
    get_db, close_db,
)

# ── Routes (Phase 4: extracted to routes package) ────────────────────────────
from routes import register_blueprints

# ── Create Flask app ─────────────────────────────────────────────────────────
app = Flask(__name__)

# ── Secret key: stable across deploys ────────────────────────────────────────
_SECRET_KEY = os.environ.get('SECRET_KEY', '')
if not _SECRET_KEY:
    _KEY_FILE = '/data/.secret_key'
    try:
        os.makedirs('/data', exist_ok=True)
        if os.path.exists(_KEY_FILE):
            with open(_KEY_FILE) as _f:
                _SECRET_KEY = _f.read().strip()
        if not _SECRET_KEY:
            _SECRET_KEY = secrets.token_hex(32)
            with open(_KEY_FILE, 'w') as _f:
                _f.write(_SECRET_KEY)
    except Exception:
        import hashlib as _hashlib
        _svc = os.environ.get('RAILWAY_SERVICE_ID', 'floodclaim-pro-default')
        _SECRET_KEY = _hashlib.sha256(f'floodclaim-secret-{_svc}'.encode()).hexdigest()

app.secret_key = _SECRET_KEY

# ── Session config ────────────────────────────────────────────────────────────
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('RAILWAY_ENVIRONMENT') is not None

# ── Paths ─────────────────────────────────────────────────────────────────────
DATA_DIR = os.environ.get('RAILWAY_VOLUME_MOUNT_PATH', '/data')
DB_PATH = os.path.join(DATA_DIR, 'floodclaim.db')
UPLOAD_DIR = os.path.join(DATA_DIR, 'uploads')
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)

# ── Initialize models ────────────────────────────────────────────────────────
_set_app(app)
_set_paths(DB_PATH, DATA_DIR)
app.teardown_appcontext(close_db)

# ── CSRF protection ───────────────────────────────────────────────────────────
def _get_csrf_token():
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_hex(32)
    return session['csrf_token']

def _validate_csrf():
    token = (request.form.get('csrf_token')
             or request.headers.get('X-CSRF-Token', ''))
    return bool(token and token == session.get('csrf_token', ''))

app.jinja_env.globals['csrf_token'] = _get_csrf_token

# ── Bot / scanner sink ───────────────────────────────────────────────────────
_BOT_PATHS = [
    '/wp-admin/', '/wp-login.php', '/wp-cron.php', '/wp-includes/',
    '/wp-content/', '/xmlrpc.php', '/wp-admin/install.php',
    '/wp-json/', '/.env', '/.git/', '/config.php', '/setup.php',
    '/install.php', '/phpmyadmin/', '/pma/', '/admin/config.php',
    '/sitemap.xml', '/sitemap_index.xml', '/robots.txt.bak',
    '/.htaccess', '/web.config', '/backup/', '/administrator/',
    '/joomla/', '/drupal/', '/typo3/',
]

@app.before_request
def _block_bot_paths():
    path = request.path
    if any(path == p or path.startswith(p) for p in _BOT_PATHS):
        return '', 410

@app.before_request
def _csrf_protect():
    if request.method in ('POST', 'PUT', 'DELETE', 'PATCH'):
        if request.path.startswith('/api/'):
            return
        if request.path.startswith('/portal/'):
            return
        if request.path == '/seed':
            return
        import re
        if re.search(r'/claims/\d+/sign$', request.path):
            return
        if not _validate_csrf():
            abort(403)

# ── Jinja globals ────────────────────────────────────────────────────────────
app.jinja_env.globals['get_setting'] = get_db  # placeholder, overridden below
# get_setting is imported from models.database but needs to be a jinja global
from models.database import get_setting as _get_setting
app.jinja_env.globals['get_setting'] = _get_setting

# ── Security headers ─────────────────────────────────────────────────────────
from utils.security import security_headers
app.after_request(security_headers)

# ── Cross-app: Pet Vet AI ────────────────────────────────────────────────────
_PET_VET_AI_URL = "https://ai-vet-tech.alexanderai.site"

def _call_pet_vet_ai(path, data=None, method='POST', timeout=30):
    """Call Pet Vet AI directly via hardcoded URL. No EcDash dependency."""
    import urllib.request, urllib.error, json as _json
    url = _PET_VET_AI_URL + path
    try:
        body = _json.dumps(data).encode() if data else None
        headers = {'Content-Type': 'application/json'}
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return _json.loads(resp.read().decode())
    except Exception as e:
        import sys
        print(f"[pet_vet_ai] call failed (non-fatal): {e}", file=sys.stderr)
        return None

# ── Register blueprints ──────────────────────────────────────────────────────
register_blueprints(app)

# ── Error handlers ────────────────────────────────────────────────────────────
@app.errorhandler(404)
def not_found(e):
    return render_template('errors/404.html'), 404

@app.errorhandler(403)
def forbidden(e):
    return render_template('errors/403.html'), 403

@app.errorhandler(500)
def server_error(e):
    return render_template('errors/500.html'), 500

# ── Run ───────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
