"""Routes for api blueprint."""

from flask import Blueprint, jsonify, request
from models.database import get_db, hash_pw, get_openrouter_key
import os, sqlite3

bp = Blueprint("api", __name__)

@bp.route('/health')
def health():
    # Report whether an OpenRouter key is configured (DB or env) WITHOUT leaking it.
    # Lets us verify AI is set up on this instance without admin login.
    try:
        ai_configured = bool(get_openrouter_key())
    except Exception:
        ai_configured = False
    return jsonify({'status': 'ok', 'ai_configured': ai_configured})


@bp.route('/api/ai-selftest')
def ai_selftest():
    """Validate the configured OpenRouter key with a tiny live call.
    Returns the real outcome (valid / invalid / out-of-credits / error) WITHOUT
    leaking the key. Public so AI health can be verified without admin login."""
    import requests as _req
    key = ''
    try:
        key = get_openrouter_key()
    except Exception:
        pass
    if not key:
        return jsonify({'ok': False, 'state': 'no_key',
                        'message': 'No OpenRouter key configured (DB or env).'})
    try:
        r = _req.post(
            'https://openrouter.ai/api/v1/chat/completions',
            headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
            json={'model': 'openrouter/auto',
                  'messages': [{'role': 'user', 'content': 'ping'}],
                  'max_tokens': 1},
            timeout=20,
        )
        if r.status_code == 401:
            return jsonify({'ok': False, 'state': 'invalid_key', 'http': 401,
                            'message': 'Key is invalid or expired (401).'})
        if r.status_code == 402:
            return jsonify({'ok': False, 'state': 'no_credits', 'http': 402,
                            'message': 'Key valid but account out of credits (402).'})
        if r.status_code == 429:
            return jsonify({'ok': True, 'state': 'rate_limited', 'http': 429,
                            'message': 'Key works (rate-limited right now, but valid).'})
        d = r.json()
        if 'error' in d:
            msg = d['error'].get('message', str(d['error'])) if isinstance(d['error'], dict) else str(d['error'])
            return jsonify({'ok': False, 'state': 'api_error', 'http': r.status_code,
                            'message': msg[:200]})
        if 'choices' in d:
            return jsonify({'ok': True, 'state': 'working', 'http': r.status_code,
                            'message': 'Key works — AI is ready.'})
        return jsonify({'ok': False, 'state': 'unexpected', 'http': r.status_code,
                        'message': f'Unexpected response (HTTP {r.status_code}).'})
    except Exception as e:
        return jsonify({'ok': False, 'state': 'network_error',
                        'message': f'Could not reach OpenRouter: {str(e)[:160]}'})


@bp.route('/seed', methods=['GET', 'POST'])
def seed_admin():
    """Create admin user. Call once to set up initial admin."""
    try:
        data_dir = os.environ.get('RAILWAY_VOLUME_MOUNT_PATH', '/data')
        db_path = os.path.join(data_dir, 'floodclaim.db')
        
        if not os.path.exists(db_path):
            # Try local path
            db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'floodclaim.db')
        
        if not os.path.exists(db_path):
            return jsonify({'error': 'DB file not found', 'path': db_path}), 404
        
        db = sqlite3.connect(db_path)
        db.row_factory = sqlite3.Row
        
        # Check if users table exists
        tables = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'").fetchall()
        if not tables:
            # Initialize the DB
            from models.database import init_db, DATA_DIR as MDATA_DIR, DB_PATH as MDB_PATH
            os.makedirs(MDATA_DIR, exist_ok=True)
            init_db()
            db.close()
            db = sqlite3.connect(db_path)
            db.row_factory = sqlite3.Row
        
        # Check if admin already exists
        existing = db.execute('SELECT id, email FROM users WHERE email=?', ('leprograms@protonmail.com',)).fetchone()
        if existing:
            db.close()
            return jsonify({'message': 'Admin user already exists', 'email': existing['email']}), 200
        
        # Create admin user
        pw_hash = hash_pw('Mhall001!')
        db.execute(
            'INSERT INTO users (email, password, name, role, is_active) VALUES (?,?,?,?,?)',
            ('leprograms@protonmail.com', pw_hash, 'Jay Alexander', 'admin', 1)
        )
        db.commit()
        
        # Verify
        user = db.execute('SELECT id, email, role FROM users WHERE email=?', ('leprograms@protonmail.com',)).fetchone()
        db.close()
        
        return jsonify({'message': 'Admin user created', 'email': user['email'], 'role': user['role']}), 201
    except Exception as e:
        import traceback
        return jsonify({'error': str(e), 'trace': traceback.format_exc()}), 500



@bp.route('/ready')
def ready():
    """Readiness probe — checks DB connectivity."""
    try:
        db = get_db()
        db.execute('SELECT 1 FROM claims LIMIT 1')
        return jsonify({'ready': True, 'db': 'ok'})
    except Exception as e:
        return jsonify({'ready': False, 'db': str(e)}), 503

# ── Phase 3: Standardized /api/status ───────────────────────────────────


@bp.route('/api/status', methods=['GET'])
def api_status():
    """Simple status endpoint — no rate limiting, no complex queries."""
    return jsonify({
        'app': 'FloodClaims Pro',
        'version': '1.0',
        'status': 'ok',
    })

# ── Claims List (redirect to dashboard) ──────────────────────────────────

