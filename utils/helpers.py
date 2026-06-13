"""Helper utilities — secret key generation, rate limiting.

Extracted from app.py Phase 1 (lines 56-76, 1563-1572).
"""
import os
import re
import secrets
import time


def _get_secret_key():
    """Get or create a stable secret key.

    Priority:
    1. SECRET_KEY env var
    2. File-based key at $RAILWAY_DATA_DIR/secret_key (or /data/secret_key)
    3. Generate a new random key
    """
    env_key = os.environ.get('SECRET_KEY')
    if env_key:
        return env_key
    data_dir = os.environ.get('RAILWAY_DATA_DIR') or os.environ.get('DATA_DIR') or '/data'
    key_file = os.path.join(data_dir, 'secret_key')
    try:
        os.makedirs(data_dir, exist_ok=True)
        if os.path.exists(key_file):
            with open(key_file) as f:
                key = f.read().strip()
            if key:
                return key
        key = secrets.token_hex(32)
        with open(key_file, 'w') as f:
            f.write(key)
        return key
    except Exception:
        return secrets.token_hex(32)


# In-memory rate limiter {key: [timestamp, ...]}
_rate_store: dict = {}


def is_rate_limited(key, max_calls=5, window=60):
    """Return True if key has exceeded max_calls within window seconds."""
    now = time.time()
    calls = [t for t in _rate_store.get(key, []) if now - t < window]
    _rate_store[key] = calls
    if len(calls) >= max_calls:
        return True
    _rate_store[key].append(now)
    return False


def _log_activity(claim_id, action, user_name=None):
    """Write an entry to the claim activity log."""
    try:
        from flask import session as _session
        from models.database import get_db
        db  = get_db()
        who = user_name or _session.get('name', 'System')
        db.execute(
            'INSERT INTO activity_log (claim_id, actor, action) VALUES (?,?,?)',
            (claim_id, who, action)
        )
        db.commit()
    except Exception as e:
        print(f'_log_activity error: {e}')


def _validate_password(pw):
    """Validate password strength. Returns (ok, error_message)."""
    if len(pw) < 8:
        return False, 'Password must be at least 8 characters.'
    if not re.search(r'[A-Z]', pw):
        return False, 'Password must contain at least one uppercase letter.'
    if not re.search(r'[a-z]', pw):
        return False, 'Password must contain at least one lowercase letter.'
    if not re.search(r'[0-9]', pw):
        return False, 'Password must contain at least one number.'
    return True, ''
