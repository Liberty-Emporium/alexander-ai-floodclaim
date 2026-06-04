"""Routes for api blueprint."""

from flask import Blueprint, jsonify
from models.database import get_db

bp = Blueprint("api", __name__)

@bp.route('/health')
def health():
    return jsonify({'status': 'ok'})



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

