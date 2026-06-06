"""Routes for api blueprint."""

from flask import Blueprint, jsonify, request
from models.database import get_db, hash_pw
import os, sqlite3

bp = Blueprint("api", __name__)

@bp.route('/health')
def health():
    return jsonify({'status': 'ok'})


@bp.route('/seed', methods=['POST'])
def seed_admin():
    """Create admin user. Call once to set up initial admin."""
    try:
        # Use direct connection to avoid any g.db issues
        data_dir = os.environ.get('RAILWAY_VOLUME_MOUNT_PATH', '/data')
        db_path = os.path.join(data_dir, 'floodclaim.db')
        
        if not os.path.exists(db_path):
            return jsonify({'error': 'DB file not found', 'path': db_path}), 404
        
        db = sqlite3.connect(db_path)
        db.row_factory = sqlite3.Row
        
        # Check if users table exists
        tables = db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='users'").fetchall()
        if not tables:
            db.close()
            return jsonify({'error': 'users table does not exist'}), 500
        
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
        return jsonify({'error': str(e)}), 500



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

