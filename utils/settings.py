"""Settings helpers — get/set app settings in the database.

Extracted from app.py Phase 1 (lines 757-776).
"""
import sqlite3


def get_setting(db_path, key, default=''):
    """Read a setting value from the settings table."""
    try:
        db = sqlite3.connect(db_path)
        db.row_factory = sqlite3.Row
        row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        db.close()
        return row['value'] if row else default
    except Exception:
        return default


def set_setting(db_path, key, value):
    """Write a setting value to the settings table (upsert)."""
    db = sqlite3.connect(db_path)
    db.execute(
        'INSERT INTO settings (key, value) VALUES (?,?) '
        'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
        (key, value))
    db.commit()
    db.close()
