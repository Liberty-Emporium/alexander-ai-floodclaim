"""Authentication decorators — login_required, admin_required, manager_required.

Extracted from app.py Phase 1 (lines 710-738).
"""
from functools import wraps
from flask import session, flash, redirect, url_for


def login_required(f):
    """Require an active session. Redirects to login if user_id is missing."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            flash('Your session expired — please log in again.', 'warning')
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    """Require admin or manager role. Redirects to dashboard if unauthorized."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get('role') not in ('admin', 'manager'):
            flash('Admin access required.', 'error')
            return redirect(url_for('auth.dashboard'))
        return f(*args, **kwargs)
    return decorated


def manager_required(f):
    """Manager can manage team and adjusters but not change app settings."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get('role') not in ('admin', 'manager'):
            flash('Manager access required.', 'error')
            return redirect(url_for('auth.dashboard'))
        return f(*args, **kwargs)
    return decorated
