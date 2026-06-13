"""
FloodClaims Pro — Modular Flask App
====================================
Clean app.py — all logic in proper modules.

Structure:
  routes/     — All route blueprints (auth, claims, admin, etc.)
  models/     — Database layer (models/database.py)
  services/   — Business logic (AI, email, FEMA, etc.)
  utils/      — Helpers (security, auth decorators, settings)
"""
import os
import logging
from datetime import timedelta

from flask import Flask, render_template, jsonify

# ── Models ────────────────────────────────────────────────────────────────────
from models.database import (
    _set_app, _set_paths,
    get_db, close_db, init_db,
    get_setting, get_openrouter_key,
)

# ── Routes ────────────────────────────────────────────────────────────────────
from routes import register_blueprints

# ── Utils ─────────────────────────────────────────────────────────────────────
from utils.security import csrf_setup, block_bot_paths, csrf_protect, security_headers
from utils.helpers import _get_secret_key

# ── Create Flask app ──────────────────────────────────────────────────────────
app = Flask(__name__)

# ── Secret key ────────────────────────────────────────────────────────────────
app.secret_key = _get_secret_key()

# ── Session config ────────────────────────────────────────────────────────────
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'

# ── Upload limit ──────────────────────────────────────────────────────────────
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50 MB for batch uploads
# Railway edge terminates TLS; plain HTTP to container. Secure=False required
# so the session cookie is sent back on the internal HTTP connection.
app.config['SESSION_COOKIE_SECURE'] = False

# ── Paths ─────────────────────────────────────────────────────────────────────
DATA_DIR = os.environ.get('RAILWAY_VOLUME_MOUNT_PATH', '/data')
DB_PATH = os.path.join(DATA_DIR, 'floodclaim.db')
UPLOAD_DIR = os.path.join(DATA_DIR, 'uploads')
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)

# ── Initialize models ─────────────────────────────────────────────────────────
_set_app(app)
_set_paths(DB_PATH, DATA_DIR)
app.teardown_appcontext(close_db)

# ── CSRF protection ───────────────────────────────────────────────────────────
csrf_setup(app)

# ── Request hooks ─────────────────────────────────────────────────────────────
app.before_request(block_bot_paths)
app.before_request(csrf_protect)
app.after_request(security_headers)

# ── Jinja globals ─────────────────────────────────────────────────────────────
app.jinja_env.globals['get_setting'] = get_setting

# ── Register all blueprints ───────────────────────────────────────────────────
register_blueprints(app)

# ── Start Aquila's background cron scheduler ─────────────────────────────────
# Only start in production (when running on Railway), not during local dev.
# Railway sets PORT env var; local dev typically doesn't.
if os.environ.get('PORT'):
    try:
        from services.aquila_cron import start_scheduler
        start_scheduler()
        print('[AQUILA] Background cron scheduler started')
    except Exception as e:
        print(f'[AQUILA] Failed to start scheduler: {e}')
else:
    print('[AQUILA] Local dev mode — scheduler not started (set PORT env var to enable)')

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

# ── Health check ──────────────────────────────────────────────────────────────
@app.route('/health')
def health():
    # Report whether an OpenRouter key is configured (DB or env) WITHOUT leaking it.
    # Lets us verify AI is set up on this instance without admin login.
    try:
        ai_configured = bool(get_openrouter_key())
    except Exception:
        ai_configured = False
    return jsonify({'status': 'ok', 'ai_configured': ai_configured})

# ── Run ───────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
