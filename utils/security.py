"""Security utilities — CSRF protection, bot blocking, security headers, file validation.

Extracted from app.py Phase 1 (lines 113-194).
"""
import secrets
from functools import wraps

from flask import request, session, jsonify, abort


# ── CSRF protection ───────────────────────────────────────────────────────────

def _get_csrf_token():
    """Generate (or retrieve) a per-session CSRF token."""
    if 'csrf_token' not in session:
        session['csrf_token'] = secrets.token_hex(32)
    return session['csrf_token']


def _validate_csrf():
    """Return True if the CSRF token in the request matches the session token."""
    token = (request.form.get('csrf_token')
             or request.headers.get('X-CSRF-Token', ''))
    return bool(token and token == session.get('csrf_token', ''))


def csrf_setup(app):
    """Register CSRF token in Jinja2 globals and set up before_request hooks."""
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


def block_bot_paths():
    """before_request hook: return 410 Gone for scanner-probed paths."""
    path = request.path
    if any(path == p or path.startswith(p) for p in _BOT_PATHS):
        return '', 410


def csrf_protect():
    """before_request hook: enforce CSRF on all state-changing requests."""
    if request.method in ('POST', 'PUT', 'DELETE', 'PATCH'):
        if request.path.startswith('/api/'):
            return  # API routes use token auth, skip CSRF
        if request.path.startswith('/portal/'):
            return  # Public client portal — no session/CSRF
        import re
        if re.search(r'/claims/\d+/sign$', request.path):
            return  # Public signature endpoint — no session
        if not _validate_csrf():
            abort(403)


def csrf_required(f):
    """Decorator: reject POST requests with missing/invalid CSRF token.
    Skips validation for Willie API routes (Bearer token auth).
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        if request.method == 'POST' and not _validate_csrf():
            # API callers use JSON + Bearer — don't break them
            if request.is_json or request.headers.get('Authorization', ''):
                return f(*args, **kwargs)
            return jsonify({'error': 'CSRF validation failed'}), 403
        return f(*args, **kwargs)
    return decorated


# ── File validation ──────────────────────────────────────────────────────────

ALLOWED_EXT = {'png', 'jpg', 'jpeg', 'gif', 'webp'}


def allowed_file(filename):
    """Return True if filename has an allowed image extension."""
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXT


# ── Security headers ─────────────────────────────────────────────────────────

def security_headers(response):
    """after_request hook: add security headers to every response."""
    response.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('X-XSS-Protection', '1; mode=block')
    response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    response.headers.setdefault('Permissions-Policy', 'geolocation=(), microphone=(), camera=()')
    response.headers.setdefault(
        'Content-Security-Policy',
        "default-src 'self' https: data: blob:; script-src 'self' 'unsafe-inline' https://unpkg.com; "
        "style-src 'self' https: 'unsafe-inline'; img-src 'self' https: data: blob:; "
        "font-src 'self' https://fonts.googleapis.com https://fonts.gstatic.com; "
        "connect-src 'self' https://unpkg.com https://openrouter.ai https://api.stripe.com; "
        "frame-src 'self' https://js.stripe.com https://maps.google.com;"
    )
    response.headers.setdefault('Strict-Transport-Security', 'max-age=31536000; includeSubDomains')
    return response
