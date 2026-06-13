"""
FloodClaims Pro — Database Layer
Extracted from app.py (Phase 3 of modularization refactor)

Contains: DB init, migrations, password hashing, settings, all schema migrations.
"""
import os
import sqlite3
import hashlib
import re
from flask import g

try:
    import bcrypt as _bcrypt
    BCRYPT_OK = True
except ImportError:
    BCRYPT_OK = False

# ── Config (set by app.py before use) ─────────────────────────────────────────
DB_PATH = None
DATA_DIR = None
ADMIN_EMAIL = os.environ.get('ADMIN_EMAIL', 'admin@floodclaimpro.com')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'FloodAdmin2026!')
app = None


def _set_app(flask_app):
    """Set the Flask app reference."""
    global app
    app = flask_app


def _set_paths(db_path, data_dir):
    """Set DB_PATH and DATA_DIR from app config."""
    global DB_PATH, DATA_DIR
    DB_PATH = db_path
    DATA_DIR = data_dir


# ── DB ────────────────────────────────────────────────────────────────────────
_db_initialized = False

def _ensure_db_initialized():
    """Lazily initialize the database on first DB access. Never crashes the app."""
    global _db_initialized
    if not _db_initialized:
        try:
            init_db()
            migrate_claims_columns()
            migrate_new_features()
            migrate_photos_columns()
            migrate_new_features_v2()
            _migrate_recruitment_tables()
            migrate_batch_photo_columns()
            _migrate_aquila_tables()
            _migrate_feedback_tables()
            _db_initialized = True
        except Exception as e:
            import sys
            print(f"[db_init] WARNING: init_db() failed (non-fatal): {e}", file=sys.stderr)
            # Don't set _db_initialized — retry on next access

def get_db():
    global _db_initialized
    if 'db' not in g:
        _ensure_db_initialized()
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA journal_mode=WAL")
        g.db.execute("PRAGMA foreign_keys=ON")
    return g.db


def close_db(e=None):
    """Close the database connection. Registered as teardown in app.py."""
    db = g.pop('db', None)
    if db:
        db.close()


def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            email       TEXT UNIQUE NOT NULL,
            name        TEXT NOT NULL DEFAULT '',
            password    TEXT NOT NULL,
            role        TEXT NOT NULL DEFAULT 'adjuster',
            is_active   INTEGER DEFAULT 1,
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS claims (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            claim_number    TEXT UNIQUE NOT NULL,
            adjuster_id     INTEGER REFERENCES users(id),
            client_name     TEXT NOT NULL,
            client_phone    TEXT DEFAULT '',
            client_phone_alt TEXT DEFAULT '',
            client_email    TEXT DEFAULT '',
            property_address TEXT NOT NULL,
            property_type   TEXT DEFAULT '',
            property_sqft   TEXT DEFAULT '',
            year_built      TEXT DEFAULT '',
            num_floors      TEXT DEFAULT '',
            flood_date      TEXT NOT NULL,
            flood_source    TEXT DEFAULT '',
            water_category  TEXT DEFAULT '',
            water_class     TEXT DEFAULT '',
            water_depth_in  TEXT DEFAULT '',
            date_water_removed TEXT DEFAULT '',
            inspection_date TEXT DEFAULT '',
            insurance_company TEXT DEFAULT '',
            policy_number   TEXT DEFAULT '',
            policy_type     TEXT DEFAULT '',
            coverage_building REAL DEFAULT 0,
            coverage_contents REAL DEFAULT 0,
            deductible      REAL DEFAULT 0,
            mortgage_company TEXT DEFAULT '',
            mortgage_loan_number TEXT DEFAULT '',
            cause_of_loss   TEXT DEFAULT '',
            priority        TEXT DEFAULT 'Normal',
            status          TEXT DEFAULT 'New',
            total_estimate  REAL DEFAULT 0,
            notes           TEXT DEFAULT '',
            created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS rooms (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            claim_id    INTEGER REFERENCES claims(id) ON DELETE CASCADE,
            name        TEXT NOT NULL,
            description TEXT DEFAULT '',
            subtotal    REAL DEFAULT 0,
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS line_items (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            room_id     INTEGER REFERENCES rooms(id) ON DELETE CASCADE,
            description TEXT NOT NULL,
            quantity    REAL DEFAULT 1,
            unit        TEXT DEFAULT 'ea',
            unit_cost   REAL DEFAULT 0,
            total       REAL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS photos (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            claim_id    INTEGER REFERENCES claims(id) ON DELETE CASCADE,
            room_id     INTEGER REFERENCES rooms(id) ON DELETE SET NULL,
            filename    TEXT NOT NULL,
            caption     TEXT DEFAULT '',
            ai_description TEXT DEFAULT '',
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP
        );
    ''')
    # Soft-delete columns (added via migration for existing DBs)
    _rooms_cols = [r[1] for r in db.execute('PRAGMA table_info(rooms)').fetchall()]
    if 'deleted_at' not in _rooms_cols:
        db.execute('ALTER TABLE rooms ADD COLUMN deleted_at TEXT DEFAULT NULL')
    _li_cols = [r[1] for r in db.execute('PRAGMA table_info(line_items)').fetchall()]
    if 'deleted_at' not in _li_cols:
        db.execute('ALTER TABLE line_items ADD COLUMN deleted_at TEXT DEFAULT NULL')
    if 'claim_id' not in _li_cols:
        db.execute('ALTER TABLE line_items ADD COLUMN claim_id INTEGER REFERENCES claims(id) ON DELETE CASCADE')
    _photo_cols = [r[1] for r in db.execute('PRAGMA table_info(photos)').fetchall()]
    if 'deleted_at' not in _photo_cols:
        db.execute('ALTER TABLE photos ADD COLUMN deleted_at TEXT DEFAULT NULL')
    db.executescript('''
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS willie_conversations (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER REFERENCES users(id),
            title   TEXT NOT NULL DEFAULT 'New Conversation',
            created TEXT DEFAULT CURRENT_TIMESTAMP,
            updated TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS willie_messages (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER REFERENCES willie_conversations(id) ON DELETE CASCADE,
            role            TEXT NOT NULL,
            content         TEXT NOT NULL,
            created         TEXT DEFAULT CURRENT_TIMESTAMP
        );
    ''')
    cur = db.execute('SELECT id FROM users WHERE email=?', (ADMIN_EMAIL,))
    if not cur.fetchone():
        db.execute('INSERT INTO users (email, name, password, role) VALUES (?,?,?,?)',
                   (ADMIN_EMAIL, 'Admin', hash_pw(ADMIN_PASSWORD), 'admin'))
    # Migration: add is_active column if missing (existing databases)
    cols = [row[1] for row in db.execute('PRAGMA table_info(users)').fetchall()]
    if 'is_active' not in cols:
        db.execute('ALTER TABLE users ADD COLUMN is_active INTEGER DEFAULT 1')
    # Migration: ensure admin user has a proper password
    admin = db.execute('SELECT id FROM users WHERE email=?', (ADMIN_EMAIL,)).fetchone()
    if admin:
        db.execute('UPDATE users SET password=? WHERE id=?',
                   (hash_pw(ADMIN_PASSWORD), admin['id']))
    db.commit()
    db.close()

def hash_pw(pw):
    """Hash password with bcrypt (12 rounds). Falls back to sha256 if bcrypt unavailable."""
    if BCRYPT_OK:
        return _bcrypt.hashpw(pw.encode(), _bcrypt.gensalt(12)).decode()
    return hashlib.sha256(pw.encode()).hexdigest()

def check_pw(pw, hashed):
    """Verify password — handles both bcrypt hashes and legacy sha256 hashes.
    On successful legacy login, transparently upgrades the stored hash to bcrypt.
    """
    if not hashed:
        return False
    # bcrypt hashes start with $2b$ or $2a$
    if BCRYPT_OK and hashed.startswith('$2'):
        try:
            return _bcrypt.checkpw(pw.encode(), hashed.encode())
        except Exception:
            return False
    # Legacy SHA-256 path
    return hashlib.sha256(pw.encode()).hexdigest() == hashed

# All DB migrations (init_db, migrate_claims_columns, migrate_new_features, etc.)
# are now called lazily in get_db() via _ensure_db_initialized().
# This prevents worker crashes when Railway volume isn't ready on startup.

def migrate_claims_columns():
    new_cols = [
        ('client_phone_alt','TEXT DEFAULT ""'),
        ('property_type','TEXT DEFAULT ""'),
        ('property_sqft','TEXT DEFAULT ""'),
        ('year_built','TEXT DEFAULT ""'),
        ('num_floors','TEXT DEFAULT ""'),
        ('flood_source','TEXT DEFAULT ""'),
        ('water_category','TEXT DEFAULT ""'),
        ('water_class','TEXT DEFAULT ""'),
        ('water_depth_in','TEXT DEFAULT ""'),
        ('date_water_removed','TEXT DEFAULT ""'),
        ('inspection_date','TEXT DEFAULT ""'),
        ('policy_type','TEXT DEFAULT ""'),
        ('coverage_building','REAL DEFAULT 0'),
        ('coverage_contents','REAL DEFAULT 0'),
        ('deductible','REAL DEFAULT 0'),
        ('mortgage_company','TEXT DEFAULT ""'),
        ('mortgage_loan_number','TEXT DEFAULT ""'),
        ('cause_of_loss','TEXT DEFAULT ""'),
        ('priority','TEXT DEFAULT "Normal"'),
    ]
    try:
        db   = sqlite3.connect(DB_PATH)
        cols = [r[1] for r in db.execute('PRAGMA table_info(claims)').fetchall()]
        for col, typedef in new_cols:
            if col not in cols:
                db.execute(f'ALTER TABLE claims ADD COLUMN {col} {typedef}')
        db.commit()
        db.close()
    except Exception:
        pass

# migrate_claims_columns() is now called lazily in get_db()


def migrate_new_features():
    """Add tables/columns for new features — safe to run every boot."""
    try:
        db = sqlite3.connect(DB_PATH)
        db.executescript('''
            CREATE TABLE IF NOT EXISTS client_portal_tokens (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                claim_id   INTEGER REFERENCES claims(id) ON DELETE CASCADE,
                token      TEXT UNIQUE NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS signatures (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                claim_id   INTEGER REFERENCES claims(id) ON DELETE CASCADE,
                signer     TEXT NOT NULL,
                signed_at  TEXT DEFAULT CURRENT_TIMESTAMP,
                sig_data   TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS stripe_customers (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER UNIQUE REFERENCES users(id),
                stripe_customer TEXT,
                stripe_sub_id   TEXT,
                plan            TEXT DEFAULT 'basic',
                status          TEXT DEFAULT 'active',
                created_at      TEXT DEFAULT CURRENT_TIMESTAMP
            );
        ''')
        # Estimate jobs table — async polling so browser never times out
        db.execute('''
            CREATE TABLE IF NOT EXISTS estimate_jobs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                claim_id    INTEGER NOT NULL,
                status      TEXT DEFAULT 'pending',
                progress    INTEGER DEFAULT 0,
                progress_msg TEXT DEFAULT '',
                result      TEXT DEFAULT '',
                error       TEXT DEFAULT '',
                created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at  TEXT DEFAULT CURRENT_TIMESTAMP
            );
        ''')
        # Migrate existing table if columns missing
        _ej_cols = [r[1] for r in db.execute('PRAGMA table_info(estimate_jobs)').fetchall()]
        if 'progress' not in _ej_cols:
            db.execute('ALTER TABLE estimate_jobs ADD COLUMN progress INTEGER DEFAULT 0')
        if 'progress_msg' not in _ej_cols:
            db.execute('ALTER TABLE estimate_jobs ADD COLUMN progress_msg TEXT DEFAULT ""')
        # Migrate: add ON DELETE CASCADE to claim_id (SQLite requires table rebuild)
        _ej_fk = db.execute('PRAGMA foreign_key_list(estimate_jobs)').fetchall()
        _ej_has_cascade = any(fk['table'] == 'claims' and fk['on_delete'] == 'CASCADE' for fk in _ej_fk)
        if not _ej_has_cascade:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS estimate_jobs_new (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    claim_id    INTEGER NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
                    status      TEXT DEFAULT 'pending',
                    progress    INTEGER DEFAULT 0,
                    progress_msg TEXT DEFAULT '',
                    result      TEXT DEFAULT '',
                    error       TEXT DEFAULT '',
                    created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at  TEXT DEFAULT CURRENT_TIMESTAMP
                );
                INSERT INTO estimate_jobs_new SELECT * FROM estimate_jobs;
                DROP TABLE estimate_jobs;
                ALTER TABLE estimate_jobs_new RENAME TO estimate_jobs;
            ''')
        cols = [r[1] for r in db.execute('PRAGMA table_info(claims)').fetchall()]
        extras = [
            ('flood_zone',     'TEXT DEFAULT ""'),
            ('fema_map_number','TEXT DEFAULT ""'),
            ('lat',            'REAL DEFAULT 0'),
            ('lng',            'REAL DEFAULT 0'),
            ('maps_embed_url', 'TEXT DEFAULT ""'),
            ('client_token',   'TEXT DEFAULT ""'),
        ]
        for col, typedef in extras:
            if col not in cols:
                db.execute(f'ALTER TABLE claims ADD COLUMN {col} {typedef}')
        db.commit()
        db.close()
    except Exception as e:
        print(f'migrate_new_features error: {e}')

# migrate_new_features() now called lazily in get_db()


def migrate_photos_columns():
    """Ensure photos table has room_id and ai_description columns (added in later versions)."""
    try:
        db   = sqlite3.connect(DB_PATH)
        cols = [r[1] for r in db.execute('PRAGMA table_info(photos)').fetchall()]
        if 'room_id' not in cols:
            db.execute('ALTER TABLE photos ADD COLUMN room_id INTEGER REFERENCES rooms(id) ON DELETE SET NULL')
        if 'ai_description' not in cols:
            db.execute('ALTER TABLE photos ADD COLUMN ai_description TEXT DEFAULT ""')
        if 'caption' not in cols:
            db.execute('ALTER TABLE photos ADD COLUMN caption TEXT DEFAULT ""')
        db.commit()
        db.close()
    except Exception as e:
        print(f'migrate_photos_columns error: {e}')

# migrate_photos_columns() now called lazily in get_db()


def migrate_new_features_v2():
    """Add tables for Kanban, Scheduler, Notifications, Analytics, Activity Log — safe to run every boot."""
    try:
        db = sqlite3.connect(DB_PATH)
        db.executescript('''
            CREATE TABLE IF NOT EXISTS inspection_slots (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                claim_id    INTEGER REFERENCES claims(id) ON DELETE CASCADE,
                adjuster_id INTEGER REFERENCES users(id),
                slot_date   TEXT NOT NULL,
                slot_time   TEXT NOT NULL,
                status      TEXT DEFAULT 'pending',
                notes       TEXT DEFAULT '',
                created_at  TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS notifications_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                claim_id    INTEGER REFERENCES claims(id) ON DELETE CASCADE,
                type        TEXT NOT NULL,
                recipient   TEXT NOT NULL,
                message     TEXT NOT NULL,
                sent_at     TEXT DEFAULT CURRENT_TIMESTAMP,
                status      TEXT DEFAULT 'sent'
            );
            CREATE TABLE IF NOT EXISTS activity_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                claim_id    INTEGER REFERENCES claims(id) ON DELETE CASCADE,
                actor       TEXT NOT NULL DEFAULT 'System',
                action      TEXT NOT NULL,
                created_at  TEXT DEFAULT CURRENT_TIMESTAMP
            );
        ''')
        # Add inspection_date + kanban_order columns to claims if missing
        cols = [r[1] for r in db.execute('PRAGMA table_info(claims)').fetchall()]
        extras = [
            ('kanban_order',  'INTEGER DEFAULT 0'),
            ('sched_date',    'TEXT DEFAULT ""'),
            ('sched_time',    'TEXT DEFAULT ""'),
        ]
        for col, typedef in extras:
            if col not in cols:
                db.execute(f'ALTER TABLE claims ADD COLUMN {col} {typedef}')
        db.commit()
        db.close()
    except Exception as e:
        print(f'migrate_new_features_v2 error: {e}')

# migrate_new_features_v2() now called lazily in get_db()

def get_setting(key, default=''):
    try:
        db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
        row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        db.close()
        return row['value'] if row else default
    except Exception:
        return default


def set_setting(key, value):
    db = sqlite3.connect(DB_PATH)
    db.execute(
        'INSERT INTO settings (key, value) VALUES (?,?) '
        'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
        (key, value))
    db.commit()
    db.close()


def get_openrouter_key():
    """Resolve the active OpenRouter API key.

    Single source of truth used by every AI route. Order:
      1. DB setting `openrouter_api_key` (Billy can paste his own key in Settings)
      2. Railway env var OPENROUTER_API_KEY

    Returns '' when no key is configured. Replaces the bare `OPENROUTER_KEY`
    global that was lost in the monolith->modules split (it was undefined in
    routes/, causing a NameError that silently killed Aquila chat and the
    photo/estimate routes).
    """
    return get_setting('openrouter_api_key', '') or os.environ.get('OPENROUTER_API_KEY', '')

# ── Aquila API token ─────────────────────────────────────────────────────────────────

# ── Admin: Recruitment ─────────────────────────────────────────────────────────

def _migrate_recruitment_tables():
    """Create recruitment application tables if they don't exist."""
    db = sqlite3.connect(DB_PATH)
    db.executescript('''
        CREATE TABLE IF NOT EXISTS adjuster_applications (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT NOT NULL,
            email           TEXT NOT NULL,
            phone           TEXT DEFAULT '',
            license_number  TEXT NOT NULL,
            state           TEXT NOT NULL,
            status          TEXT DEFAULT 'pending',
            notes           TEXT DEFAULT '',
            created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
            reviewed_at     TEXT DEFAULT NULL,
            reviewed_by     INTEGER DEFAULT NULL
        );
        CREATE TABLE IF NOT EXISTS contractor_applications (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT NOT NULL,
            email           TEXT NOT NULL,
            phone           TEXT DEFAULT '',
            license_type    TEXT DEFAULT '',
            license_number  TEXT DEFAULT '',
            state           TEXT NOT NULL,
            experience_years TEXT DEFAULT '',
            status          TEXT DEFAULT 'pending',
            progress        INTEGER DEFAULT 0,
            training_completed TEXT DEFAULT '',
            test_score      INTEGER DEFAULT NULL,
            notes           TEXT DEFAULT '',
            created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
            reviewed_at     TEXT DEFAULT NULL,
            reviewed_by     INTEGER DEFAULT NULL
        );
    ''')
    db.commit()
    db.close()

def _migrate_feedback_tables():
    """Create feedback conversation and message tables, plus client registry and poll state."""
    db = sqlite3.connect(DB_PATH)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS feedback_conversations (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER REFERENCES users(id),
            client_email    TEXT NOT NULL DEFAULT '',
            client_name     TEXT NOT NULL DEFAULT '',
            title           TEXT NOT NULL DEFAULT 'Feedback Session',
            status          TEXT NOT NULL DEFAULT 'active',
            summary         TEXT NOT NULL DEFAULT '',
            created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS feedback_messages (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            conversation_id INTEGER REFERENCES feedback_conversations(id) ON DELETE CASCADE,
            role            TEXT NOT NULL,
            content         TEXT NOT NULL,
            created_at      TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS feedback_clients (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL DEFAULT '',
            email       TEXT NOT NULL DEFAULT '',
            token       TEXT UNIQUE NOT NULL,
            app_name    TEXT DEFAULT '',
            status      TEXT DEFAULT 'active',
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS feedback_poll_state (
            id              INTEGER PRIMARY KEY CHECK (id = 1),
            last_check      TEXT DEFAULT '',
            last_notification TEXT DEFAULT ''
        );
    """)
    # Add columns to feedback_conversations if they don't exist yet (migration for existing installs)
    _cols = [r[1] for r in db.execute('PRAGMA table_info(feedback_conversations)').fetchall()]
    if 'admin_read' not in _cols:
        db.execute('ALTER TABLE feedback_conversations ADD COLUMN admin_read INTEGER DEFAULT 0')
    if 'priority' not in _cols:
        db.execute('ALTER TABLE feedback_conversations ADD COLUMN priority TEXT DEFAULT "normal"')
    if 'client_token' not in _cols:
        db.execute('ALTER TABLE feedback_conversations ADD COLUMN client_token TEXT DEFAULT ""')
    # Seed poll state if empty
    existing = db.execute('SELECT id FROM feedback_poll_state WHERE id=1').fetchone()
    if not existing:
        db.execute('INSERT INTO feedback_poll_state (id, last_check, last_notification) VALUES (1, "", "")')
    # Seed Mr. Forbes as first client (only if not already exists)
    forbes = db.execute('SELECT id FROM feedback_clients WHERE email=?', ('tcwilliamsemail@gmail.com',)).fetchone()
    if not forbes:
        import secrets as _sec
        token = _sec.token_urlsafe(24)
        db.execute('INSERT INTO feedback_clients (name, email, token, app_name) VALUES (?,?,?,?)',
                   ('Mr. Forbes', 'tcwilliamsemail@gmail.com', token, 'Forbes Custom App'))
    db.commit()
    db.close()


def migrate_batch_photo_columns():
    """Add columns to photos table for batch AI analysis."""
    try:
        db = sqlite3.connect(DB_PATH)
        cols = [r[1] for r in db.execute('PRAGMA table_info(photos)').fetchall()]
        extras = [
            ('batch_id',       'INTEGER DEFAULT 0'),
            ('ai_raw_json',    'TEXT DEFAULT ""'),
            ('detected_items', 'TEXT DEFAULT "[]"'),
            ('is_high_value',  'INTEGER DEFAULT 0'),
            ('needs_closeup',  'INTEGER DEFAULT 0'),
            ('water_category', 'TEXT DEFAULT ""'),
            ('water_class',    'TEXT DEFAULT ""'),
            ('ai_confidence',  'REAL DEFAULT 0'),
            ('customer_submitted', 'INTEGER DEFAULT 0'),
            ('analysis_status',    'TEXT DEFAULT "pending"'),
            ('ai_damage_severity', 'TEXT DEFAULT ""'),
            ('ai_room_type',       'TEXT DEFAULT ""'),
            ('ai_water_evidence',  'TEXT DEFAULT ""'),
            ('ai_mold_detected',   'INTEGER DEFAULT 0'),
            ('ai_structural_damage', 'INTEGER DEFAULT 0'),
            ('ai_flooring_type',   'TEXT DEFAULT ""'),
            ('ai_flooring_damage', 'TEXT DEFAULT ""'),
            ('ai_wall_damage',     'TEXT DEFAULT ""'),
            ('ai_suggested_items', 'TEXT DEFAULT "[]"'),
            ('ai_analysis_json',   'TEXT DEFAULT ""'),
        ]
        for col, typedef in extras:
            if col not in cols:
                db.execute(f'ALTER TABLE photos ADD COLUMN {col} {typedef}')
        db.commit()
        db.close()
    except Exception as e:
        print(f'migrate_batch_photo_columns error: {e}')

# migrate_batch_photo_columns() now called lazily in get_db()


def _migrate_aquila_tables():
    """Create Aquila 3D model generation tables if they don't exist."""
    try:
        db = sqlite3.connect(DB_PATH)
        db.executescript('''
            CREATE TABLE IF NOT EXISTS aquila_jobs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                claim_id        INTEGER NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
                photo_id        INTEGER REFERENCES photos(id) ON DELETE SET NULL,
                meshy_task_id   TEXT NOT NULL,
                model_name      TEXT DEFAULT '',
                model_url       TEXT DEFAULT '',
                model_data      TEXT DEFAULT '',
                status          TEXT DEFAULT 'pending',
                error           TEXT DEFAULT '',
                created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at      TEXT DEFAULT CURRENT_TIMESTAMP,
                completed_at    TEXT DEFAULT NULL
            );
        ''')
        db.commit()
        db.close()
    except Exception as e:
        print(f'_migrate_aquila_tables error: {e}')

