"""
FloodClaims Pro — Database Layer
Extracted from app.py (Phase 3 of modularization refactor)

Contains: DB init, migrations, password hashing, settings, all schema migrations.
"""
import os
import sqlite3
import hashlib
from flask import g

try:
    import bcrypt as _bcrypt
    BCRYPT_OK = True
except ImportError:
    BCRYPT_OK = False

# ── Config (set by app.py before use) ─────────────────────────────────────────
DB_PATH = None
DATA_DIR = None
ADMIN_EMAIL = 'admin@floodclaimpro.com'
ADMIN_PASSWORD = 'admin123'
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
            _migrate_training_tables()
            _seed_training_modules()
            migrate_batch_photo_columns()
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
        CREATE TABLE IF NOT EXISTS training_classes (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            title           TEXT NOT NULL,
            description     TEXT NOT NULL DEFAULT '',
            price_cents     INTEGER NOT NULL DEFAULT 5000,
            status          TEXT NOT NULL DEFAULT 'active',
            image_url       TEXT DEFAULT '',
            created_at      TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS training_lessons (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            class_id        INTEGER REFERENCES training_classes(id) ON DELETE CASCADE,
            title           TEXT NOT NULL,
            content         TEXT NOT NULL DEFAULT '',
            lesson_order    INTEGER NOT NULL DEFAULT 0,
            video_url       TEXT DEFAULT '',
            created_at      TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS training_enrollments (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER REFERENCES users(id),
            class_id        INTEGER REFERENCES training_classes(id),
            stripe_session  TEXT DEFAULT '',
            payment_status  TEXT NOT NULL DEFAULT 'pending',
            progress_pct    INTEGER NOT NULL DEFAULT 0,
            completed_at    TEXT DEFAULT NULL,
            enrolled_at     TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, class_id)
        );
        CREATE TABLE IF NOT EXISTS training_progress (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            enrollment_id   INTEGER REFERENCES training_enrollments(id) ON DELETE CASCADE,
            lesson_id       INTEGER REFERENCES training_lessons(id) ON DELETE CASCADE,
            completed       INTEGER NOT NULL DEFAULT 0,
            completed_at    TEXT DEFAULT NULL,
            UNIQUE(enrollment_id, lesson_id)
        );
        CREATE TABLE IF NOT EXISTS training_exam_questions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            class_id        INTEGER REFERENCES training_classes(id) ON DELETE CASCADE,
            question        TEXT NOT NULL,
            option_a        TEXT NOT NULL,
            option_b        TEXT NOT NULL,
            option_c        TEXT NOT NULL,
            option_d        TEXT NOT NULL,
            correct_answer  TEXT NOT NULL DEFAULT 'a',
            created_at      TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS training_certificates (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER REFERENCES users(id),
            class_id        INTEGER REFERENCES training_classes(id),
            enrollment_id   INTEGER REFERENCES training_enrollments(id),
            score           INTEGER NOT NULL DEFAULT 0,
            certificate_id  TEXT NOT NULL,
            issued_at       TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, class_id)
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
    # Seed default training class if none exist
    try:
        tc = db.execute('SELECT COUNT(*) FROM training_classes').fetchone()[0]
        if tc == 0:
            db.execute('''INSERT INTO training_classes (title, description, price_cents, status) VALUES (?,?,?,?)''',
                       ('Flood Adjusting Fundamentals',
                        'Complete training for aspiring flood adjusters. Covers NFIP guidelines, water damage classification, claim documentation, using FloodClaims Pro platform, and passing the certification exam.',
                        0, 'active'))
            class_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
            # Seed lessons
            lessons = [
                ('Introduction to Flood Adjusting', 'Flood adjusting is a specialized field within the insurance industry that focuses on assessing and documenting damage caused by flooding events. As a flood adjuster, you will be responsible for inspecting properties, documenting damage, estimating repair costs, and working with policyholders through the claims process.\n\n<h4>What You Will Learn</h4>\n<ul>\n<li>The role and responsibilities of a flood insurance adjuster</li>\n<li>How the National Flood Insurance Program (NFIP) works</li>\n<li>Types of flood zones and their significance</li>\n<li>Insurance policy coverage limits and exclusions</li>\n</ul>\n\n<h4>Career Outlook</h4>\n<p>Flood adjusting offers both full-time and contract opportunities. During catastrophic events, demand for qualified adjusters increases significantly. Experienced flood adjusters can earn $500-$1,000+ per day during deployment periods.</p>', 0, ''),
                ('Water Damage Classification', 'Flood damage is categorized by water category and class. Understanding these classifications is essential for accurate claim documentation.\n\n<h4>Water Categories</h4>\n<ul>\n<li><strong>Category 1 (Clean Water):</strong> Originates from a sanitary source. Examples: broken water supply lines, tub or sink overflows.</li>\n<li><strong>Category 2 (Gray Water):</strong> Contains significant contamination. Examples: dishwasher overflow, sump pump failure, toilet overflow (urine only).</li>\n<li><strong>Category 3 (Black Water):</strong> Grossly contaminated. Examples: sewage, seawater, river water, storm surge.</li>\n</ul>\n\n<h4>Water Classes (by evaporation rate)</h4>\n<ul>\n<li><strong>Class 1:</strong> Least affected. Only a portion of a room is affected.</li>\n<li><strong>Class 2:</strong> Affecting entire room. 12-24 inches up walls.</li>\n<li><strong>Class 3:</strong> Highest evaporation rate. Ceiling and walls fully saturated.</li>\n<li><strong>Class 4:</strong> Specialty drying. Hardwood, concrete, plaster — low permeance materials.</li>\n</ul>', 1, ''),
                ('NFIP & FEMA Guidelines', 'The National Flood Insurance Program (NFIP) is the primary provider of flood insurance in the United States, administered by FEMA.\n\n<h4>Key Policy Facts</h4>\n<ul>\n<li><strong>Residential Building Coverage:</strong> Up to $250,000</li>\n<li><strong>Residential Contents Coverage:</strong> Up to $100,000</li>\n<li><strong>Commercial Building Coverage:</strong> Up to $500,000</li>\n<li><strong>Contents (Commercial):</strong> Up to $500,000</li>\n<li><strong>Waiting Period:</strong> 30 days before coverage begins</li>\n<li><strong>Deductible:</strong> Separate building and contents deductibles</li>\n</ul>\n\n<h4>Important Forms</h4>\n<ul>\n<li><strong>Proof of Loss (Form 81-31):</strong> Must be filed within 60 days</li>\n<li><strong>Elevation Certificate:</strong> Documents BFE and building elevation</li>\n<li><strong>Adjuster\'s Damage Inspection Report:</strong> Your primary documentation</li>\n</ul>', 2, ''),
                ('FloodClaims Pro Platform Training', 'FloodClaims Pro is an integrated platform for managing flood insurance claims from start to finish.\n\n<h4>Platform Features</h4>\n<ul>\n<li><strong>Dashboard:</strong> View all claims, their status, and priority at a glance</li>\n<li><strong>Pipeline:</strong> Kanban-style board for tracking claim progress through stages</li>\n<li><strong>Inspection Scheduler:</strong> Schedule and manage property inspections</li>\n<li><strong>Photo Analysis:</strong> AI-powered photo assessment that automatically identifies and documents damage</li>\n<li><strong>Report Generation:</strong> Create professional reports with one click</li>\n<li><strong>Client Portal:</strong> Allow customers to upload photos and track claim status</li>\n<li><strong>Compliance Checker:</strong> Ensures all required fields are completed</li>\n</ul>\n\n<h4>Workflow</h4>\n<p>1. Create claim → 2. Inspect property → 3. Document with photos → 4. AI analysis → 5. Write report → 6. Submit package</p>', 3, ''),
                ('Claim Documentation & Report Writing', 'Proper documentation is the foundation of every successful flood claim. Your reports must be thorough, accurate, and meet NFIP requirements.\n\n<h4>Essential Documentation</h4>\n<ul>\n<li>Flood water line heights (interior and exterior)</li>\n<li>Photos of all affected areas (minimum 20-30 per property)</li>\n<li>Room-by-room damage assessments</li>\n<li>Contents inventory with pre-loss condition</li>\n<li>Elevation certificate (if available)</li>\n<li>Previous flood claim history</li>\n</ul>\n\n<h4>Report Structure</h4>\n<p>1. Property Information → 2. Flood Event Details → 3. Room-by-Room Assessment → 4. Photo Documentation → 5. Damage Summary → 6. Recommendations</p>', 4, ''),
            ]
            for title, content, order, video_url in lessons:
                db.execute('INSERT INTO training_lessons (class_id, title, content, lesson_order, video_url) VALUES (?,?,?,?,?)',
                           (class_id, title, content, order, video_url))
            _seed_training_questions(db, class_id)
    except Exception:
        pass  # Tables may not exist yet on older databases; migration will handle it
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

app.jinja_env.globals['get_setting'] = get_setting

def set_setting(key, value):
    db = sqlite3.connect(DB_PATH)
    db.execute(
        'INSERT INTO settings (key, value) VALUES (?,?) '
        'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
        (key, value))
    db.commit()
    db.close()

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

def _migrate_training_tables():
    """Create training and exam tables."""
    db = sqlite3.connect(DB_PATH)
    db.executescript('''
        CREATE TABLE IF NOT EXISTS training_modules (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            module_num  INTEGER NOT NULL,
            title       TEXT NOT NULL,
            slug        TEXT NOT NULL UNIQUE,
            content     TEXT NOT NULL,
            duration_min INTEGER DEFAULT 30,
            sort_order  INTEGER DEFAULT 0,
            is_active   INTEGER DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS exam_sessions (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_name  TEXT NOT NULL,
            candidate_email TEXT NOT NULL,
            session_token   TEXT UNIQUE NOT NULL,
            questions_json  TEXT NOT NULL,
            answers_json    TEXT DEFAULT '{}',
            score           INTEGER DEFAULT NULL,
            total_questions INTEGER DEFAULT 0,
            is_completed    INTEGER DEFAULT 0,
            is_practice     INTEGER DEFAULT 1,
            created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
            completed_at    TEXT DEFAULT NULL
        );
        CREATE TABLE IF NOT EXISTS adjuster_applications_v2 (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            name            TEXT NOT NULL,
            email           TEXT NOT NULL,
            phone           TEXT DEFAULT '',
            state           TEXT NOT NULL,
            licensed        INTEGER DEFAULT 0,
            license_number  TEXT DEFAULT '',
            exam_score      INTEGER DEFAULT NULL,
            exam_session_id INTEGER DEFAULT NULL,
            status          TEXT DEFAULT 'interested',
            invited_by      TEXT DEFAULT '',
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


def _seed_training_modules():
    """Seed default training modules if table is empty."""
    db = sqlite3.connect(DB_PATH)
    count = db.execute('SELECT COUNT(*) FROM training_modules').fetchone()[0]
    if count > 0:
        db.close()
        return
    modules = [
        (1, "Flood Damage Fundamentals", "flood-damage-fundamentals", """<h2>Flood Damage Fundamentals</h2>
<p>Welcome to the first module in your flood adjuster training. This course covers the essential knowledge you need to begin assessing flood damage professionally.</p>
<h3>What is Flood Damage?</h3>
<p>Flood damage refers to the destruction caused by water entering a building from external sources — river overflow, storm surge, heavy rainfall, or rapid snowmelt. Unlike internal water damage, flood water contacts the ground and may carry contaminants.</p>
<h3>The Restoration Industry</h3>
<p>The flood restoration industry includes emergency services (water extraction, board-up), drying & dehumidification, demolition, reconstruction, and contents cleaning.</p>
<h3>Key Terminology</h3>
<ul>
<li><strong>NFIP:</strong> National Flood Insurance Program — FEMA's flood insurance program</li>
<li><strong>WYO:</strong> Write Your Own — private insurers that sell NFIP policies</li>
<li><strong>ICC:</strong> Increased Cost of Compliance — coverage for elevation/floodproofing (up to $30K)</li>
<li><strong>SFHA:</strong> Special Flood Hazard Area — zones A and V</li>
<li><strong>BFE:</strong> Base Flood Elevation — computed floodwater elevation</li>
<li><strong>RCBAP:</strong> Residential Condominium Building Association Policy</li>
</ul>
<h3>Types of Flood Events</h3>
<ul>
<li><strong>Riverine:</strong> Rivers overflowing — slow onset, widespread</li>
<li><strong>Flash:</strong> Rapid onset from intense rainfall — very dangerous</li>
<li><strong>Coastal:</strong> Storm surge from hurricanes</li>
<li><strong>Urban:</strong> Overwhelmed drainage systems</li>
</ul>
<h3>Major NC Flood Events</h3>
<p>Hurricane Floyd (1999), Matthew (2016), Florence (2018), Dorian (2019), Helene (2024). NC has the 3rd most repetitive loss properties in the US.</p>""", 30, 1),
        (2, "Water Categories & Classes", "water-categories-classes", """<h2>Water Categories & Classes</h2>
<p>Understanding water classification is critical for proper damage assessment and determining remediation procedures.</p>
<h3>Water Categories (Contamination Level)</h3>
<ul>
<li><strong>Category 1 — Clean Water:</strong> From a broken water line, faucet, or rainwater. No significant contamination. Lowest risk.</li>
<li><strong>Category 2 — Grey Water:</strong> Contains significant chemical, biological, or physical contamination. Toilet overflow (urine only), dishwasher overflow, sump pump failure. Can cause discomfort or illness if consumed.</li>
<li><strong>Category 3 — Black Water:</strong> Grossly contaminated. Sewage, floodwater, river water, storm surge, standing water that has become bacterial. Can cause serious illness or death.</li>
</ul>
<h3>Water Classes (Amount of Water & Evaporation Rate)</h3>
<ul>
<li><strong>Class 1 — Least Water:</strong> Only a portion of a room affected. Materials have absorbed minimal moisture. Fastest drying time.</li>
<li><strong>Class 2 — Large Amount:</strong> Carpets and cushions affected. Water wicked up walls 12-24 inches. Moisture in structural materials.</li>
<li><strong>Class 3 — Greatest Amount:</strong> Water from above. Ceiling, walls, insulation, carpet, subfloor — everything is saturated.</li>
<li><strong>Class 4 — Specialty Drying:</strong> Materials with low permeability — hardwood, concrete, plaster, gypcrete. Requires specialized low-humidity drying.</li>
</ul>
<h3>How They're Used Together</h3>
<p>A single claim may have multiple category/class combinations across different rooms. For example: a basement with Category 3 water would require full PPE and anti-microbial treatment, while a kitchen with Category 2 water from a dishwasher needs less aggressive remediation.</p>""", 25, 2),
        (3, "NFIP & FEMA Guidelines", "nfip-fema-guidelines", """<h2>NFIP & FEMA Guidelines</h2>
<p>The National Flood Insurance Program is the primary source of flood insurance in the United States. As an adjuster, you must understand how it works.</p>
<h3>NFIP Policy Basics</h3>
<ul>
<li><strong>Residential coverage limits:</strong> $250,000 building / $100,000 contents</li>
<li><strong>Commercial coverage limits:</strong> $500,000 building / $500,000 contents</li>
<li><strong>Waiting period:</strong> 30 days for new policies to take effect (exceptions: renewals, map changes, mortgage closings)</li>
<li><strong>Deductibles:</strong> Separate building and contents deductibles. Higher deductibles in SFHAs</li>
</ul>
<h3>WYO Companies</h3>
<p>Write Your Own companies are private insurers that sell and service NFIP policies. The federal government underwrites the risk. Major WYO companies include Allstate, USAA, Assurant, and others.</p>
<h3>Claims Process</h3>
<ol>
<li>Policyholder contacts insurer to report loss</li>
<li>Insurance company assigns adjuster</li>
<li>Adjuster inspects property, documents damage</li>
<li>Proof of Loss filed within 60 days</li>
<li>Claim settled and payment issued</li>
</ol>
<h3>ICC Coverage</h3>
<p>Increased Cost of Compliance — up to $30,000 for buildings declared substantially damaged. Covers elevation, relocation, or demolition to bring building into compliance with current floodplain regulations.</p>
<h3>Flood Zones</h3>
<ul>
<li><strong>Zone V:</strong> Coastal high-risk (wave action)</li>
<li><strong>Zone VE:</strong> Coastal high-risk with detailed mapping</li>
<li><strong>Zone A:</strong> Inland high-risk</li>
<li><strong>Zone AE:</strong> Inland high-risk with detailed mapping</li>
<li><strong>Zone X:</strong> Moderate to low risk</li>
</ul>""", 40, 3),
        (4, "Damage Assessment & Documentation", "damage-assessment", """<h2>Damage Assessment & Documentation</h2>
<p>Thorough documentation is the foundation of every successful flood claim. Missing details can result in underpayment or denied claims.</p>
<h3>Initial Inspection Steps</h3>
<ol>
<li><strong>Safety first:</strong> Check for structural damage, electrical hazards, gas leaks, mold</li>
<li><strong>Establish water source:</strong> Where did the water come from? Category determination</li>
<li><strong>Document the water line:</strong> Photograph and measure water stains on walls</li>
<li><strong>Room-by-room survey:</strong> Systematic documentation of every affected room</li>
</ol>
<h3>Photo Documentation</h3>
<ul>
<li>Wide shots of each room showing overall damage</li>
<li>Close-ups of specific damage areas</li>
<li>Water line measurements with tape measure visible</li>
<li>Serial numbers on damaged appliances and equipment</li>
<li>Before photos if available</li>
</ul>
<h3>Building Materials to Identify</h3>
<ul>
<li><strong>Drywall:</strong> Note wicking height, staining, bubbling paint</li>
<li><strong>Flooring:</strong> Material type, damage type (buckling, warping, delamination), affected sqft</li>
<li><strong>Insulation:</strong> Fiberglass (unsalvageable if wet), spray foam (ok), cellulose (replace)</li>
<li><strong>Electrical:</li>
<li><strong>HVAC:</strong> Ductwork, air handler, thermostat damage</li>
<li><strong>Cabinets:</strong> Base vs. upper, material, water line height</li>
</ul>
<h3>Mold Assessment</h3>
<p>Mold can begin growing in 24-48 hours. Document any visible mold — location, color, coverage area, growth stage. Recommend professional mold remediation for Category 3 or large areas.</p>""", 35, 4),
        (5, "Adjuster Licensing & Certification", "adjuster-licensing", """<h2>Adjuster Licensing & Certification</h2>
<p>Each state has its own requirements for insurance adjuster licensing. Here's what you need to know to get started.</p>
<h3>State Licensing</h3>
<p>Most states require adjusters to be licensed through the state Department of Insurance. Requirements typically include:</p>
<ul>
<li>Complete a pre-licensing education course (20-40 hours)</li>
<li>Pass the state adjuster licensing exam</li>
<li>Submit application and fees</li>
<li>Background check (some states)</li>
<li>Maintain continuing education (typically 24 hours every 2 years)</li>
</ul>
<h3>Designated Home State (DHS)</h3>
<p>If you're licensed in your home state, most other states will grant you a license through reciprocity. The National Insurance Producer Registry (NIPR) handles multi-state licensing.</p>
<h3>FEMA Adjuster Exam</h3>
<p>FEMA doesn't license adjusters, but they do offer training and certification for adjusters who work on NFIP claims. The FEMA adjuster exam covers NFIP policy details, claims procedures, and documentation requirements.</p>
<h3>Independent vs. Staff Adjuster</h3>
<ul>
<li><strong>Staff adjuster:</strong> Employed by one insurance company. Steady work, benefits, company training.</li>
<li><strong>Independent adjuster:</strong> Self-employed, contracted by multiple companies. More flexibility, storm chasing opportunities, higher earning potential during catastrophe events.</li>
</ul>
<h3>Career Path</h3>
<p>Many successful adjusters start during storm events (hurricane season), build experience and relationships, then transition to full-time independent work. Average income for experienced independent adjusters ranges from $60K-$120K+, with some earning significantly more during major catastrophe events.</p>""", 20, 5),
    ]
    db.executemany(
        'INSERT OR IGNORE INTO training_modules (module_num, title, slug, content, duration_min, sort_order) VALUES (?,?,?,?,?,?)',
        modules
    )
    db.commit()
    db.close()

# _seed_training_modules() now called lazily in get_db()

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
        ]
        for col, typedef in extras:
            if col not in cols:
                db.execute(f'ALTER TABLE photos ADD COLUMN {col} {typedef}')
        db.commit()
        db.close()
    except Exception as e:
        print(f'migrate_batch_photo_columns error: {e}')

# migrate_batch_photo_columns() now called lazily in get_db()

def _seed_training_questions(db, class_id):
    """Seed flood adjustment exam questions for a class."""
    existing = db.execute('SELECT COUNT(*) FROM training_exam_questions WHERE class_id=?', (class_id,)).fetchone()[0]
    if existing:
        return
    questions = [
        ("What is the maximum coverage under the NFIP for a single-family dwelling?", "$100,000 building / $100,000 contents", "$250,000 building / $100,000 contents", "$500,000 building / $250,000 contents", "$250,000 building / $250,000 contents", "b"),
        ("How many days does a policyholder typically have to file a Proof of Loss?", "30 days", "60 days", "90 days", "1 year", "b"),
        ("Which flood zone designation indicates high-risk coastal areas?", "Zone A", "Zone AE", "Zone V", "Zone X", "c"),
        ("What does ICC (Increased Cost of Compliance) coverage help pay for?", "Temporary housing", "Building elevation or demolition costs", "Legal fees", "Landscaping", "b"),
        ("What is the standard NFIP waiting period before coverage begins?", "15 days", "30 days", "45 days", "60 days", "b"),
        ("Which form is used to report flood damage to NFIP?", "Form 81-31 (Proof of Loss)", "Form 1040", "HUD-1", "ACORD 25", "a"),
        ("What is the maximum Increased Cost of Compliance (ICC) benefit?", "$15,000", "$20,000", "$30,000", "$50,000", "c"),
        ("What type of flood damage is typically NOT covered by NFIP?", "Storm surge flooding", "Mudflow", "Sewer backup (without general flooding)", "River overflow", "c"),
        ("Which elevation certificate component shows the lowest floor elevation?", "Section A - Property Information", "Section B - Flood Zone", "Section C - Building Elevation", "Section D - Surveyor Certification", "c"),
        ("What is the deductible structure for NFIP policies per building and contents?", "One deductible per occurrence", "Separate building and contents deductibles", "No deductible required", "Flat $500 deductible", "b"),
        ("What does the term 'Base Flood Elevation' (BFE) represent?", "The highest recorded flood level", "The computed elevation floodwater rises to in a 1% annual chance flood", "The lowest point of the property", "The average rainfall per year", "b"),
        ("Which coverage type does NOT exist in a standard NFIP policy?", "Building property", "Personal property (contents)", "Additional living expenses", "Debris removal", "c"),
        ("How is actual cash value (ACV) calculated for flood claims?", "Replacement cost", "Replacement cost minus depreciation", "Original purchase price", "Market value of the property", "b"),
        ("What is the maximum personal property coverage under NFIP for residential?", "$100,000", "$200,000", "$250,000", "$500,000", "a"),
        ("Which program backs the NFIP flood insurance policies?", "Private reinsurers", "Federal Emergency Management Agency (FEMA)", "State governments", "The National Weather Service", "b"),
        ("What does 'substantial damage' mean in floodplain management?", "Damage > 25% of market value", "Damage > 50% of market value", "Damage > 75% of market value", "Total loss only", "b"),
        ("How often must an elevation certificate be renewed?", "Every year", "Every 5 years", "Every 10 years", "No expiration — valid unless structure changes", "d"),
        ("What is the first step when inspecting a flood-damaged property?", "Take photographs", "Review the policy and coverage", "Measure water lines", "Interview the homeowner", "b"),
        ("What constitutes 'general flooding' required for NFIP coverage?", "One property affected", "Two or more properties and 2+ acres", "Any water entry from any source", "Federal disaster declaration only", "b"),
        ("What is the purpose of a Damage Inspection Report?", "To finalize the claim", "To document findings and estimate damage", "To approve the claim", "To close the file", "b"),
    ]
    db.executemany('INSERT INTO training_exam_questions (class_id,question,option_a,option_b,option_c,option_d,correct_answer) VALUES (?,?,?,?,?,?,?)',
                    [(class_id, q[0], q[1], q[2], q[3], q[4], q[5]) for q in questions])
    db.commit()

