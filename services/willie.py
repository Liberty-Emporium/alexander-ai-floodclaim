"""Willie API service — token auth for Aquila API integration.

Extracted from app.py Phase 2 (lines 779-791).
"""
import os
import secrets
import sqlite3

from flask import request as _flask_request


def _get_setting(key, default=''):
    DB_PATH = os.environ.get('DB_PATH') or '/data/floodclaims.db'
    try:
        db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
        row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        db.close()
        return row['value'] if row else default
    except Exception:
        return default


def _set_setting(key, value):
    DB_PATH = os.environ.get('DB_PATH') or '/data/floodclaims.db'
    db = sqlite3.connect(DB_PATH)
    db.execute(
        'INSERT INTO settings (key, value) VALUES (?,?) '
        'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
        (key, value))
    db.commit()
    db.close()


def get_willie_token():
    """Get or auto-generate the Aquila API token."""
    token = _get_setting('willie_api_token')
    if not token:
        token = secrets.token_urlsafe(32)
        _set_setting('willie_api_token', token)
    return token


def willie_auth():
    """Verify Willie API token from Authorization header."""
    auth  = _flask_request.headers.get('Authorization', '')
    token = auth.replace('Bearer ', '').strip() if auth.startswith('Bearer ') else ''
    return bool(token and token == _get_setting('willie_api_token'))
