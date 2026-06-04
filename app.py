import os, sqlite3, secrets, hashlib, json, datetime, pathlib, base64, io, zipfile, xml.etree.ElementTree as _ET
try:
    import bcrypt as _bcrypt
    BCRYPT_OK = True
except ImportError:
    BCRYPT_OK = False
from datetime import timedelta, timezone
from functools import wraps
from flask import (Flask, render_template, request, redirect, url_for,
                   session, flash, jsonify, g, send_from_directory, make_response)
from werkzeug.utils import secure_filename
import requests as _req

# Optional deps — degrade gracefully if not installed yet
WEASYPRINT_OK = False  # Disabled — not compatible with Railway environment

try:
    import stripe as _stripe
    STRIPE_OK = True
except Exception:
    STRIPE_OK = False

try:
    from sendgrid import SendGridAPIClient
    from sendgrid.helpers.mail import Mail
    SENDGRID_OK = True
except Exception:
    SENDGRID_OK = False

app = Flask(__name__)

# ── Cross-app: Pet Vet AI photo analysis ──────────────────────────────────────
# HARDCODED: Pet Vet AI URL (no EcDash lookup needed, no network calls on startup)
_PET_VET_AI_URL = "https://ai-vet-tech.alexanderai.site"

def _call_pet_vet_ai(path, data=None, method='POST', timeout=30):
    """Call Pet Vet AI directly via hardcoded URL. No EcDash dependency."""
    import urllib.request, urllib.error, json as _json
    url = _PET_VET_AI_URL + path
    try:
        body = _json.dumps(data).encode() if data else None
        headers = {'Content-Type': 'application/json'}
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return _json.loads(resp.read().decode())
    except Exception as e:
        import sys
        print(f"[pet_vet_ai] call failed (non-fatal): {e}", file=sys.stderr)
        return None

APP_NAME    = 'FloodClaims Pro'
APP_VERSION = '1.0'
import time as _uptime_time
_APP_START_TIME = _uptime_time.time()

def _get_secret_key():
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
        import secrets as _sec
        key = _sec.token_hex(32)
        with open(key_file, 'w') as f:
            f.write(key)
        return key
    except Exception:
        import secrets as _sec
        return _sec.token_hex(32)


# ── Secret key: stable across deploys ────────────────────────────────────────
# Railway: set SECRET_KEY as an env var (one-time). Falls back to a file-based
# key so at minimum it survives restarts on the same volume.
_SECRET_KEY = os.environ.get('SECRET_KEY', '')
if not _SECRET_KEY:
    _KEY_FILE = '/data/.secret_key'
    try:
        os.makedirs('/data', exist_ok=True)
        if os.path.exists(_KEY_FILE):
            with open(_KEY_FILE) as _f:
                _SECRET_KEY = _f.read().strip()
        if not _SECRET_KEY:
            _SECRET_KEY = secrets.token_hex(32)
            with open(_KEY_FILE, 'w') as _f:
                _f.write(_SECRET_KEY)
    except Exception:
        # Last resort: derive a stable key from a fixed string + Railway service ID
        # so at least it's consistent within the same Railway service even without a volume.
        import hashlib
        _svc = os.environ.get('RAILWAY_SERVICE_ID', 'floodclaim-pro-default')
        _SECRET_KEY = hashlib.sha256(f'floodclaim-secret-{_svc}'.encode()).hexdigest()

app.secret_key = _get_secret_key()

# ── Session config ────────────────────────────────────────────────────────────
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
app.config['SESSION_COOKIE_HTTPONLY']    = True
app.config['SESSION_COOKIE_SAMESITE']   = 'Lax'
# Keep Secure=False — Railway's edge terminates TLS; the cookie travels over
# plain HTTP between the edge and the app container, so Secure would silently
# drop it. Railway enforces HTTPS at the edge already.
app.config['SESSION_COOKIE_SECURE']     = os.environ.get('RAILWAY_ENVIRONMENT') is not None

# ── CSRF protection ───────────────────────────────────────────────────────────────
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

# Expose to all Jinja2 templates as {{ csrf_token() }}
app.jinja_env.globals['csrf_token'] = _get_csrf_token

# ── Bot / scanner sink ──────────────────────────────────────────────────────
# Returns 410 Gone for paths that scanners probe but will never exist here.
_BOT_PATHS = [
    '/wp-admin/', '/wp-login.php', '/wp-cron.php', '/wp-includes/',
    '/wp-content/', '/xmlrpc.php', '/wp-admin/install.php',
    '/wp-json/', '/.env', '/.git/', '/config.php', '/setup.php',
    '/install.php', '/phpmyadmin/', '/pma/', '/admin/config.php',
    '/sitemap.xml', '/sitemap_index.xml', '/robots.txt.bak',
    '/.htaccess', '/web.config', '/backup/', '/administrator/',
    '/joomla/', '/drupal/', '/typo3/',
]

@app.before_request
def _block_bot_paths():
    path = request.path
    if any(path == p or path.startswith(p) for p in _BOT_PATHS):
        return '', 410
# ────────────────────────────────────────────────────────────────────────────


@app.before_request
def _csrf_protect():
    """Enforce CSRF on all state-changing requests."""
    if request.method in ('POST', 'PUT', 'DELETE', 'PATCH'):
        if request.path.startswith('/api/'):
            return  # API routes use token auth, skip CSRF
        if request.path.startswith('/portal/'):
            return  # Public client portal — no session/CSRF
        import re
        if re.search(r'/claims/\d+/sign$', request.path):
            return  # Public signature endpoint — no session
        if not _validate_csrf():
            from flask import abort
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

DATA_DIR    = os.environ.get('RAILWAY_VOLUME_MOUNT_PATH', '/data')
DB_PATH     = os.path.join(DATA_DIR, 'floodclaim.db')
UPLOAD_DIR  = os.path.join(DATA_DIR, 'uploads')
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)

ADMIN_EMAIL    = os.environ.get('ADMIN_EMAIL', 'admin@floodclaimpro.com')
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'FloodAdmin2026!')
OPENROUTER_KEY = os.environ.get('OPENROUTER_API_KEY', '')

ALLOWED_EXT = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXT

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

@app.teardown_appcontext
def close_db(e=None):
    db = g.pop('db', None)
    if db: db.close()

@app.after_request
def security_headers(response):
    response.headers.setdefault('X-Frame-Options', 'SAMEORIGIN')
    response.headers.setdefault('X-Content-Type-Options', 'nosniff')
    response.headers.setdefault('X-XSS-Protection', '1; mode=block')
    response.headers.setdefault('Referrer-Policy', 'strict-origin-when-cross-origin')
    response.headers.setdefault('Permissions-Policy', 'geolocation=(), microphone=(), camera=()')
    response.headers.setdefault(
        'Content-Security-Policy',
        "default-src 'self' https: data: blob:; script-src 'self' 'unsafe-inline' https://unpkg.com; style-src 'self' https: 'unsafe-inline'; img-src 'self' https: data: blob:; font-src 'self' https://fonts.googleapis.com https://fonts.gstatic.com; connect-src 'self' https://unpkg.com https://openrouter.ai https://api.stripe.com; frame-src 'self' https://js.stripe.com https://maps.google.com;"
    )
    response.headers.setdefault('Strict-Transport-Security', 'max-age=31536000; includeSubDomains')
    return response

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


# ── Integrations: FEMA, Maps, Email (helpers only — routes defined after auth) ──

def lookup_fema_flood_zone(address):
    """Look up FEMA flood zone for an address using FEMA's free API."""
    try:
        # Geocode address via Census Bureau (free, no key)
        geo_url = 'https://geocoding.geo.census.gov/geocoder/locations/onelineaddress'
        r = _req.get(geo_url, params={'address': address, 'benchmark': 'Public_AR_Current', 'format': 'json'}, timeout=8)
        matches = r.json().get('result', {}).get('addressMatches', [])
        if not matches:
            return {}
        lat = matches[0]['coordinates']['y']
        lng = matches[0]['coordinates']['x']
        # FEMA flood zone via NFHL API
        fema_url = 'https://hazards.fema.gov/gis/nfhl/rest/services/public/NFHL/MapServer/28/query'
        fr = _req.get(fema_url, params={
            'geometry': f'{lng},{lat}', 'geometryType': 'esriGeometryPoint',
            'inSR': '4326', 'spatialRel': 'esriSpatialRelIntersects',
            'outFields': 'FLD_ZONE,DFIRM_ID', 'returnGeometry': 'false', 'f': 'json'
        }, timeout=8)
        features = fr.json().get('features', [])
        zone = features[0]['attributes']['FLD_ZONE'] if features else 'Unknown'
        map_num = features[0]['attributes']['DFIRM_ID'] if features else ''
        maps_url = f'https://www.google.com/maps/embed/v1/place?key=AIzaSyD-9tSrke72PouQMnMX-a7eZSW0jkFMBWY&q={lat},{lng}&zoom=15'
        return {'lat': lat, 'lng': lng, 'flood_zone': zone, 'fema_map_number': map_num, 'maps_embed_url': maps_url}
    except Exception as e:
        print(f'FEMA lookup error: {e}')
        return {}


def send_email(to_email, subject, html_body):
    """Send email via SendGrid if configured, else log."""
    sg_key = get_setting('sendgrid_api_key') or os.environ.get('SENDGRID_API_KEY', '')
    from_email = get_setting('from_email') or os.environ.get('FROM_EMAIL', 'noreply@floodclaimpro.com')
    if not sg_key or not SENDGRID_OK:
        print(f'[EMAIL] To: {to_email} | Subject: {subject} | (SendGrid not configured)')
        return False
    try:
        msg = Mail(from_email=from_email, to_emails=to_email, subject=subject, html_content=html_body)
        SendGridAPIClient(sg_key).send(msg)
        return True
    except Exception as e:
        print(f'SendGrid error: {e}')
        return False


def notify_client_status_change(claim, new_status):
    """Email client when claim status changes."""
    if not claim['client_email']:
        return
    subject = f'FloodClaims Pro — Your Claim {claim["claim_number"]} Update'
    html = f'''<div style="font-family:sans-serif;max-width:600px;margin:0 auto">
        <h2 style="color:#0a1628">FloodClaims Pro Update</h2>
        <p>Hello {claim["client_name"]},</p>
        <p>Your flood damage claim <strong>{claim["claim_number"]}</strong> has been updated.</p>
        <p style="background:#f0fdf4;padding:12px;border-radius:8px;border-left:4px solid #10b981">
            <strong>New Status: {new_status}</strong></p>
        <p>If you have questions, please contact your adjuster directly.</p>
        <hr style="margin:24px 0;border:none;border-top:1px solid #e2e8f0">
        <p style="font-size:12px;color:#94a3b8">FloodClaims Pro · Professional Flood Damage Assessment</p>
    </div>'''
    send_email(claim['client_email'], subject, html)

# ── Auth ──────────────────────────────────────────────────────────────────────
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            flash('Your session expired — please log in again.', 'warning')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get('role') not in ('admin', 'manager'):
            flash('Admin access required.', 'error')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated

def manager_required(f):
    """Manager can manage team and adjusters but not change app settings."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if session.get('role') not in ('admin', 'manager'):
            flash('Manager access required.', 'error')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated

# ── Helpers ───────────────────────────────────────────────────────────────────
def gen_claim_number():
    prefix = datetime.datetime.now().strftime('%Y%m')
    suffix = secrets.token_hex(3).upper()
    return f'FC-{prefix}-{suffix}'

def recalc_claim(claim_id):
    db = get_db()
    rooms = db.execute('SELECT id FROM rooms WHERE claim_id=? AND deleted_at IS NULL', (claim_id,)).fetchall()
    total = 0
    for room in rooms:
        rt = db.execute('SELECT COALESCE(SUM(total),0) as s FROM line_items WHERE room_id=? AND deleted_at IS NULL',
                        (room['id'],)).fetchone()['s']
        db.execute('UPDATE rooms SET subtotal=? WHERE id=?', (rt, room['id']))
        total += rt
    db.execute('UPDATE claims SET total_estimate=?, updated_at=CURRENT_TIMESTAMP WHERE id=?',
               (total, claim_id))
    db.commit()

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
def get_willie_token():
    """Get or auto-generate the Aquila API token."""
    token = get_setting('willie_api_token')
    if not token:
        token = secrets.token_urlsafe(32)
        set_setting('willie_api_token', token)
    return token

def willie_auth():
    """Verify Willie API token from Authorization header."""
    auth  = request.headers.get('Authorization', '')
    token = auth.replace('Bearer ', '').strip() if auth.startswith('Bearer ') else ''
    return bool(token and token == get_setting('willie_api_token'))


# ── AI Adjuster Estimate — Async job system ────────────────────────────────────
import threading

def _run_estimate_job(job_id, claim_id, claim, rooms, photo_analyses, photo_section,
                      room_section, model, key):
    """Background thread: runs the AI call and writes result to estimate_jobs table."""
    import sqlite3 as _sq3
    db = _sq3.connect(DB_PATH)
    db.row_factory = _sq3.Row
    def _update(progress, msg, status='pending'):
        db.execute('UPDATE estimate_jobs SET progress=?, progress_msg=?, status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?',
                   (progress, msg, status, job_id))
        db.commit()
    try:
        _update(5, 'Building pricing knowledge base...')
        PRICING_KB = _build_pricing_kb()
        _update(10, 'Preparing claim data and photo analysis...')
        prompt = _build_estimate_prompt(claim, room_section, photo_section, PRICING_KB)
        photo_count = len(photo_analyses) if photo_analyses else 0
        _update(20, f'Calling AI model ({photo_count} photos to analyze)...')
        # Simulate incremental progress during the AI call
        import time as _time
        estimate = call_openrouter([{'role': 'user', 'content': prompt}], model, key, max_tokens=4000)
        _update(90, 'Processing and formatting estimate results...')
        # Update claim total_estimate with AI-recommended amount if parseable
        try:
            import re as _re
            # Look for GRAND TOTAL or similar in the estimate
            total_matches = [_re.search(r'GRAND TOTAL[:\s]*\$?([\d,]+\.?\d*)', estimate, _re.IGNORECASE),
                           _re.search(r'(?:Total|Grand Total|Claim Amount)[:\s]*\$?([\d,]+\.?\d*)', estimate, _re.IGNORECASE)]
            for m in total_matches:
                if m:
                    ai_total = float(m.group(1).replace(',', ''))
                    if ai_total > 0:
                        db.execute('UPDATE claims SET total_estimate=? WHERE id=?', (ai_total, claim_id))
                        break
        except Exception:
            pass
        db.execute('UPDATE estimate_jobs SET status=?, progress=100, progress_msg=?, result=?, updated_at=CURRENT_TIMESTAMP WHERE id=?',
                   ('done', 'Estimate complete!', estimate, job_id))
        db.commit()
    except Exception as e:
        db.execute('UPDATE estimate_jobs SET status=?, progress=0, progress_msg=?, error=?, updated_at=CURRENT_TIMESTAMP WHERE id=?',
                   ('error', 'Estimate failed', str(e), job_id))
        db.commit()
    finally:
        db.close()


def _build_pricing_kb():
    return """
=== 2026 FLOOD RESTORATION PRICING REFERENCE (USE THESE RATES) ===

NATIONAL AVERAGES (2026 — Palm Build, NuBilt, Angi, Xactimate):
- Average claim payout: $10,234–$11,605
- Full restoration (mitigation + rebuild): $5,000–$16,000
- Mitigation: $3.00–$7.50/sf | Full rebuild: $20.00–$37.00/sf
- Myrtle Beach / SC rate: $14–$16/sf cleanup, $20–$30/sf rebuild
- 1 inch floodwater → ~$25,000 damage (FEMA/NFIP)

WATER CATEGORIES (IICRC):
- Cat 1 (clean): $3.50/sf | Cat 2 (gray): $5.25/sf | Cat 3 (black/flood): $7.50/sf+
- Flood water from outside = ALWAYS Cat 3

MITIGATION (Xactimate 2024–2026):
- Emergency call: $271–$407 EA | Extraction: $0.75–$1.50/sf
- Air mover/24h: $38–$55 EA (1 per 50–100sf) | Dehumidifier/24h: $83–$110 EA
- Antimicrobial: $0.35–$0.75/sf | Moisture mapping: $250 flat
- Content pack-out: $77/hr | Debris/dumpster: $350–$600 EA

TEAR-OUT:
- Drywall Cat3: $1.79/sf | Insulation: $0.91/sf | Baseboard: $0.66/lf
- LVP/vinyl: $1.25–$2.00/sf | Hardwood: $5.82/sf | Tile+mortar: $3.50–$5.00/sf
- Subfloor: $2.00–$3.50/sf

RECONSTRUCTION:
- Drywall 1/2" hung/taped/floated: $3.99–$5.50/sf | Insulation R-19: $1.40–$2.00/sf
- Paint 2 coats: $1.50–$2.50/sf | Baseboard R&R: $5.51/lf
- LVP installed: $4.00–$8.00/sf ($5.50 mid) | Carpet+pad: $3.50–$6.50/sf
- Hardwood: $8.00–$14.00/sf | Tile: $7.00–$12.00/sf | Subfloor: $4.50–$6.00/sf

MOLD: $1,200–$3,800 flat (small) or $15–$30/sf | Encapsulation: $1.00–$2.50/sf
ELECTRICAL: Re-inspection $150–$400 | GFCI R&R $85–$150 EA
CABINETS: Base $175–$350/lf | Upper $125–$250/lf | Countertop $25–$40/lf
DOORS/WINDOWS: Interior door $401–$550 EA | Window $392–$550 EA

O&P + CONTINGENCY (always include):
- Contractor O&P: 20% of subtotal (standard insurance practice)
- Sales tax on materials: ~8% (SC rate)
- Contingency: 10% of subtotal

TYPICAL TOTALS: Single room $8k–$18k | Two rooms $15k–$30k | Full floor $25k–$60k
NFIP avg: $10,234 moderate / $66,000 severe

RULES:
1. NEVER estimate below $8,000 when photos show drywall + flooring damage
2. Floodwater from outside = Cat 3 always
3. Peeling drywall in photos = full replacement, NOT patch
4. Visible rotted/torn floor = full room replacement
5. Always include BOTH mitigation AND reconstruction phases
6. Always add O&P (20%) + contingency (10%)
7. Damage >48h old = add mold remediation line items
"""


def _build_estimate_prompt(claim, room_section, photo_section, pricing_kb):
    return f"""You are a licensed public adjuster with 20 years of flood damage experience.
Generate a complete professional insurance estimate using the 2026 pricing reference below.
USE THESE EXACT RATES. Do not guess or use outdated numbers.

{pricing_kb}

=== CLAIM ===
Claim #: {claim['claim_number']}
Client: {claim['client_name']}
Property: {claim['property_address']}
Flood Date: {claim['flood_date']}
Flood Source: {claim.get('flood_source') or 'Not specified'}
Water Category: {claim.get('water_category') or 'Not specified'}
Water Class: {claim.get('water_class') or 'Not specified'}
Water Depth: {claim.get('water_depth_in') or 'Not specified'} inches
Insurance Co: {claim.get('insurance_company') or 'Not specified'}
FEMA Zone: {claim.get('flood_zone') or 'Not determined'}

=== CURRENT ROOMS & LINE ITEMS ===
{room_section}
Current Total: ${claim['total_estimate']:.2f}

=== PHOTO ANALYSIS ===
{photo_section}

=== YOUR TASK ===
1. **PHOTO FINDINGS** — Specific damage per photo (water lines, mold, drywall, flooring, structural). Note water category/class.

2. **COMPLETE LINE-ITEM ESTIMATE** — Both mitigation AND reconstruction phases:
   | Item | Qty | Unit | Unit Cost | Total |
   Mark existing ✅, add missing ➕. Include drying equipment, antimicrobial, debris removal.

3. **ESTIMATE SUMMARY**
   - Subtotal per room
   - Contractor O&P (20%)
   - Sales tax (~8%)
   - Contingency (10%)
   - **GRAND TOTAL** (recommended claim amount)

4. **ADJUSTER NOTES** — Red flags, documentation gaps, is ${claim['total_estimate']:.2f} adequate?

Be thorough — this goes to the insurance company. Low estimates hurt the homeowner."""


@app.route('/claims/<int:claim_id>/ai-estimate', methods=['POST'])
def ai_estimate(claim_id):
    """Start AI estimate job. Returns job_id immediately; client polls /ai-estimate/<job_id>.
    Accepts session login OR Willie API token."""
    # Allow Willie token auth as fallback for cross-origin requests
    if not session.get('user_id'):
        if not willie_auth():
            return jsonify({'ok': False, 'error': 'Session expired — please refresh the page and log in again.'}), 401
    db = get_db()
    claim = db.execute('SELECT * FROM claims WHERE id=?', (claim_id,)).fetchone()
    if not claim:
        return jsonify({'ok': False, 'error': 'Claim not found'}), 404
    claim = dict(claim)  # convert sqlite3.Row → dict so .get() works

    key   = get_setting('openrouter_api_key') or OPENROUTER_KEY
    model = get_setting('ai_chat_model') or get_setting('ai_model', 'openrouter/owl-alpha')
    if not key:
        return jsonify({'ok': False, 'error': 'OpenRouter API key not configured. Go to Settings and add your key.'}), 400

    # Rooms + line items
    rooms = db.execute('SELECT * FROM rooms WHERE claim_id=? AND deleted_at IS NULL ORDER BY id', (claim_id,)).fetchall()
    room_section = ''
    for r in rooms:
        items = db.execute('SELECT * FROM line_items WHERE room_id=? AND deleted_at IS NULL ORDER BY id', (r['id'],)).fetchall()
        item_list = '; '.join([f"{i['description']} x{i['quantity']} {i['unit']} @${i['unit_cost']:.2f}" for i in items]) or 'No items'
        room_section += f"  {r['name']}: {item_list}\n"
    if not room_section:
        room_section = '  No rooms documented yet.\n'

    # Analyze photos (use cached AI descriptions or run fresh)
    photos = [dict(p) for p in db.execute('SELECT * FROM photos WHERE claim_id=? AND deleted_at IS NULL ORDER BY id', (claim_id,)).fetchall()]
    photo_analyses = []
    for photo in photos[:8]:
        photo_path = os.path.join(UPLOAD_DIR, photo['filename'])
        desc = photo.get('ai_description', '') or ''
        # Clear cached error strings so they get retried
        if desc.startswith('AI analysis failed') or desc.startswith('Error'):
            desc = ''
            db.execute('UPDATE photos SET ai_description=NULL WHERE id=?', (photo['id'],))
            db.commit()
        if not desc and os.path.exists(photo_path):
            desc = ai_describe_photo(photo_path)
            if desc:
                db.execute('UPDATE photos SET ai_description=? WHERE id=?', (desc, photo['id']))
                db.commit()
        if desc:
            label = photo.get('caption') or photo['filename']
            photo_analyses.append(f"  [{label}]: {desc}")
    photo_count = len(photos)
    missing_files = sum(1 for p in photos[:8] if not os.path.exists(os.path.join(UPLOAD_DIR, p['filename'])))
    photo_section = '\n'.join(photo_analyses) if photo_analyses else '  No photos uploaded yet.'
    if missing_files > 0:
        photo_section += f'\n  Note: {missing_files} photo file(s) not found on disk.'

    PRICING_KNOWLEDGE_BASE = """
=== 2026 FLOOD RESTORATION PRICING REFERENCE (USE THESE RATES) ===

NATIONAL AVERAGES (2026 data — Palm Build, NuBilt, Angi, Xactimate):
- Average water damage claim payout: $10,234–$11,605
- Typical full restoration (mitigation + rebuild): $5,000–$16,000
- Per sq ft mitigation only: $3.00–$7.50/sf
- Per sq ft full rebuild: $20.00–$37.00/sf
- Myrtle Beach / South Carolina local rate: $14–$16/sf (cleanup), $20–$30/sf (rebuild)
- 1 inch of standing floodwater → ~$25,000 in damage to a typical home (FEMA/NFIP data)

WATER CATEGORIES (IICRC):
- Cat 1 (clean water): $3.50/sf mitigation
- Cat 2 (gray water/appliance): $5.25/sf mitigation
- Cat 3 (black water/floodwater/sewage): $7.50/sf mitigation + biohazard uplift
  → Flood water from outside IS always Cat 3

WATER CLASSES:
- Class 1 (partial room, floors only): 24–48h dry-out
- Class 2 (full room, walls <24" wicking): 48–72h dry-out
- Class 3 (ceiling/walls saturated): 72–96h dry-out
- Class 4 (specialty — brick, hardwood, concrete): 120h+ dry-out

MITIGATION LINE ITEMS (Xactimate-based 2024–2026):
- Emergency service call (business hours): $271–$407 EA
- Water extraction / pumping: $0.75–$1.50/sf
- Air mover (per 24h): $38–$55 EA (typically 1 per 50–100 sf)
- Dehumidifier 70–109 ppd (per 24h): $83–$110 EA (typically 1 per 500–1,000 sf)
- Wall cavity drying — injection type (per 24h): $141 EA
- Antimicrobial treatment: $0.35–$0.50/sf
- Moisture mapping report: $250 flat
- Containment barriers: $0.18/sf
- Content manipulation / pack-out: $77/hr
- Debris hauling (dumpster): $350–$600 EA

DEMOLITION / TEAR-OUT:
- Tear out wet drywall Cat 3 (no bagging): $1.79/sf
- Tear out wet insulation (no bagging): $0.91/sf
- Tear out baseboard: $0.66/lf
- Tear out carpet + pad: $1.05–$1.50/sy (or $0.12–$0.17/sf)
- Tear out LVP/vinyl flooring: $1.25–$2.00/sf
- Tear out non-salvageable hardwood (bagged): $5.82/sf
- Tear out ceramic tile + mortar bed: $3.50–$5.00/sf
- Tear out subfloor (OSB/plywood): $2.00–$3.50/sf

DRYWALL REPLACEMENT:
- 1/2" drywall hung, taped, floated, ready for paint: $3.99–$5.50/sf
- Drywall repair (labor only, Myrtle Beach): $40–$60/hr
- Batt insulation 6" R19: $1.40–$2.00/sf
- Seal/prime + 2 coats paint walls: $1.50–$2.50/sf
- Baseboard 4-1/4" R&R: $5.51/lf
- Seal & paint baseboard: $2.75/lf

FLOORING REPLACEMENT:
- Luxury Vinyl Plank (LVP) installed: $4.00–$8.00/sf (mid-grade $5.50)
- Carpet + pad installed: $3.50–$6.50/sf (mid-grade $4.50)
- Hardwood installed (mid-grade): $8.00–$14.00/sf
- Ceramic/porcelain tile installed: $7.00–$12.00/sf
- Subfloor OSB 3/4" R&R: $4.50–$6.00/sf

MOLD REMEDIATION:
- HEPA air scrubber (per 24h): $80–$115 EA
- Antimicrobial application: $0.35–$0.75/sf
- Mold remediation (contained area): $1,200–$3,800 total; $15–$30/sf for large areas
- Encapsulation coating: $1.00–$2.50/sf

ELECTRICAL / MECHANICAL:
- Electrical safety re-inspection after flood: $150–$400
- GFCI outlet R&R: $85–$150 EA
- Electrical outlet/switch R&R (standard): $45–$90 EA

CABINETS / KITCHEN:
- Base cabinet removal & replace (per LF): $175–$350/lf
- Upper cabinet removal & replace (per LF): $125–$250/lf
- Countertop replace (laminate): $25–$40/lf

DOORS / WINDOWS:
- Interior door unit R&R: $401–$550 EA
- Vinyl window single-hung 9–12 sf R&R: $392–$550 EA
- Door frame/jamb R&R: $254–$350 EA

CONTINGENCY & OVERHEAD:
- Standard contingency: 10–15% of subtotal
- Contractor O&P (overhead & profit): 20% on top of labor + materials (standard insurance practice)
- Sales tax on materials: ~8% (SC rate)

AVERAGE TOTAL COSTS BY CLAIM TYPE (2026 insurance data):
- Single room flood (200–400 sf): $8,000–$18,000
- Two-room flood: $15,000–$30,000
- Full first-floor flood (1,000–1,500 sf): $25,000–$60,000
- Basement flood: $10,000–$30,000
- NFIP average payout for flood claims: $66,000 (severe) / $10,234 (moderate)

KEY RULES FOR ADJUSTER ESTIMATES:
1. NEVER estimate below $8,000 for any claim showing visible drywall damage + flooring damage in 2+ photos
2. Flood water from outside = Cat 3 black water ALWAYS — this triggers biohazard protocols and higher rates
3. Any peeling paint/drywall visible in photos = walls need full replacement, not patch repair
4. Rotted/torn flooring visible = full room flooring replacement, not partial
5. Always include mitigation phase (extraction/drying) AND reconstruction phase in estimate
6. Add 10% contingency + 20% O&P to all estimates
7. If mold risk present (damage >48h old), add mold remediation line items
"""

    prompt = f"""You are a licensed public adjuster and flood damage estimator with 20 years of experience.
Analyze this flood damage claim and produce a complete, professional estimate like you would submit to an insurance company.

You have access to a current 2026 pricing reference — USE THESE EXACT RATES, do not guess or use outdated numbers:
{PRICING_KNOWLEDGE_BASE}

=== CLAIM DETAILS ===
Claim #: {claim['claim_number']}
Client: {claim['client_name']}
Property: {claim['property_address']}
Flood Date: {claim['flood_date']}
Flood Source: {claim.get('flood_source') or 'Not specified'}
Water Category: {claim.get('water_category') or 'Not specified'}
Water Class: {claim.get('water_class') or 'Not specified'}
Water Depth: {claim.get('water_depth_in') or 'Not specified'} inches
Insurance Co: {claim.get('insurance_company') or 'Not specified'}
FEMA Flood Zone: {claim.get('flood_zone') or 'Not determined'}

=== CURRENT ROOMS & LINE ITEMS ===
{room_section}
Current Documented Total: ${claim['total_estimate']:.2f}

=== PHOTO ANALYSIS ===
{photo_section}

=== YOUR TASK ===
As a professional adjuster, provide:

1. 📸 PHOTO FINDINGS
Describe specific damage visible in each photo (water lines, peeling drywall, rotted flooring, mold, structural damage, etc.). Note the water category and class implied by what you see.

2. 📊 COMPLETE LINE-ITEM ESTIMATE
Using the pricing reference above, list EVERY repair needed — both mitigation phase and reconstruction phase:
| Item | Qty | Unit | Unit Cost | Total |
Mark existing items ✅ and new recommended items ➕
Do NOT omit standard line items like drying equipment, antimicrobial treatment, debris removal.

3. 💰 ESTIMATE SUMMARY
- Subtotal per room
- Contractor O&P (20%)
- Sales tax on materials (~8%)
- 10% contingency
- GRAND TOTAL (recommended claim amount)

4. ⚠️ ADJUSTER NOTES
Documentation gaps, red flags, items insurance may dispute, additional photos needed, and whether the current estimate of ${claim['total_estimate']:.2f} is adequate.

Be thorough — this goes directly to the insurance company. Low estimates hurt the homeowner."""

    # Launch background thread — returns job_id immediately so browser never times out
    cur = db.execute(
        'INSERT INTO estimate_jobs (claim_id, status) VALUES (?, ?)', (claim_id, 'pending'))
    db.commit()
    job_id = cur.lastrowid
    t = threading.Thread(
        target=_run_estimate_job,
        args=(job_id, claim_id, claim, rooms, photo_analyses, photo_section,
              room_section, model, key),
        daemon=True)
    t.start()
    return jsonify({'ok': True, 'job_id': job_id, 'status': 'pending',
                    'poll_url': f'/claims/{claim_id}/ai-estimate/{job_id}'})


@app.route('/claims/<int:claim_id>/ai-estimate/<int:job_id>', methods=['GET'])
def ai_estimate_poll(claim_id, job_id):
    if not session.get('user_id'):
        if not willie_auth():
            return jsonify({'ok': False, 'error': 'unauthorized'}), 401
    db = get_db()
    job = db.execute('SELECT * FROM estimate_jobs WHERE id=? AND claim_id=?',
                     (job_id, claim_id)).fetchone()
    if not job:
        return jsonify({'ok': False, 'error': 'Job not found'}), 404
    job = dict(job)
    if job['status'] == 'done':
        claim = dict(db.execute('SELECT * FROM claims WHERE id=?', (claim_id,)).fetchone())
        return jsonify({
            'ok': True, 'status': 'done',
            'progress': 100,
            'estimate': job['result'],
            'claim_number': claim['claim_number'],
            'client': claim['client_name'],
            'current_total': float(claim['total_estimate']),
        })
    if job['status'] == 'error':
        return jsonify({'ok': False, 'status': 'error', 'progress': 0,
                        'error': job['error'] or 'AI estimate failed'})
    return jsonify({'ok': True, 'status': 'pending',
                    'progress': job.get('progress', 0) or 0,
                    'progress_msg': job.get('progress_msg', '') or ''})


@app.route('/claims/<int:claim_id>/update-estimate', methods=['POST'])
def update_claim_estimate(claim_id):
    """Update total_estimate from AI adjuster result. Accepts session or Willie token."""
    if not session.get('user_id') and not willie_auth():
        return jsonify({'ok': False, 'error': 'unauthorized'}), 401
    data = request.get_json(silent=True) or {}
    total = data.get('total_estimate')
    if total is None:
        return jsonify({'ok': False, 'error': 'total_estimate required'}), 400
    try:
        total = float(total)
    except (ValueError, TypeError):
        return jsonify({'ok': False, 'error': 'Invalid total'}), 400
    db = get_db()
    db.execute('UPDATE claims SET total_estimate=?, updated_at=CURRENT_TIMESTAMP WHERE id=?', (total, claim_id))
    db.commit()
    return jsonify({'ok': True, 'total_estimate': total})


# ── PDF Export ────────────────────────────────────────────────────────────────
@app.route('/claims/<int:claim_id>/report/pdf')
@login_required
def report_pdf(claim_id):
    db = get_db()
    claim = db.execute('''SELECT c.*, u.name as adjuster_name, u.email as adjuster_email
        FROM claims c LEFT JOIN users u ON c.adjuster_id=u.id WHERE c.id=?''', (claim_id,)).fetchone()
    if not claim:
        flash('Claim not found.', 'error')
        return redirect(url_for('dashboard'))
    rooms = db.execute('SELECT * FROM rooms WHERE claim_id=? AND deleted_at IS NULL ORDER BY id', (claim_id,)).fetchall()
    room_data = []
    for room in rooms:
        items  = db.execute('SELECT * FROM line_items WHERE room_id=? AND deleted_at IS NULL ORDER BY id', (room['id'],)).fetchall()
        photos = db.execute('SELECT * FROM photos WHERE room_id=? AND deleted_at IS NULL ORDER BY id', (room['id'],)).fetchall()
        room_data.append({'room': room, 'line_items': items, 'room_photos': photos})
    unassigned_photos = db.execute('SELECT * FROM photos WHERE claim_id=? AND room_id IS NULL AND deleted_at IS NULL', (claim_id,)).fetchall()
    recalc_claim(claim_id)
    claim = db.execute('''SELECT c.*, u.name as adjuster_name, u.email as adjuster_email
        FROM claims c LEFT JOIN users u ON c.adjuster_id=u.id WHERE c.id=?''', (claim_id,)).fetchone()
    signature = db.execute(
        'SELECT * FROM signatures WHERE claim_id=? ORDER BY id DESC LIMIT 1',
        (claim_id,)).fetchone()
    return render_template('report.html', claim=claim, room_data=room_data,
                           unassigned_photos=unassigned_photos, pdf_mode=True, auto_print=True,
                           signature=signature,
                           generated=datetime.datetime.now().strftime('%B %d, %Y %I:%M %p'))


# ── Xactimate ESX Export ──────────────────────────────────────────────────────
@app.route('/claims/<int:claim_id>/export/xactimate')
@login_required
def export_xactimate(claim_id):
    db = get_db()
    claim = db.execute('SELECT * FROM claims WHERE id=?', (claim_id,)).fetchone()
    if not claim:
        flash('Claim not found.', 'error')
        return redirect(url_for('dashboard'))
    rooms = db.execute('SELECT * FROM rooms WHERE claim_id=? AND deleted_at IS NULL ORDER BY id', (claim_id,)).fetchall()
    now = datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<XactimateEstimate version="1.0">',
        '  <ClaimInfo>',
        f'    <ClaimNumber>{claim["claim_number"]}</ClaimNumber>',
        f'    <InsuredName>{claim["client_name"]}</InsuredName>',
        f'    <LossAddress>{claim["property_address"]}</LossAddress>',
        f'    <DateOfLoss>{claim["flood_date"]}</DateOfLoss>',
        f'    <InsuranceCompany>{claim["insurance_company"]}</InsuranceCompany>',
        f'    <PolicyNumber>{claim["policy_number"]}</PolicyNumber>',
        f'    <FloodZone>{claim["flood_zone"]}</FloodZone>',
        f'    <TotalEstimate>{claim["total_estimate"]:.2f}</TotalEstimate>',
        f'    <ExportDate>{now}</ExportDate>',
        '  </ClaimInfo>',
        '  <Rooms>',
    ]
    for room in rooms:
        items = db.execute('SELECT * FROM line_items WHERE room_id=? AND deleted_at IS NULL ORDER BY id', (room['id'],)).fetchall()
        lines += ['    <Room>', f'      <Name>{room["name"]}</Name>',
                  f'      <Subtotal>{room["subtotal"]:.2f}</Subtotal>', '      <LineItems>']
        for item in items:
            lines += ['        <LineItem>',
                      f'          <Description>{item["description"]}</Description>',
                      f'          <Quantity>{item["quantity"]}</Quantity>',
                      f'          <Unit>{item["unit"]}</Unit>',
                      f'          <UnitCost>{item["unit_cost"]:.2f}</UnitCost>',
                      f'          <Total>{item["total"]:.2f}</Total>',
                      '        </LineItem>']
        lines += ['      </LineItems>', '    </Room>']
    lines += ['  </Rooms>', '</XactimateEstimate>']
    resp = make_response('\n'.join(lines))
    resp.headers['Content-Type'] = 'application/xml'
    resp.headers['Content-Disposition'] = f'attachment; filename="{claim["claim_number"]}-xactimate.esx"'
    return resp


# ── FEMA Flood Zone Lookup ────────────────────────────────────────────────────
@app.route('/claims/<int:claim_id>/fema-lookup', methods=['POST'])
@login_required
def fema_lookup(claim_id):
    db = get_db()
    claim = db.execute('SELECT * FROM claims WHERE id=?', (claim_id,)).fetchone()
    if not claim:
        return jsonify({'error': 'not found'}), 404
    result = lookup_fema_flood_zone(claim['property_address'])
    if result:
        result = dict(result) if not isinstance(result, dict) else result
        db.execute('UPDATE claims SET flood_zone=?,fema_map_number=?,lat=?,lng=?,maps_embed_url=? WHERE id=?',
                   (result.get('flood_zone',''), result.get('fema_map_number',''),
                    result.get('lat',0), result.get('lng',0), result.get('maps_embed_url',''), claim_id))
        db.commit()
    return jsonify({'ok': True, **result})


# ── Client Portal ─────────────────────────────────────────────────────────────
@app.route('/claims/<int:claim_id>/portal/generate', methods=['POST'])
@login_required
def generate_portal_link(claim_id):
    db = get_db()
    token = secrets.token_urlsafe(24)
    db.execute('DELETE FROM client_portal_tokens WHERE claim_id=?', (claim_id,))
    db.execute('INSERT INTO client_portal_tokens (claim_id, token) VALUES (?,?)', (claim_id, token))
    db.commit()
    portal_url = url_for('client_portal', token=token, _external=True)
    claim = db.execute('SELECT * FROM claims WHERE id=?', (claim_id,)).fetchone()
    if claim['client_email']:
        subject = f'View Your Flood Damage Claim — {claim["claim_number"]}'
        html = f'''<div style="font-family:sans-serif;max-width:600px;margin:0 auto">
            <h2 style="color:#0a1628">Your Claim Portal</h2>
            <p>Hello {claim["client_name"]},</p>
            <p>Your adjuster has shared your flood damage claim with you.</p>
            <p><a href="{portal_url}" style="background:#0a1628;color:#fff;padding:12px 24px;border-radius:8px;text-decoration:none;display:inline-block;margin:16px 0">View My Claim ↗</a></p>
            <p style="font-size:12px;color:#94a3b8">Claim: {claim["claim_number"]} · FloodClaims Pro</p></div>'''
        send_email(claim['client_email'], subject, html)
    return jsonify({'ok': True, 'portal_url': portal_url, 'token': token})


@app.route('/portal/<token>')
def client_portal(token):
    db = get_db()
    row = db.execute('SELECT claim_id FROM client_portal_tokens WHERE token=?', (token,)).fetchone()
    if not row:
        return render_template('portal_invalid.html'), 404
    claim_id = row['claim_id']
    claim = db.execute('''SELECT c.*, u.name as adjuster_name, u.email as adjuster_email
        FROM claims c LEFT JOIN users u ON c.adjuster_id=u.id WHERE c.id=?''', (claim_id,)).fetchone()
    rooms = db.execute('SELECT * FROM rooms WHERE claim_id=? AND deleted_at IS NULL ORDER BY id', (claim_id,)).fetchall()
    room_data = []
    for room in rooms:
        items  = db.execute('SELECT * FROM line_items WHERE room_id=? ORDER BY id', (room['id'],)).fetchall()
        photos = db.execute('SELECT * FROM photos WHERE room_id=? AND deleted_at IS NULL ORDER BY id', (room['id'],)).fetchall()
        room_data.append({'room': room, 'line_items': items, 'room_photos': photos})
    return render_template('client_portal.html', claim=claim, room_data=room_data, token=token,
                           generated=datetime.datetime.now().strftime('%B %d, %Y'))


# ── Digital Signature ─────────────────────────────────────────────────────────
@app.route('/claims/<int:claim_id>/sign', methods=['POST'])
def sign_claim(claim_id):
    data = request.get_json(silent=True) or {}
    signer   = data.get('signer', 'Client').strip()
    sig_data = data.get('sig_data', '').strip()
    if not sig_data:
        return jsonify({'error': 'sig_data required'}), 400
    db = get_db()
    db.execute('DELETE FROM signatures WHERE claim_id=?', (claim_id,))
    db.execute('INSERT INTO signatures (claim_id, signer, sig_data) VALUES (?,?,?)',
               (claim_id, signer, sig_data))
    db.commit()
    return jsonify({'ok': True, 'message': f'Claim signed by {signer}'})


@app.route('/claims/<int:claim_id>/signature')
@login_required
def get_signature(claim_id):
    db = get_db()
    sig = db.execute('SELECT * FROM signatures WHERE claim_id=? ORDER BY id DESC LIMIT 1', (claim_id,)).fetchone()
    if not sig:
        return jsonify({'signed': False})
    return jsonify({'signed': True, 'signer': sig['signer'], 'signed_at': sig['signed_at']})


# ── Stripe Subscriptions ──────────────────────────────────────────────────────
STRIPE_PLANS = [
    {'id': 'basic',  'name': 'Basic',  'price': '$49/mo',  'price_cents': 4900,
     'stripe_price_id': 'price_1TS3NiE50C70iVkQpmBiiQr0',
     'features': ['25 claims/mo', 'PDF export', 'Aquila AI', 'Client portal', 'NFIP Compliance']},
    {'id': 'pro',    'name': 'Pro',    'price': '$99/mo',  'price_cents': 9900,
     'stripe_price_id': 'price_1TS3NiE50C70iVkQGZYJRdNq',
     'features': ['100 claims/mo', 'Everything in Basic', 'Xactimate export', 'Analytics', 'Priority support']},
    {'id': 'agency', 'name': 'Agency', 'price': '$249/mo', 'price_cents': 24900,
     'stripe_price_id': 'price_1TS3NiE50C70iVkQD6vVFdsV',
     'features': ['Unlimited claims', 'Everything in Pro', 'Multi-adjuster team', 'White-label reports', 'SMS alerts']},
]

@app.route('/billing')
@login_required
def billing():
    db  = get_db()
    sub = db.execute('SELECT * FROM stripe_customers WHERE user_id=?', (session['user_id'],)).fetchone()
    stripe_pub = get_setting('stripe_publishable_key') or os.environ.get('STRIPE_PUBLISHABLE_KEY', '')
    return render_template('billing.html', plans=STRIPE_PLANS, sub=sub, stripe_pub=stripe_pub)

@app.route('/billing/checkout', methods=['POST'])
@login_required
@csrf_required
def billing_checkout():
    plan_id    = request.form.get('plan', 'basic')
    stripe_key = get_setting('stripe_secret_key') or os.environ.get('STRIPE_SECRET_KEY', '')
    if not stripe_key or not STRIPE_OK:
        flash('Stripe not configured — add your STRIPE_SECRET_KEY in Settings first.', 'error')
        return redirect(url_for('billing'))
    try:
        _stripe.api_key = stripe_key
        plan = next((p for p in STRIPE_PLANS if p['id'] == plan_id), STRIPE_PLANS[0])
        checkout = _stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{'price_data': {
                'currency': 'usd',
                'product_data': {'name': f'FloodClaims Pro — {plan["name"]} Plan'},
                'unit_amount': plan['price_cents'],
                'recurring': {'interval': 'month'},
            }, 'quantity': 1}],
            mode='subscription',
            success_url=url_for('billing_success', _external=True) + '?session_id={CHECKOUT_SESSION_ID}',
            cancel_url=url_for('billing', _external=True),
            customer_email=session.get('email', ''),
            metadata={'user_id': str(session['user_id']), 'plan': plan_id},
        )
        return redirect(checkout.url, code=303)
    except Exception as e:
        flash(f'Stripe error: {e}', 'error')
        return redirect(url_for('billing'))

@app.route('/billing/success')
@login_required
def billing_success():
    session_id = request.args.get('session_id', '')
    stripe_key = get_setting('stripe_secret_key') or os.environ.get('STRIPE_SECRET_KEY', '')
    if session_id and stripe_key and STRIPE_OK:
        try:
            _stripe.api_key = stripe_key
            cs = _stripe.checkout.Session.retrieve(session_id)
            plan_id = cs.get('metadata', {}).get('plan', 'basic')
            db = get_db()
            db.execute('''
                INSERT INTO stripe_customers (user_id, stripe_customer, stripe_sub_id, plan, status)
                VALUES (?,?,?,?,?)
                ON CONFLICT(user_id) DO UPDATE SET
                  stripe_customer=excluded.stripe_customer,
                  stripe_sub_id=excluded.stripe_sub_id,
                  plan=excluded.plan, status=excluded.status
            ''', (session['user_id'], cs.get('customer',''), cs.get('subscription',''), plan_id, 'active'))
            db.commit()
        except Exception:
            pass
    flash('🎉 Subscription activated! Welcome to FloodClaims Pro.', 'success')
    return redirect(url_for('billing'))

@app.route('/billing/portal', methods=['POST'])
@login_required
@csrf_required
def billing_portal():
    stripe_key = get_setting('stripe_secret_key') or os.environ.get('STRIPE_SECRET_KEY', '')
    if not stripe_key or not STRIPE_OK:
        flash('Stripe not configured.', 'error')
        return redirect(url_for('billing'))
    db  = get_db()
    sub = db.execute('SELECT * FROM stripe_customers WHERE user_id=?', (session['user_id'],)).fetchone()
    if not sub or not sub['stripe_customer']:
        flash('No active subscription found.', 'error')
        return redirect(url_for('billing'))
    try:
        _stripe.api_key = stripe_key
        portal = _stripe.billing_portal.Session.create(
            customer=sub['stripe_customer'],
            return_url=url_for('billing', _external=True)
        )
        return redirect(portal.url, code=303)
    except Exception as e:
        flash(f'Stripe portal error: {e}', 'error')
        return redirect(url_for('billing'))

def call_openrouter(messages, model, key, max_tokens=4000):
    """Call OpenRouter chat completions API with automatic fallback. Returns response text or error string."""
    fallback_model = get_setting('ai_fallback_model', 'anthropic/claude-sonnet-4-5')
    models_to_try = [model]
    if fallback_model and fallback_model != model:
        models_to_try.append(fallback_model)
    
    last_error = None
    for m in models_to_try:
        try:
            r = _req.post(
                'https://openrouter.ai/api/v1/chat/completions',
                headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
                json={'model': m, 'messages': messages, 'max_tokens': max_tokens},
                timeout=90
            )
            if r.status_code == 401:
                return 'Error: Invalid or expired OpenRouter API key. Please update it in Settings.'
            if r.status_code == 402:
                return 'Error: OpenRouter account out of credits. Please add credits at openrouter.ai.'
            if r.status_code == 429:
                last_error = 'Rate limited'
                continue  # Try fallback
            data = r.json()
            if 'error' in data:
                err_msg = data['error'].get('message', str(data['error']))
                if any(k in err_msg.lower() for k in ['rate', 'limit', 'unavailable', 'not found', 'capacity']):
                    last_error = err_msg
                    continue  # Try fallback
                return f'AI Error: {err_msg}'
            result = data['choices'][0]['message']['content'].strip()
            if m != model:
                result = f"[Used fallback: {m}]\n\n{result}"
            return result
        except Exception as e:
            last_error = str(e)
            continue
    
    return f'Error: AI unavailable. Tried: {", ".join(models_to_try)}. Last error: {last_error or "unknown"}'


def ai_describe_photo(image_path):
    key = OPENROUTER_KEY
    if not key:
        return ''  # No key — return empty, don't pollute DB with error strings
    try:
        with open(image_path, 'rb') as f:
            img_b64 = base64.b64encode(f.read()).decode()
        ext  = image_path.rsplit('.', 1)[-1].lower()
        mime = f'image/{ext}' if ext != 'jpg' else 'image/jpeg'
        # Use a vision-capable model — owl-alpha is text-only
        model = get_setting('ai_vision_model') or get_setting('ai_model', 'openrouter/auto')
        # If the configured model is known text-only, force a vision-capable one
        text_only_models = {'openrouter/owl-alpha', 'openrouter/owl', 'openai/o3-mini', 'deepseek/deepseek-r1'}
        if model in text_only_models:
            model = 'openrouter/auto'
        result = call_openrouter(
            messages=[{
                'role': 'user',
                'content': [
                    {'type': 'text', 'text': (
                        'You are a flood damage assessor. Describe the flood damage '
                        'visible in this photo in 2-3 sentences. Be specific about what '
                        'is damaged, the severity, and likely repair needs. Be professional and concise.'
                    )},
                    {'type': 'image_url', 'image_url': {'url': f'data:{mime};base64,{img_b64}'}}
                ]
            }],
            model=model,
            key=key,
            max_tokens=200
        )
        # result is a string — if it starts with "Error:" or "[Used fallback:" it's still usable
        if result.startswith('Error:'):
            return ''
        # Strip fallback tag from output if present
        if result.startswith('[Used fallback:'):
            result = result.split(']\n\n', 1)[-1] if ']\n\n' in result else result
        return result
    except Exception as e:
        return ''  # Return empty so it can be retried

# ── Routes ────────────────────────────────────────────────────────────────────

# In-memory rate limiter {key: [timestamp, ...]}
_rate_store: dict = {}

def is_rate_limited(key, max_calls=5, window=60):
    """Return True if key has exceeded max_calls within window seconds."""
    import time
    now = time.time()
    calls = [t for t in _rate_store.get(key, []) if now - t < window]
    _rate_store[key] = calls
    if len(calls) >= max_calls:
        return True
    _rate_store[key].append(now)
    return False

@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
@csrf_required
def login():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    if request.method == 'POST':
        ip    = request.remote_addr or 'unknown'
        if is_rate_limited(f'login:{ip}', max_calls=5, window=60):
            flash('Too many login attempts. Please wait a minute and try again.', 'error')
            return render_template('login.html')
        email = request.form.get('email', '').strip().lower()
        pw    = request.form.get('password', '')
        db    = get_db()
        user  = db.execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()
        if not user or not check_pw(pw, user['password']):
            flash('Invalid email or password.', 'error')
            return render_template('login.html')
        # Check if user is active (managers/admins can be deactivated by admin)
        if not user.get('is_active', 1):
            flash('Your account has been deactivated. Contact your administrator.', 'error')
            return render_template('login.html')
        # Transparent bcrypt upgrade: if stored hash is legacy sha256, re-hash now
        if BCRYPT_OK and user['password'] and not user['password'].startswith('$2'):
            db.execute('UPDATE users SET password=? WHERE id=?',
                       (hash_pw(pw), user['id']))
            db.commit()
        session.permanent = True
        session['user_id'] = user['id']
        session['email']   = user['email']
        session['name']    = user['name']
        session['role']    = user['role']
        return redirect(url_for('dashboard'))
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/dashboard')
@login_required
def dashboard():
    db = get_db()
    # Search + filter params
    q          = request.args.get('q', '').strip()
    f_status   = request.args.get('status', '')
    f_adjuster = request.args.get('adjuster_id', '')
    f_priority = request.args.get('priority', '')
    f_date_from= request.args.get('date_from', '')
    f_date_to  = request.args.get('date_to', '')

    base_sql = '''SELECT c.*, u.name as adjuster_name
        FROM claims c LEFT JOIN users u ON c.adjuster_id=u.id WHERE 1=1'''
    params = []
    if session['role'] != 'admin':
        base_sql += ' AND c.adjuster_id=?'
        params.append(session['user_id'])
    if q:
        base_sql += ' AND (c.client_name LIKE ? OR c.claim_number LIKE ? OR c.property_address LIKE ?)'
        like = f'%{q}%'
        params += [like, like, like]
    if f_status:
        base_sql += ' AND c.status=?'
        params.append(f_status)
    if f_adjuster and session['role'] == 'admin':
        base_sql += ' AND c.adjuster_id=?'
        params.append(f_adjuster)
    if f_priority:
        base_sql += ' AND c.priority=?'
        params.append(f_priority)
    if f_date_from:
        base_sql += ' AND c.flood_date >= ?'
        params.append(f_date_from)
    if f_date_to:
        base_sql += ' AND c.flood_date <= ?'
        params.append(f_date_to)
    base_sql += ' ORDER BY c.created_at DESC'
    claims = db.execute(base_sql, params).fetchall()

    # Stats always from full set (no filters)
    if session['role'] == 'admin':
        all_claims = db.execute('SELECT status, total_estimate FROM claims').fetchall()
    else:
        all_claims = db.execute('SELECT status, total_estimate FROM claims WHERE adjuster_id=?',
                                (session['user_id'],)).fetchall()
    stats = {
        'total':       len(all_claims),
        'new':         sum(1 for c in all_claims if c['status'] == 'New'),
        'in_progress': sum(1 for c in all_claims if c['status'] == 'In Progress'),
        'submitted':   sum(1 for c in all_claims if c['status'] == 'Submitted'),
        'closed':      sum(1 for c in all_claims if c['status'] == 'Closed'),
        'pipeline':    sum(c['total_estimate'] for c in all_claims if c['status'] != 'Closed'),
    }
    adjusters = db.execute('SELECT * FROM users ORDER BY name').fetchall() \
                if session['role'] == 'admin' else []
    return render_template('dashboard.html', claims=claims, stats=stats, adjusters=adjusters,
                           q=q, f_status=f_status, f_adjuster=f_adjuster,
                           f_priority=f_priority, f_date_from=f_date_from, f_date_to=f_date_to)

@app.route('/claims/new', methods=['GET', 'POST'])
@login_required
@csrf_required
def new_claim():
    db = get_db()
    if request.method == 'POST':
        claim_num   = gen_claim_number()
        adjuster_id = request.form.get('adjuster_id') or session['user_id']
        g  = lambda k, d='': request.form.get(k, d)  # shorthand
        db.execute('''INSERT INTO claims
            (claim_number, adjuster_id, client_name, client_phone, client_phone_alt, client_email,
             property_address, property_type, property_sqft, year_built, num_floors,
             flood_date, flood_source, water_category, water_class, water_depth_in,
             date_water_removed, inspection_date,
             insurance_company, policy_number, policy_type,
             coverage_building, coverage_contents, deductible,
             mortgage_company, mortgage_loan_number,
             cause_of_loss, priority, notes)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (claim_num, adjuster_id,
             g('client_name'), g('client_phone'), g('client_phone_alt'), g('client_email'),
             g('property_address'), g('property_type'), g('property_sqft'),
             g('year_built'), g('num_floors'),
             g('flood_date'), g('flood_source'), g('water_category'),
             g('water_class'), g('water_depth_in'), g('date_water_removed'),
             g('inspection_date'),
             g('insurance_company'), g('policy_number'), g('policy_type'),
             float(g('coverage_building') or 0), float(g('coverage_contents') or 0),
             float(g('deductible') or 0),
             g('mortgage_company'), g('mortgage_loan_number'),
             g('cause_of_loss'), g('priority', 'Normal'), g('notes')))
        db.commit()
        # Handle initial photos submitted with the form
        photos = request.files.getlist('initial_photos')
        claim  = db.execute('SELECT * FROM claims WHERE claim_number=?', (claim_num,)).fetchone()
        for photo in photos:
            if photo and photo.filename and allowed_file(photo.filename):
                ext      = photo.filename.rsplit('.', 1)[1].lower()
                filename = f'{secrets.token_hex(12)}.{ext}'
                save_path = os.path.join(UPLOAD_DIR, filename)
                photo.save(save_path)
                ai_desc = ai_describe_photo(save_path)
                db.execute(
                    'INSERT INTO photos (claim_id, filename, caption, ai_description) VALUES (?,?,?,?)',
                    (claim['id'], filename, 'Initial site photo', ai_desc))
        db.commit()
        _log_activity(claim['id'], f'Claim created: {claim_num}')
        flash(f'Claim {claim_num} created!', 'success')
        return redirect(url_for('claim_detail', claim_id=claim['id']))
    adjusters = db.execute('SELECT * FROM users ORDER BY name').fetchall() \
                if session['role'] == 'admin' else []
    return render_template('new_claim.html', adjusters=adjusters)

@app.route('/claims/<int:claim_id>/delete', methods=['POST'])
@login_required
@csrf_required
def delete_claim(claim_id):
    """Delete a claim and all its rooms, line items, and photos."""
    db = get_db()
    claim = db.execute('SELECT id, client_name, claim_number FROM claims WHERE id=?', (claim_id,)).fetchone()
    if not claim:
        flash('Claim not found.', 'error')
        return redirect(url_for('dashboard'))
    # Delete uploaded photo files from disk
    photos = db.execute('SELECT filename FROM photos WHERE claim_id=?', (claim_id,)).fetchall()
    for p in photos:
        try:
            path = os.path.join(UPLOAD_DIR, p['filename'])
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            pass
    db.execute('DELETE FROM claims WHERE id=?', (claim_id,))
    db.commit()
    _log_activity(claim_id, f'Claim {claim["claim_number"]} deleted')
    flash(f'Claim {claim["claim_number"]} ({claim["client_name"]}) deleted.', 'success')
    return redirect(url_for('dashboard'))


@app.route('/claims/<int:claim_id>/nfip-fill', methods=['POST'])
@login_required
@csrf_required
def nfip_quick_fill(claim_id):
    """Quick-fill all NFIP compliance fields in one shot."""
    db = get_db()
    claim = db.execute('SELECT id FROM claims WHERE id=?', (claim_id,)).fetchone()
    if not claim:
        flash('Claim not found.', 'error')
        return redirect(url_for('dashboard'))
    f = request.form
    db.execute('''
        UPDATE claims SET
            policy_type=?, coverage_building=?, coverage_contents=?, deductible=?,
            flood_source=?, water_category=?, water_class=?, water_depth_in=?,
            date_water_removed=?, flood_zone=?, fema_map_number=?, inspection_date=?,
            updated_at=CURRENT_TIMESTAMP
        WHERE id=?
    ''', (
        f.get('policy_type','').strip(),
        float(f.get('coverage_building') or 0),
        float(f.get('coverage_contents') or 0),
        float(f.get('deductible') or 0),
        f.get('flood_source','').strip(),
        f.get('water_category','').strip(),
        f.get('water_class','').strip(),
        f.get('water_depth_in','').strip(),
        f.get('date_water_removed','').strip(),
        f.get('flood_zone','').strip(),
        f.get('fema_map_number','').strip(),
        f.get('inspection_date','').strip(),
        claim_id
    ))
    db.commit()
    flash('NFIP fields saved — recheck your compliance score!', 'success')
    return redirect(url_for('claim_detail', claim_id=claim_id))


@app.route('/claims/<int:claim_id>/notes', methods=['POST'])
@login_required
@csrf_required
def update_claim_notes(claim_id):
    """Update the notes field on a claim."""
    db = get_db()
    claim = db.execute('SELECT id FROM claims WHERE id=?', (claim_id,)).fetchone()
    if not claim:
        return jsonify({'ok': False, 'error': 'Claim not found'}), 404
    notes = request.form.get('notes', '').strip()
    db.execute('UPDATE claims SET notes=?, updated_at=CURRENT_TIMESTAMP WHERE id=?', (notes, claim_id))
    db.commit()
    _log_activity(claim_id, 'Notes updated')
    flash('Notes saved.', 'success')
    return redirect(url_for('claim_detail', claim_id=claim_id))


@app.route('/claims/<int:claim_id>')
@login_required
def claim_detail(claim_id):
    try:
        db = get_db()
        claim = db.execute('''SELECT c.*, u.name as adjuster_name
            FROM claims c LEFT JOIN users u ON c.adjuster_id=u.id WHERE c.id=?''',
            (claim_id,)).fetchone()
        if not claim:
            flash('Claim not found.', 'error')
            return redirect(url_for('dashboard'))
        rooms = db.execute('SELECT * FROM rooms WHERE claim_id=? AND deleted_at IS NULL ORDER BY id', (claim_id,)).fetchall()
        room_data = []
        for room in rooms:
            items  = db.execute('SELECT * FROM line_items WHERE room_id=? AND deleted_at IS NULL ORDER BY id', (room['id'],)).fetchall()
            photos = db.execute('SELECT * FROM photos WHERE room_id=? AND deleted_at IS NULL ORDER BY id',     (room['id'],)).fetchall()
            room_data.append({'room': room, 'line_items': items, 'room_photos': photos})
        unassigned_photos = db.execute(
            'SELECT * FROM photos WHERE claim_id=? AND room_id IS NULL AND deleted_at IS NULL ORDER BY id',
            (claim_id,)).fetchall()
        recalc_claim(claim_id)
        # Re-fetch after recalc so totals are fresh
        claim = db.execute('''SELECT c.*, u.name as adjuster_name
            FROM claims c LEFT JOIN users u ON c.adjuster_id=u.id WHERE c.id=?''',
            (claim_id,)).fetchone()
        if not claim:
            flash('Claim not found.', 'error')
            return redirect(url_for('dashboard'))
        signature = db.execute(
            'SELECT * FROM signatures WHERE claim_id=? ORDER BY id DESC LIMIT 1',
            (claim_id,)).fetchone()
        return render_template('claim_detail.html', claim=claim,
                               room_data=room_data, unassigned_photos=unassigned_photos,
                               signature=signature)
    except Exception as _claim_err:
        import traceback as _tb
        print(f'[claim_detail ERROR] claim_id={claim_id}: {_claim_err}\n{_tb.format_exc()}')
        flash(f'Error loading claim — check server logs for details: {_claim_err}', 'error')
        return redirect(url_for('dashboard'))

@app.route('/claims/<int:claim_id>/mobile')
@login_required
def claim_detail_mobile(claim_id):
    """Simplified mobile-first claim detail view."""
    try:
        db = get_db()
        claim = db.execute('''SELECT c.*, u.name as adjuster_name
            FROM claims c LEFT JOIN users u ON c.adjuster_id=u.id WHERE c.id=?''',
            (claim_id,)).fetchone()
        if not claim:
            flash('Claim not found.', 'error')
            return redirect(url_for('dashboard'))
        rooms = db.execute('SELECT * FROM rooms WHERE claim_id=? AND deleted_at IS NULL ORDER BY id', (claim_id,)).fetchall()
        room_data = []
        for room in rooms:
            items  = db.execute('SELECT * FROM line_items WHERE room_id=? AND deleted_at IS NULL ORDER BY id', (room['id'],)).fetchall()
            photos = db.execute('SELECT * FROM photos WHERE room_id=? AND deleted_at IS NULL ORDER BY id', (room['id'],)).fetchall()
            room_data.append({'room': room, 'line_items': items, 'room_photos': photos})
        unassigned_photos = db.execute(
            'SELECT * FROM photos WHERE claim_id=? AND room_id IS NULL AND deleted_at IS NULL ORDER BY id',
            (claim_id,)).fetchall()
        signature = db.execute(
            'SELECT * FROM signatures WHERE claim_id=? ORDER BY id DESC LIMIT 1',
            (claim_id,)).fetchone()
        return render_template('claim_detail_mobile.html', claim=claim,
                               room_data=room_data, unassigned_photos=unassigned_photos,
                               signature=signature)
    except Exception as _e:
        import traceback as _tb
        print(f'[claim_detail_mobile ERROR] claim_id={claim_id}: {_e}\n{_tb.format_exc()}')
        flash('Error loading claim.', 'error')
        return redirect(url_for('dashboard'))

@app.route('/claims/<int:claim_id>/status', methods=['POST'])
@login_required
@csrf_required
def update_status(claim_id):
    db = get_db()
    status = request.form.get('status')
    claim = db.execute('SELECT * FROM claims WHERE id=?', (claim_id,)).fetchone()
    db.execute('UPDATE claims SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?',
               (status, claim_id))
    db.commit()
    if claim:
        notify_client_status_change(claim, status)
        _log_activity(claim_id, f'Status changed to {status}')
    return redirect(url_for('claim_detail', claim_id=claim_id))

@app.route('/claims/<int:claim_id>/room/add', methods=['POST'])
@login_required
@csrf_required
def add_room(claim_id):
    db    = get_db()
    claim = db.execute('SELECT id FROM claims WHERE id=?', (claim_id,)).fetchone()
    if not claim:
        flash('Claim not found.', 'error')
        return redirect(url_for('dashboard'))
    name = request.form.get('room_name', '').strip()
    if name:
        db.execute('INSERT INTO rooms (claim_id, name) VALUES (?,?)', (claim_id, name))
        db.commit()
        _log_activity(claim_id, f'Room added: {name}')
    return redirect(url_for('claim_detail', claim_id=claim_id))

@app.route('/rooms/<int:room_id>/delete', methods=['POST'])
@login_required
@csrf_required
def delete_room(room_id):
    db   = get_db()
    room = db.execute('SELECT * FROM rooms WHERE id=?', (room_id,)).fetchone()
    if not room:
        return redirect(url_for('dashboard'))
    claim_id = room['claim_id']
    # Soft-delete room and its line items
    db.execute('UPDATE rooms SET deleted_at=CURRENT_TIMESTAMP WHERE id=?', (room_id,))
    db.execute('UPDATE line_items SET deleted_at=CURRENT_TIMESTAMP WHERE room_id=?', (room_id,))
    db.execute('UPDATE photos SET room_id=NULL WHERE room_id=?', (room_id,))
    db.commit()
    recalc_claim(claim_id)
    _log_activity(claim_id, f'Room soft-deleted: {room["name"]}')
    return redirect(url_for('claim_detail', claim_id=claim_id))

@app.route('/rooms/<int:room_id>/item/add', methods=['POST'])
@login_required
@csrf_required
def add_item(room_id):
    db        = get_db()
    room      = db.execute('SELECT * FROM rooms WHERE id=?', (room_id,)).fetchone()
    if not room:
        return redirect(url_for('dashboard'))
    desc      = request.form.get('description', '')
    qty       = float(request.form.get('quantity', 1) or 1)
    unit      = request.form.get('unit', 'ea')
    unit_cost = float(request.form.get('unit_cost', 0) or 0)
    total     = qty * unit_cost
    db.execute(
        'INSERT INTO line_items (room_id, description, quantity, unit, unit_cost, total) '
        'VALUES (?,?,?,?,?,?)',
        (room_id, desc, qty, unit, unit_cost, total))
    db.commit()
    recalc_claim(room['claim_id'])
    _log_activity(room['claim_id'], f'Line item added: {desc} x{qty} {unit} @${unit_cost:.2f}')
    return redirect(url_for('claim_detail', claim_id=room['claim_id']))

@app.route('/items/<int:item_id>/delete', methods=['POST'])
@login_required
@csrf_required
def delete_item(item_id):
    db   = get_db()
    item = db.execute(
        'SELECT r.claim_id FROM line_items li JOIN rooms r ON li.room_id=r.id WHERE li.id=?',
        (item_id,)).fetchone()
    db.execute('UPDATE line_items SET deleted_at=CURRENT_TIMESTAMP WHERE id=?', (item_id,))
    db.commit()
    if item:
        recalc_claim(item['claim_id'])
        _log_activity(item['claim_id'], 'Line item soft-deleted')
    return jsonify({'ok': True})


# ── Feedback: Dashboard API & Client Portal ────────────────────────────────────

@app.route('/api/health/feedback-tables')
def feedback_tables_health():
    """Check if feedback tables exist (no auth required for monitoring)."""
    import sqlite3
    db = sqlite3.connect(DB_PATH)
    tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'feedback_%'").fetchall()]
    return jsonify({'tables': tables, 'ok': len(tables) == 4})

@app.route('/api/dashboard/feedback')
@login_required
def dashboard_feedback_api():
    """Return all feedback sessions for the dashboard widget."""
    db = get_db()
    sessions = db.execute('''
        SELECT fc.id, fc.client_name, fc.client_email, fc.title, fc.status,
               fc.priority, fc.admin_read, fc.created_at, fc.updated_at,
               (SELECT COUNT(*) FROM feedback_messages fm WHERE fm.conversation_id=fc.id AND fm.role='user') as message_count,
           (SELECT fm.content FROM feedback_messages fm WHERE fm.conversation_id=fc.id AND fm.role='user' ORDER BY fm.id DESC LIMIT 1) as last_message
        FROM feedback_conversations fc
        ORDER BY fc.updated_at DESC
        LIMIT 50
    ''').fetchall()
    # Get poll state
    poll = db.execute('SELECT last_check FROM feedback_poll_state WHERE id=1').fetchone()
    last_check = poll['last_check'] if poll else ''
    result = {
        'sessions': [dict(s) for s in sessions],
        'last_check': last_check,
        'unread_count': sum(1 for s in sessions if not s['admin_read'])
    }
    return jsonify(result)

@app.route('/admin/feedback/conversations/<int:conv_id>/read', methods=['POST'])
@login_required
def feedback_mark_read(conv_id):
    """Mark a conversation as read by admin."""
    if session.get('role') not in ('admin', 'manager'):
        abort(403)
    db = get_db()
    db.execute('UPDATE feedback_conversations SET admin_read=1 WHERE id=?', (conv_id,))
    db.commit()
    return jsonify({'ok': True})

@app.route('/admin/feedback/clients', methods=['GET', 'POST'])
@login_required
def feedback_clients():
    """List or create feedback clients."""
    if session.get('role') != 'admin':
        abort(403)
    db = get_db()
    if request.method == 'POST':
        name = request.json.get('name', '').strip()
        email = request.json.get('email', '').strip()
        app_name = request.json.get('app_name', '').strip()
        token = secrets.token_urlsafe(24)
        db.execute('INSERT INTO feedback_clients (name, email, token, app_name) VALUES (?,?,?,?)',
                   (name, email, token, app_name))
        db.commit()
        return jsonify({'ok': True, 'token': token, 'url': f'/feedback/{token}'})
    clients = db.execute('SELECT * FROM feedback_clients ORDER BY created_at DESC').fetchall()
    return jsonify([dict(c) for c in clients])

@app.route('/feedback/<token>')
def feedback_client_portal(token):
    """Client-facing feedback portal — no login required."""
    db = get_db()
    client = db.execute('SELECT * FROM feedback_clients WHERE token=? AND status="active"', (token,)).fetchone()
    if not client:
        return 'Invalid or expired link. Please contact Jay Alexander for a new link.', 404
    # Get or create a conversation for this client
    conv = db.execute('SELECT * FROM feedback_conversations WHERE client_token=? ORDER BY updated_at DESC LIMIT 1',
                      (token,)).fetchone()
    conv_id = conv['id'] if conv else None
    return render_template('feedback_portal.html', client=client, conv_id=conv_id)


@app.route('/claims/<int:claim_id>/photo/upload', methods=['POST'])
@login_required
@csrf_required
def upload_photo(claim_id):
    db      = get_db()
    file    = request.files.get('photo')
    room_id = request.form.get('room_id') or None
    caption = request.form.get('caption', '')
    if not file or not allowed_file(file.filename):
        flash('Invalid file type. Please upload a PNG, JPG, GIF, or WEBP.', 'error')
        return redirect(url_for('claim_detail', claim_id=claim_id))
    # ── File size check (10MB max) ──
    file.seek(0, 2)  # seek to end
    file_size = file.tell()
    file.seek(0)  # reset
    if file_size > 10 * 1024 * 1024:
        flash('File too large. Maximum size is 10MB. Please compress your image and try again.', 'error')
        return redirect(url_for('claim_detail', claim_id=claim_id))
    ext       = file.filename.rsplit('.', 1)[1].lower()
    filename  = f'{secrets.token_hex(12)}.{ext}'
    save_path = os.path.join(UPLOAD_DIR, filename)
    # ── Auto-compress large images ──
    try:
        from PIL import Image as _PILImage
        img = _PILImage.open(file)
        # Resize if max dimension > 2048px
        max_dim = max(img.size)
        if max_dim > 2048:
            scale = 2048 / max_dim
            new_size = (int(img.size[0] * scale), int(img.size[1] * scale))
            img = img.resize(new_size, _PILImage.LANCZOS)
        # Convert RGBA to RGB for JPEG
        if img.mode == 'RGBA' and ext in ('jpg', 'jpeg'):
            img = img.convert('RGB')
        # Save with quality optimization
        save_kwargs = {'optimize': True}
        if ext in ('jpg', 'jpeg'):
            save_kwargs['quality'] = 80
        elif ext == 'png':
            save_kwargs['compress_level'] = 6
        img.save(save_path, **save_kwargs)
        size_kb = os.path.getsize(save_path) / 1024
        if int(file_size / 1024) > 500:
            compressed_pct = int((1 - size_kb / (file_size / 1024)) * 100)
            flash_msg = f'Photo uploaded and compressed ({compressed_pct}% smaller)'
        else:
            flash_msg = 'Photo uploaded!'
    except Exception:
        # PIL not available or error — save original
        file.save(save_path)
        flash_msg = 'Photo uploaded!'
    ai_desc = ai_describe_photo(save_path)
    db.execute(
        'INSERT INTO photos (claim_id, room_id, filename, caption, ai_description) '
        'VALUES (?,?,?,?,?)',
        (claim_id, room_id, filename, caption, ai_desc))
    db.commit()
    _log_activity(claim_id, f'Photo uploaded: {filename}')
    flash(flash_msg + (' AI analysis complete.' if ai_desc else
          ' Add an OpenRouter key in Settings to enable AI analysis.'), 'success')
    return redirect(url_for('claim_detail', claim_id=claim_id))

@app.route('/uploads/<filename>')
@login_required
def uploaded_file(filename):
    return send_from_directory(UPLOAD_DIR, filename)

@app.route('/photos/<int:photo_id>/delete', methods=['POST'])
@login_required
@csrf_required
def delete_photo(photo_id):
    db    = get_db()
    photo = db.execute('SELECT * FROM photos WHERE id=?', (photo_id,)).fetchone()
    if not photo:
        return jsonify({'ok': False, 'error': 'Not found'}), 404
    # Delete the file from disk
    try:
        file_path = os.path.join(UPLOAD_DIR, photo['filename'])
        if os.path.exists(file_path):
            os.remove(file_path)
    except Exception:
        pass
    db.execute('UPDATE photos SET deleted_at=CURRENT_TIMESTAMP WHERE id=?', (photo_id,))
    db.commit()
    _log_activity(photo['claim_id'], f'Photo soft-deleted: {photo["filename"]}')
    return jsonify({'ok': True})

@app.route('/photos/<int:photo_id>/ai-description', methods=['POST'])
@login_required
def edit_ai_description(photo_id):
    """Save a manually edited AI description for a photo."""
    data = request.get_json(silent=True) or {}
    description = data.get('description', '').strip()
    db = get_db()
    db.execute('UPDATE photos SET ai_description=? WHERE id=?', (description, photo_id))
    db.commit()
    return jsonify({'ok': True})


@app.route('/photos/<int:photo_id>/analyze', methods=['POST'])
@login_required
def analyze_photo_route(photo_id):
    db    = get_db()
    photo = db.execute('SELECT * FROM photos WHERE id=?', (photo_id,)).fetchone()
    if not photo:
        return jsonify({'error': 'Photo not found'}), 404
    image_path = os.path.join(UPLOAD_DIR, photo['filename'])
    if not os.path.exists(image_path):
        return jsonify({'error': 'Image file not found on disk'}), 404
    desc = ai_describe_photo(image_path)
    if not desc:
        return jsonify({'error': 'AI unavailable — add an OpenRouter key in ⚙️ Settings'})
    db.execute('UPDATE photos SET ai_description=? WHERE id=?', (desc, photo_id))
    db.commit()
    return jsonify({'ok': True, 'description': desc})

@app.route('/photos/<int:photo_id>/edit', methods=['POST'])
@login_required
@csrf_required
def edit_photo(photo_id):
    db      = get_db()
    photo   = db.execute('SELECT * FROM photos WHERE id=?', (photo_id,)).fetchone()
    if not photo:
        flash('Photo not found.', 'error')
        return redirect(url_for('dashboard'))
    caption = request.form.get('caption', '').strip()
    room_id = request.form.get('room_id') or None
    db.execute('UPDATE photos SET caption=?, room_id=? WHERE id=?',
               (caption, room_id, photo_id))
    db.commit()
    flash('Photo updated!', 'success')
    return redirect(url_for('claim_detail', claim_id=photo['claim_id']))

@app.route('/claims/<int:claim_id>/report')
@login_required
def report(claim_id):
    db    = get_db()
    claim = db.execute('''SELECT c.*, u.name as adjuster_name, u.email as adjuster_email
        FROM claims c LEFT JOIN users u ON c.adjuster_id=u.id WHERE c.id=?''',
        (claim_id,)).fetchone()
    if not claim:
        flash('Claim not found.', 'error')
        return redirect(url_for('dashboard'))
    rooms = db.execute('SELECT * FROM rooms WHERE claim_id=? AND deleted_at IS NULL ORDER BY id', (claim_id,)).fetchall()
    room_data = []
    for room in rooms:
        items  = db.execute('SELECT * FROM line_items WHERE room_id=? ORDER BY id', (room['id'],)).fetchall()
        photos = db.execute('SELECT * FROM photos WHERE room_id=? AND deleted_at IS NULL ORDER BY id',     (room['id'],)).fetchall()
        room_data.append({'room': room, 'line_items': items, 'room_photos': photos})
    unassigned_photos = db.execute(
        'SELECT * FROM photos WHERE claim_id=? AND room_id IS NULL AND deleted_at IS NULL', (claim_id,)).fetchall()
    recalc_claim(claim_id)
    claim = db.execute('''SELECT c.*, u.name as adjuster_name, u.email as adjuster_email
        FROM claims c LEFT JOIN users u ON c.adjuster_id=u.id WHERE c.id=?''',
        (claim_id,)).fetchone()
    signature = db.execute(
        'SELECT * FROM signatures WHERE claim_id=? ORDER BY id DESC LIMIT 1',
        (claim_id,)).fetchone()
    return render_template('report.html', claim=claim, room_data=room_data,
                           unassigned_photos=unassigned_photos, signature=signature,
                           generated=datetime.datetime.now().strftime('%B %d, %Y %I:%M %p'))

# ── Admin: Settings ───────────────────────────────────────────────────────────

@app.route('/admin/settings', methods=['GET', 'POST'])
@login_required
@admin_required
@csrf_required
def settings():
    if request.method == 'POST':
        # API key is managed via Railway env var OPENROUTER_API_KEY only — not stored in DB
        # Aquila Chat is always locked to OWL Alpha — not user-configurable
        # Vision model selection
        ai_vision_model = request.form.get('ai_vision_model', '').strip()
        if ai_vision_model:
            set_setting('ai_vision_model', ai_vision_model)
        # Fallback model for chat (also locked to OWL Alpha compatible models)
        fallback_model = request.form.get('ai_fallback_model', '').strip()
        if fallback_model:
            set_setting('ai_fallback_model', fallback_model)
        # Integration keys (SendGrid, Stripe, etc. — these are safe in DB)
        for key in ['sendgrid_api_key', 'from_email', 'stripe_secret_key',
                    'stripe_publishable_key', 'google_maps_api_key',
                    'twilio_account_sid', 'twilio_auth_token', 'twilio_from_number',
                    'admin_report_email']:
            val = request.form.get(key, '').strip()
            if val:
                set_setting(key, val)
        flash('Settings saved!', 'success')
        return redirect(url_for('settings'))

    env_key_set       = bool(OPENROUTER_KEY)
    current_model     = get_setting('ai_model', 'openai/gpt-4o-mini')
    current_vision_model = get_setting('ai_vision_model', 'openrouter/auto')
    current_chat_model   = get_setting('ai_chat_model') or get_setting('ai_model', 'openrouter/owl-alpha')
    current_fallback  = get_setting('ai_fallback_model', 'anthropic/claude-sonnet-4-5')
    return render_template('settings.html',
                           env_key_set=env_key_set,
                           current_model=current_model,
                           current_vision_model=current_vision_model,
                           current_chat_model=current_chat_model,
                           current_fallback=current_fallback)

# ── Free Models API ─────────────────────────────────────────────────────────────

@app.route('/admin/api/free-models')
@login_required
@admin_required
def api_free_models():
    """Fetch latest free models from OpenRouter with 1024+ context."""
    import urllib.request as _req
    import json as _json
    try:
        req = _req.Request('https://openrouter.ai/api/v1/models', headers={
            'User-Agent': 'FloodClaims-Pro/1.0'
        })
        resp = _req.urlopen(req, timeout=10)
        data = _json.loads(resp.read())
        models = data.get('data', data) if isinstance(data, dict) else data
        free_models = []
        for m in models:
            mid = m.get('id', '')
            pricing = m.get('pricing', {})
            prompt_price = pricing.get('prompt', '0')
            # Check if free (prompt price is 0 or very close to 0)
            try:
                is_free = float(prompt_price) <= 0
            except (ValueError, TypeError):
                is_free = False
            if not is_free:
                continue
            # Check context length >= 1024
            ctx = m.get('context_length', 0)
            try:
                ctx = int(ctx)
            except (ValueError, TypeError):
                ctx = 0
            if ctx < 1024:
                continue
            # Check if vision-capable
            architecture = m.get('architecture', {})
            modality = architecture.get('modality', m.get('modality', ''))
            input_mods = architecture.get('input_modalities', [])
            is_vision = ('image' in str(modality).lower() or 
                        'image' in str(input_mods).lower() or
                        'vision' in mid.lower())
            free_models.append({
                'id': mid,
                'name': mid.split('/')[-1].replace('-', ' ').title(),
                'provider': mid.split('/')[0] if '/' in mid else 'Unknown',
                'context': ctx,
                'vision': is_vision,
                'prompt_price': prompt_price,
                'completion_price': pricing.get('completion', '0'),
            })
        # Sort by context length descending
        free_models.sort(key=lambda x: x['context'], reverse=True)
        return jsonify({'ok': True, 'models': free_models, 'count': len(free_models)})
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500


@app.route('/admin/api/init-brain', methods=['POST'])
@login_required
@admin_required
def api_init_brain():
    """Initialize brain files with default content."""
    import os

    identity_path = os.path.join(os.path.dirname(__file__), '..', 'brain', 'IDENTITY.md')
    soul_path = os.path.join(os.path.dirname(__file__), '..', 'brain', 'SOUL.md')
    memory_path = os.path.join(os.path.dirname(__file__), '..', 'brain', 'MEMORY.md')

    # Read from files if they exist, otherwise use built-in defaults
    identity = _read_brain_file(identity_path, 'brain_identity_md')
    soul = _read_brain_file(soul_path, 'brain_soul_md')
    memory = _read_brain_file(memory_path, 'brain_memory_md')
    system = _get_default_brain('brain_system_prompt')
    photo = _get_default_brain('brain_photo_prompt')

    set_setting('brain_identity_md', identity)
    set_setting('brain_soul_md', soul)
    set_setting('brain_memory_md', memory)
    set_setting('brain_system_prompt', system)
    set_setting('brain_photo_prompt', photo)

    return jsonify({
        'ok': True,
        'message': 'Brain files initialized',
        'sizes': {
            'identity': len(identity),
            'soul': len(soul),
            'memory': len(memory),
            'system': len(system),
            'photo_prompt': len(photo)
        }
    })


def _read_brain_file(filepath, setting_key):
    """Read brain file from disk or return built-in default."""
    import os
    if os.path.exists(filepath):
        with open(filepath) as f:
            content = f.read().strip()
            if content:
                return content
    # Return built-in default
    return _get_default_brain(setting_key)


def _get_default_brain(key):
    """Return built-in default content for brain files."""
    if key == 'brain_identity_md':
        return """# IDENTITY.md — Aquila, AI Flood Damage Expert

## Who I Am
I am **Aquila**, the AI flood damage expert and agentic assistant built into **FloodClaims Pro**. Named after the Latin word for "eagle" — representing sharp vision and precision. I am not just a chatbot — I am a fully capable agent who can perform actions inside the application.

## My Role
Primary AI assistant for flood insurance claims adjusters. I combine deep domain expertise in flood damage assessment with the ability to directly manipulate data, create records, and execute workflows.

## Agentic Actions
- Create new claims with all fields populated
- Edit any field on any claim (client name, property address, flood date, water category, damage estimates, notes)
- Add rooms to claims (Living Room, Kitchen, Bedroom, Bathroom, Basement, Garage, Attic, etc.)
- Add line items to rooms (description, quantity, unit, unit cost, auto-calculated total)
- Recalculate claim totals
- Move claims through pipeline (New → In Progress → Submitted → Closed)
- Assign adjusters to claims
- Add/edit team members
- Look up FEMA flood zones
- Check NFIP compliance
- Analyze damage photos in extreme detail
- Send client notifications
- Schedule inspections
- Generate claim reports (PDF, Xactimate)
- Manage contractor/applicant pipeline

## Domain Expertise
- Water Categories: 1 (clean), 2 (gray), 3 (black/floodwater)
- Water Classes: 1 (floors), 2 (walls), 3 (whole room), 4 (specialty drying)
- Damage assessment for all building materials and systems
- NFIP policy types, coverage limits, proof of loss requirements
- FEMA flood zone determination
- Standard restoration line items and pricing

## Platform
- FloodClaims Pro: https://billy-floods.up.railway.app
- AI: OpenRouter (chat locked to OWL Alpha, vision configurable)
- Adjuster recruitment: instant via license verification
- Contractor pipeline: 5-step training + certification
- Billing: Stripe (Basic $49, Pro $149, Agency $249/mo)
- Notifications: Twilio SMS + SendGrid email

## Personality
Professional, precise, proactive, thorough, authoritative. I speak like a seasoned flood claims adjuster. I get things done quickly without unnecessary chatter.

## Boundaries
Cannot process payments. Cannot legally sign documents. Flags uncertainties. Confirms before destructive actions. Respects user roles (admin vs adjuster)."""

    elif key == 'brain_soul_md':
        return """# SOUL.md — How Aquila Thinks & Operates

## Core Philosophy
Every claim tells a story of loss. My job is to help the adjuster document that loss accurately, thoroughly, and fairly.

## Decision-Making
1. Always populate every field you can — don't leave blanks if info is available
2. Infer from context — suggest water category/class from photo evidence
3. Be specific — "Hardwood buckled along north wall, ~200 sqft" not "floor damaged"
4. Use industry terminology — standard construction/restoration language
5. Flag uncertainties — say when you're not sure rather than guessing

## Photo Analysis Methodology
- Catalog every visible item (walls, floors, ceilings, fixtures, furniture, appliances, contents)
- Rate damage per item (undamaged/minor/moderate/severe/destroyed)
- Note water evidence (water lines, depth, staining, moisture marks, sediment lines)
- Assess mold (presence, color, location, coverage area, growth stage)
- Estimate measurements (room dimensions, affected sqft, linear feet)
- Identify materials specifically (e.g., "solid red oak hardwood" not just "flooring")
- Flag structural concerns (warping, buckling, cracking, delamination, sagging)
- Check HVAC, electrical, plumbing systems
- Note contents damage (furniture, electronics, personal property)
- Identify code upgrade requirements

## Communication
- Lead with the answer
- Provide context and explain why
- Suggest next steps
- Use bullet points for complex info
- Highlight critical items

## Interaction Style
- With adjusters: professional peer-to-peer, industry jargon OK
- With admins: slightly more formal, include technical details
- With clients: warm, empathetic, avoid jargon
- When uncertain: "I'm not sure about X, but here's what I can tell you..."

## Continuous Learning
- Photo analysis improves with custom Photo Training prompt in Settings
- Brain file changes take effect on very next conversation
- Always reference IDENTITY.md, SOUL.md, MEMORY.md in responses

## Error Handling
- Rate limit exceeded: wait 60s then retry
- AI service unavailable: notify user clearly
- Missing fields required: ask for minimum needed
- Conflicting info: flag it, don't guess"""

    elif key == 'brain_memory_md':
        return """# MEMORY.md — FloodClaims Pro Deployment Knowledge

## Business
- Company: Liberty Emporium
- Owner: Jay Alexander (Ronald J. Alexander Jr.)
- Address: 125 W Swannanoa Av, Liberty NC 27298
- Email: leprograms@protonmail.com
- Phone: 743-337-9506
- Website: https://alexanderai.site
- GitHub: https://github.com/Liberty-Emporium

## Deployment
- Primary: https://billy-floods.up.railway.app (Railway)
- Database: SQLite on Railway volume (/data/floodclaim.db)
- AI: OpenRouter (OPENROUTER_API_KEY env var)
- Session: 30-day cookie, server-side

## Related Apps (Railway)
- FloodClaims Pro: billy-floods.up.railway.app
- Sweet Spot Cakes: sweet-spot-cakes.up.railway.app
- Pet Vet AI: ai-vet-tech.alexanderai.site
- AI Agent Widget: ai-agent-widget-production.up.railway.app
- EcDash: jay-portfolio-production.up.railway.app (alexanderai.site)
- Liberty Oil: liberty-oil-propane.up.railway.app
- KYS: ai-api-tracker-production.up.railway.app
- Agents: agents.alexanderai.site
- LE Thrift: liberty-emporium-thrift.alexanderai.site
- Gym Forge: gymforge.ai.alexanderai.site
- Liberty Oil (main): libertyoilandpropane.com (NOT on Railway, Jay manages manually)

## Integrations
- Stripe: payments (Basic $49, Pro $149, Agency $249/mo)
- SendGrid: email delivery
- Twilio: SMS notifications
- FEMA NFHL API: flood zone lookup
- Census Geocoding: address geocoding
- Xactimate: export format support

## Agent System
- Willie Agent ID: F5J8yYT6a6GrppjviN6p8w
- Multi-agent: OWL (Kali) + Bull (KiloClaw)
- Chat model: locked to openrouter/OWL Alpha
- Vision model: configurable in Settings → Vision Model

## Water Classification
- Category 1: Clean Water (sanitary — broken supply line, sink/tub overflow)
- Category 2: Gray Water (significant contamination — sump backup, washing machine overflow)
- Category 3: Black Water (grossly contaminated — sewage, floodwater, river water)
- Class 1: Affects only part of room, minimal absorption
- Class 2: Affects entire room, carpet and padding, wicking up walls 24-48"
- Class 3: Fastest evaporation rate, ceilings and walls saturated
- Class 4: Specialty drying — hardwood, concrete, plaster

## NFIP Policy Limits
- Residential: Building $250,000, Contents $100,000
- Commercial: Building $500,000, Contents $500,000
- Deductibles: $1,000-$10,000 depending on zone and elevation
- Proof of Loss: Required within 60 days of loss date (unless extended by FEMA)

## Standard Line Items (Xactimate-style)
- Demo/Remove (per room, per sqft)
- Drywall removal & reinstall (sqft)
- Insulation removal & reinstall (sqft)
- Interior painting (sqft wall area)
- Flooring removal & install (sqft — hardwood, tile, carpet)
- Baseboard removal & reinstall (linear ft)
- Electrical outlet/switch replacement (per unit)
- HVAC duct cleaning (per room)
- Dehumidification (per day)
- Air movers (per day, per unit)
- Content manipulation (per room)
- Anti-microbial treatment (sqft)
- Ozone treatment (per day)

## Database Tables
users, claims, rooms, line_items, photos, willie_conversations, willie_messages, settings, client_portal_tokens, signatures, stripe_customers, estimate_jobs, inspection_slots, notifications_log, activity_log, adjuster_applications, contractor_applications

## Roles
- Admin: full access, settings, team, recruit, analytics, billing
- Adjuster: assigned claims only, create/edit own claims, view own inspections

## Routes
/ (dashboard), /new_claim, /claims/<id>, /pipeline, /schedule, /notifications, /analytics, /billing, /admin/settings, /admin/team, /admin/recruit, /willie, /portal/<token>, /login, /logout, /health

## Contractor Recruitment Pipeline
1. Apply (contractor application form)
2. Review (admin reviews application)
3. Training (5 certification courses)
4. Certification Test (pass/fail)
5. Activate (approved for job assignments)

## Adjuster Recruitment
- Instant: enter NC license # → verify → auto-approve
- Email notification sent to adjuster
- First login requires password setup"""

    elif key == 'brain_photo_prompt':
        return """You are an expert flood damage assessor analyzing a photo for an insurance claim. Examine this photo with extreme precision and report ALL findings.

Structure your analysis as follows:

## ROOM & CONTEXT
- Identify room type if visible
- Ceiling height estimate
- Approximate room dimensions if determinable

## WATER EVIDENCE
- Water line height (inches from floor)
- Water staining (location, extent, color)
- Sediment or debris lines
- Active moisture visibility

## DAMAGE ASSESSMENT (item by item)

### Ceiling
- Material, condition, damage level (none/minor/moderate/severe)
- Staining, sagging, peeling, holes

### Walls
- Material (drywall, plaster, wood paneling)
- Damage: wicking height, staining, peeling paint, bubbling
- Affected linear feet and height from floor

### Flooring
- Material (hardwood, tile, carpet, vinyl, laminate, concrete)
- Damage type (buckling, warping, delamination, staining, saturation)
- Affected area in square feet
- Padding condition

### Baseboards & Trim
- Affected linear feet
- Material and condition

### Doors & Windows
- Frame damage, warping
- Hardware condition

### Kitchen
- Cabinet damage (base and upper)
- Countertop condition
- Appliance damage (dishwasher, fridge, range, microwave)

### Bathroom
- Vanity, toilet, tub/shower damage
- Tile/grout condition

### Contents & Furniture
- Any visible furniture/contents
- Damage level and material type

### HVAC/Mechanical
- Visible ductwork, vents, HVAC equipment damage

### Electrical
- Outlet/switch plate water lines
- Panel damage if visible

## MOLD ASSESSMENT
- Present: Y/N
- If present: location, approximate coverage area, color, growth stage

## STRUCTURAL CONCERNS
- Warped framing, buckled walls, sagging ceiling
- Any visible foundation or structural damage

## WATER CATEGORY ASSESSMENT
- Category 1 (Clean), 2 (Gray), or 3 (Black)
- Reasoning for classification

## WATER CLASS ASSESSMENT
- Class 1 through 4 with reasoning

## REPAIR RECOMMENDATIONS
List specific restoration actions needed:
- Demo/removal items
- Drying requirements
- Replacement items
- Specialty treatments (anti-microbial, ozone)

## SUMMARY
Total affected square feet, estimated severity, priority items.

Be thorough. If something is NOT damaged, say so. If you can't see it, say "not visible." Never fabricate details."""

    elif key == 'brain_system_prompt':
        return """You are Aquila, the AI assistant for FloodClaims Pro — a flood insurance claims management platform built by Liberty Emporium.

## Your Role
You help homeowners, insurance adjusters, and contractors with flood damage assessment, claims processing, and insurance guidance. You are knowledgeable, empathetic, and action-oriented.

## How to Analyze Damage Photos
When a user uploads a flood damage photo:
1. Identify the type of damage (water staining, structural crack, mold, debris, etc.)
2. Rate severity: Minor / Moderate / Major / Severe
3. List affected materials (drywall, flooring, insulation, electrical, HVAC, foundation)
4. Estimate the remediation urgency: Immediate / Within 48 hours / Can wait
5. Provide 2-3 recommended next steps
6. Format with clear headings and bullet points

## Response Guidelines
- Be specific and actionable — tell users exactly what to do next
- When you see damage in a photo, ALWAYS mention: "I recommend having a licensed adjuster verify this in person"
- Use plain language — avoid insurance jargon unless asked
- When uncertain about dollar amounts, give ranges and recommend professional estimates
- For FEMA/NFIP questions, reference the specific policy section when possible
- Always end responses with a clear next step or question to advance the conversation

## Tone
Professional, calm, empathetic. People filing flood claims are often overwhelmed. Be the steady hand that guides them through the process.

## Critical Rules
- Never fabricate policy details — if unsure, say so and direct to FEMA or their agent
- Never guarantee claim approval or specific payout amounts
- Always recommend professional inspection for structural damage or mold
- Do not provide legal advice — direct to licensed attorneys for legal questions"""

    return ''


@app.route('/admin/api/test-photo-analysis', methods=['POST'])
@login_required
@admin_required
def api_test_photo_analysis():
    """Test photo analysis with custom prompt from brain training."""
    if 'photo' not in request.files:
        return jsonify({'ok': False, 'error': 'No photo uploaded'}), 400
    file = request.files['photo']
    test_prompt = request.form.get('test_prompt', '')
    if not test_prompt:
        test_prompt = None  # Will use default in ai_describe_photo_detailed

    import tempfile, os
    suffix = '.' + file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else '.jpg'
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    file.save(tmp.name)
    tmp.close()

    key = get_setting('openrouter_api_key') or OPENROUTER_KEY
    if not key:
        os.unlink(tmp.name)
        return jsonify({'ok': False, 'error': 'OpenRouter API key not configured'}), 400

    model = get_setting('ai_vision_model') or get_setting('ai_model', 'openrouter/auto')
    text_only = {'openrouter/owl-alpha', 'openrouter/owl', 'openai/o3-mini', 'deepseek/deepseek-r1'}
    if model in text_only:
        model = 'openrouter/auto'

    try:
        # Use custom prompt if provided
        if test_prompt:
            import base64 as _b64
            with open(tmp.name, 'rb') as f:
                img_b64 = _b64.b64encode(f.read()).decode()
            ext = suffix.replace('.', '')
            mime = f'image/{ext}' if ext != 'jpg' else 'image/jpeg'
            r = _req.post(
                'https://openrouter.ai/api/v1/chat/completions',
                headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
                json={
                    'model': model,
                    'messages': [{
                        'role': 'user',
                        'content': [
                            {'type': 'text', 'text': test_prompt},
                            {'type': 'image_url', 'image_url': {'url': f'data:{mime};base64,{img_b64}'}}
                        ]
                    }],
                    'max_tokens': 2000
                }, timeout=60)
            result = r.json()['choices'][0]['message']['content']
        else:
            result = ai_describe_photo_detailed(tmp.name, key, model)
        os.unlink(tmp.name)
        return jsonify({'ok': True, 'analysis': result})
    except Exception as e:
        try: os.unlink(tmp.name)
        except: pass
        return jsonify({'ok': False, 'error': str(e)}), 500


# ── Admin: Team Management ────────────────────────────────────────────────────

@app.route('/admin/team')
@login_required
@admin_required
def team():
    db = get_db()
    users = db.execute(
        'SELECT u.*, (SELECT COUNT(*) FROM claims WHERE adjuster_id=u.id) as claim_count '
        'FROM users u ORDER BY u.name').fetchall()
    adjusters = db.execute(
        '''SELECT u.*,
           (SELECT COUNT(*) FROM claims WHERE adjuster_id=u.id AND status NOT IN ('Closed','Submitted')) as active_claims,
           (SELECT COUNT(*) FROM claims WHERE adjuster_id=u.id AND status IN ('Closed','Submitted')) as completed_claims,
           COALESCE(u.is_active, 1) as is_active
           FROM users u WHERE u.role='adjuster' ORDER BY u.name''').fetchall()
    return render_template('team.html', users=users, adjusters=adjusters)

@app.route('/admin/team/add', methods=['POST'])
@login_required
@admin_required
@csrf_required
def add_team_member():
    db    = get_db()
    email = request.form.get('email', '').strip().lower()
    name  = request.form.get('name', '').strip()
    pw    = request.form.get('password', '').strip()
    role  = request.form.get('role', 'adjuster')
    if role not in ('adjuster', 'manager', 'admin'):
        role = 'adjuster'
    if not email or not pw:
        flash('Email and password required.', 'error')
        return redirect(url_for('team'))
    ok, err = _validate_password(pw)
    if not ok:
        flash(err, 'error')
        return redirect(url_for('team'))
    try:
        db.execute('INSERT INTO users (email, name, password, role, is_active) VALUES (?,?,?,?,1)',
                   (email, name, hash_pw(pw), role))
        db.commit()
        flash(f'Team member {name} added as {role}!', 'success')
    except sqlite3.IntegrityError:
        flash('Email already exists.', 'error')
    return redirect(url_for('team'))

@app.route('/admin/team/<int:user_id>/edit', methods=['POST'])
@login_required
@admin_required
@csrf_required
def edit_team_member(user_id):
    db    = get_db()
    email = request.form.get('email', '').strip().lower()
    name  = request.form.get('name', '').strip()
    pw    = request.form.get('password', '').strip()
    role  = request.form.get('role', 'adjuster')
    if role not in ('adjuster', 'manager', 'admin'):
        role = 'adjuster'
    if not email:
        flash('Email is required.', 'error')
        return redirect(url_for('team'))
    if pw:
        ok, err = _validate_password(pw)
        if not ok:
            flash(err, 'error')
            return redirect(url_for('team'))
    # Only admin can change role to/from admin
    if session.get('role') != 'admin':
        # Managers can't create/edit admins — preserve existing role if trying to set admin
        if role == 'admin':
            role = 'manager'
    try:
        if pw:
            db.execute('UPDATE users SET email=?, name=?, password=?, role=? WHERE id=?',
                       (email, name, hash_pw(pw), role, user_id))
        else:
            db.execute('UPDATE users SET email=?, name=?, role=? WHERE id=?',
                       (email, name, role, user_id))
        db.commit()
        flash(f'Team member {name} updated!', 'success')
    except sqlite3.IntegrityError:
        flash('Email already exists.', 'error')
    return redirect(url_for('team'))

@app.route('/admin/team/<int:user_id>/delete', methods=['POST'])
@login_required
@admin_required
@csrf_required
def delete_team_member(user_id):
    if user_id == session['user_id']:
        flash("Can't delete yourself.", 'error')
        return redirect(url_for('team'))
    db = get_db()
    # Don't allow deleting the last admin
    target = db.execute('SELECT role FROM users WHERE id=?', (user_id,)).fetchone()
    if target and target['role'] == 'admin':
        admin_count = db.execute("SELECT COUNT(*) as c FROM users WHERE role='admin'").fetchone()['c']
        if admin_count <= 1:
            flash("Can't delete the last admin.", 'error')
            return redirect(url_for('team'))
    db.execute('UPDATE claims SET adjuster_id=NULL WHERE adjuster_id=?', (user_id,))
    db.execute('DELETE FROM willie_conversations WHERE user_id=?', (user_id,))
    db.execute('DELETE FROM users WHERE id=?', (user_id,))
    db.commit()
    flash('Team member removed.', 'success')
    return redirect(url_for('team'))

@app.route('/admin/team/<int:user_id>/deactivate', methods=['POST'])
@login_required
@admin_required
@csrf_required
def deactivate_adjuster(user_id):
    db = get_db()
    user = db.execute('SELECT name, role FROM users WHERE id=?', (user_id,)).fetchone()
    if user and user['role'] == 'adjuster':
        db.execute('UPDATE users SET is_active=0 WHERE id=?', (user_id,))
        db.commit()
        flash(f'Adjuster {user["name"] or user_id} deactivated.', 'success')
    return redirect(url_for('team'))

@app.route('/admin/team/<int:user_id>/reactivate', methods=['POST'])
@login_required
@admin_required
@csrf_required
def reactivate_adjuster(user_id):
    db = get_db()
    user = db.execute('SELECT name FROM users WHERE id=?', (user_id,)).fetchone()
    db.execute('UPDATE users SET is_active=1 WHERE id=?', (user_id,))
    db.commit()
    flash(f'Adjuster {user["name"] if user else user_id} reactivated.', 'success')
    return redirect(url_for('team'))


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

# _migrate_training_tables() now called lazily in get_db()

# ── Client Feedback Studio ────────────────────────────────────────────────────

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

_migrate_feedback_tables()

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


# ═══════════════════════════════════════════════════════════════════════════════
# PUBLIC: Become a Flood Adjuster — Training, Exams & Application
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/become-an-agent', methods=['GET'])
def become_an_agent():
    """Public landing page for becoming a flood adjuster."""
    db = get_db()
    modules = db.execute(
        'SELECT * FROM training_modules WHERE is_active=1 ORDER BY sort_order, module_num'
    ).fetchall()
    return render_template('become_agent.html', modules=modules)


@app.route('/training/<slug>', methods=['GET'])
def training_module(slug):
    """View a single training module."""
    db = get_db()
    module = db.execute(
        'SELECT * FROM training_modules WHERE slug=? AND is_active=1', (slug,)
    ).fetchone()
    if not module:
        flash('Training module not found.', 'error')
        return redirect(url_for('become_an_agent'))
    return render_template('training_module.html', module=module)


@app.route('/practice-exam', methods=['GET', 'POST'])
def practice_exam():
    """Practice exam — AI generates random questions each time."""
    db = get_db()
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip().lower()
        if not name or not email:
            flash('Please enter your name and email to start the exam.', 'error')
            return redirect(url_for('practice_exam'))
        # Generate exam questions via AI
        questions = _generate_practice_questions(db)
        token = secrets.token_urlsafe(16)
        db.execute(
            'INSERT INTO exam_sessions (candidate_name, candidate_email, session_token, questions_json, total_questions, is_practice) VALUES (?,?,?,?,?,1)',
            (name, email, token, json.dumps(questions), len(questions))
        )
        db.commit()
        return redirect(url_for('practice_exam_take', token=token))
    return render_template('practice_exam_start.html')


@app.route('/practice-exam/<token>', methods=['GET', 'POST'])
def practice_exam_take(token):
    """Take a practice exam."""
    db = get_db()
    session = db.execute('SELECT * FROM exam_sessions WHERE session_token=? AND is_practice=1', (token,)).fetchone()
    if not session:
        flash('Exam session not found or expired.', 'error')
        return redirect(url_for('practice_exam'))
    questions = json.loads(session['questions_json'])
    if request.method == 'POST':
        answers = {}
        for q in questions:
            qid = str(q['id'])
            answers[qid] = request.form.get(qid, '')
        score = 0
        for q in questions:
            if answers.get(str(q['id']), '').lower() == q['answer'].lower():
                score += 1
        pct = int(score / len(questions) * 100) if questions else 0
        db.execute(
            'UPDATE exam_sessions SET answers_json=?, score=?, is_completed=1, completed_at=CURRENT_TIMESTAMP WHERE session_token=?',
            (json.dumps(answers), pct, token)
        )
        db.commit()
        return render_template('practice_exam_results.html', score=pct, total=len(questions), correct=score, questions=questions, answers=answers)
    return render_template('practice_exam_take.html', questions=questions, token=token, name=session['candidate_name'])


def _generate_practice_questions(db):
    """Generate 20 random practice questions covering flood adjustment knowledge."""
    import random
    question_pool = [
        # Water Categories & Classes
        {"q": "What is Water Category 1?", "options": ["Clean water from a broken pipe", "Grey water from a washing machine", "Black water from sewage", "Salt water from the ocean"], "answer": "A", "topic": "Water Categories"},
        {"q": "What is Water Category 3 also known as?", "options": ["Clean water", "Grey water", "Black water / Grossly contaminated", "Mineral water"], "answer": "C", "topic": "Water Categories"},
        {"q": "Which water class affects only the floor area of a room?", "options": ["Class 1", "Class 2", "Class 3", "Class 4"], "answer": "A", "topic": "Water Classes"},
        {"q": "What does Water Class 4 indicate?", "options": ["Only the floor is wet", "Walls are affected up to 24 inches", "The entire room is saturated", "Specialty drying for hardwood, concrete, or plaster"], "answer": "D", "topic": "Water Classes"},
        {"q": "Water from a toilet overflow with urine is classified as which category?", "options": ["Category 1", "Category 2", "Category 3", "Category 0"], "answer": "B", "topic": "Water Categories"},
        # NFIP / FEMA Knowledge
        {"q": "What is the maximum structure coverage for residential NFIP?", "options": ["$100,000", "$250,000", "$500,000", "$1,000,000"], "answer": "B", "topic": "NFIP"},
        {"q": "How long is the NFIP waiting period before a new policy takes effect?", "options": ["24 hours", "7 days", "30 days", "90 days"], "answer": "C", "topic": "NFIP"},
        {"q": "What is ICC coverage in an NFIP policy?", "options": ["Interstate Commerce Coverage", "Increased Cost of Compliance", "Insurance Claim Compensation", "International Claims Coverage"], "answer": "B", "topic": "NFIP"},
        {"q": "How long does a NFIP policyholder have to file a Proof of Loss?", "options": ["30 days", "60 days", "90 days", "1 year"], "answer": "B", "topic": "NFIP"},
        {"q": "What is a Preferred Risk Policy (PRP)?", "options": ["The most expensive flood policy", "A lower-cost policy for moderate-to-low risk zones", "A policy for commercial buildings only", "A temporary policy"], "answer": "B", "topic": "NFIP"},
        # Flood Damage Assessment
        {"q": "Within how many hours can mold start growing after water intrusion?", "options": ["2-4 hours", "6-12 hours", "24-48 hours", "7 days"], "answer": "C", "topic": "Damage Assessment"},
        {"q": "What is the first thing an adjuster should do upon arriving at a flood-damaged property?", "options": ["Start documenting with photos", "Begin water extraction", "Remove drywall", "Set up drying equipment"], "answer": "A", "topic": "Damage Assessment"},
        {"q": "What does 'wicking' refer to in flood damage?", "options": ["Water evaporating from surfaces", "Water being drawn upward into walls and materials", "Water being pumped out of a basement", "Water changing from category to category"], "answer": "B", "topic": "Damage Assessment"},
        {"q": "Which material is MOST likely to be salvageable after Category 1 water damage?", "options": ["Drywall", "Fiberglass insulation", "Concrete block", "Carpet padding"], "answer": "C", "topic": "Damage Assessment"},
        {"q": "What document must be signed by the policyholder to finalize an NFIP claim payment?", "options": ["A contractor estimate", "A Proof of Loss form", "A police report", "A home inspection report"], "answer": "B", "topic": "Damage Assessment"},
        # Adjuster Licensing
        {"q": "Which organization provides adjuster licensing in most states?", "options": ["FEMA", "State Department of Insurance", "NFIP", "Department of Housing"], "answer": "B", "topic": "Licensing"},
        {"q": "What is a WYO company?", "options": ["A company that writes flood insurance policies through NFIP", "A FEMA emergency response team", "A state licensing board", "A restoration contractor association"], "answer": "A", "topic": "Licensing"},
        {"q": "An independent adjuster typically works for:", "options": ["One specific insurance company", "Multiple insurance companies on a contract basis", "FEMA directly", "The state government"], "answer": "B", "topic": "Licensing"},
        # FloodClaims Pro Platform
        {"q": "What AI assistant is built into FloodClaims Pro?", "options": ["FloodBot", "Aquila", "ClaimMaster", "AdjusterAI"], "answer": "B", "topic": "Platform"},
        {"q": "What feature does FloodClaims Pro use to analyze damage photos?", "options": ["Manual sketching", "Photo-to-Claim AI analysis", "Video recording only", "Handwritten notes"], "answer": "B", "topic": "Platform"},
        # Safety & Standards
        {"q": "What PPE should be worn in a Category 3 water damage environment?", "options": ["No special equipment needed", "Gloves only", "Full PPE including respirator, gloves, and waterproof suit", "Hard hat only"], "answer": "C", "topic": "Safety"},
        {"q": "What is the primary purpose of an elevation certificate?", "options": ["To prove ownership", "To determine flood insurance rates and building compliance", "To file a tax deduction", "To apply for a building permit"], "answer": "B", "topic": "Safety"},
        # Claims Process
        {"q": "What is the first step in the insurance claims process after a flood?", "options": ["Hire a contractor", "File the claim with the insurance company", "Begin repairs", "Throw away damaged items"], "answer": "B", "topic": "Claims Process"},
        {"q": "Which zone is considered the highest coastal flood risk?", "options": ["Zone A", "Zone AE", "Zone V", "Zone X"], "answer": "C", "topic": "Claims Process"},
    ]
    # Pick 20 random questions each time
    selected = random.sample(question_pool, min(20, len(question_pool)))
    for i, q in enumerate(selected):
        q['id'] = i + 1
        # Shuffle options but track correct answer
        opts = list(zip(['A', 'B', 'C', 'D'], q['options']))
        random.shuffle(opts)
        q['options'] = [o[1] for o in opts]
        for j, (letter, text) in enumerate(opts):
            if letter == q['answer']:
                q['answer'] = ['A', 'B', 'C', 'D'][j]
                break
    return selected


@app.route('/apply-adjuster', methods=['GET', 'POST'])
def apply_adjuster():
    """Public application form for prospective adjusters."""
    db = get_db()
    if request.method == 'POST':
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip().lower()
        phone = request.form.get('phone', '').strip()
        state = request.form.get('state', '').strip()
        licensed = 1 if request.form.get('licensed') else 0
        license_number = request.form.get('license_number', '').strip()
        exam_score = request.form.get('exam_score', '').strip()
        if not name or not email or not state:
            flash('Name, email, and state are required.', 'error')
            return redirect(url_for('apply_adjuster'))
        try:
            db.execute(
                'INSERT INTO adjuster_applications_v2 (name, email, phone, state, licensed, license_number, exam_score, status) VALUES (?,?,?,?,?,?,?,?)',
                (name, email, phone, state, licensed, license_number, int(exam_score) if exam_score else None, 'interested')
            )
            db.commit()
            flash('Application submitted! We\'ll be in touch soon.', 'success')
        except Exception as e:
            flash('Error submitting application. Email may already be registered.', 'error')
        return redirect(url_for('become_an_agent'))
    return render_template('apply_adjuster.html')


# ═══════════════════════════════════════════════════════════════════════════════
# Admin: Recruitment Management (existing)
# ═══════════════════════════════════════════════════════════════════════════════

@app.route('/admin/recruit', methods=['GET', 'POST'])
@login_required
@admin_required
def recruit():
    db = get_db()
    if request.method == 'POST':
        app_type = request.form.get('app_type', '')
        name = request.form.get('name', '').strip()
        email = request.form.get('email', '').strip().lower()
        phone = request.form.get('phone', '').strip()
        state = request.form.get('state', '').strip()
        if not name or not email:
            flash('Name and email are required.', 'error')
            return redirect(url_for('recruit'))
        if app_type == 'adjuster':
            license_number = request.form.get('license_number', '').strip()
            if not license_number:
                flash('License number is required for adjuster applications.', 'error')
                return redirect(url_for('recruit'))
            try:
                db.execute(
                    'INSERT INTO adjuster_applications (name, email, phone, license_number, state, status) VALUES (?,?,?,?,?,?)',
                    (name, email, phone, license_number, state, 'pending'))
                db.commit()
                flash(f'Adjuster application submitted for {name}. Review and approve below.', 'success')
            except Exception as e:
                flash(f'Error submitting application: {e}', 'error')
        elif app_type == 'contractor':
            license_type = request.form.get('license_type', '').strip()
            license_number = request.form.get('license_number', '').strip()
            experience = request.form.get('experience_years', '').strip()
            try:
                db.execute(
                    'INSERT INTO contractor_applications (name, email, phone, license_type, license_number, state, experience_years, status) VALUES (?,?,?,?,?,?,?,?)',
                    (name, email, phone, license_type, license_number, state, experience, 'pending'))
                db.commit()
                flash(f'Contractor application submitted for {name}. They will need training and certification.', 'success')
            except Exception as e:
                flash(f'Error submitting application: {e}', 'error')
        return redirect(url_for('recruit'))

    adjuster_apps = db.execute(
        'SELECT * FROM adjuster_applications ORDER BY created_at DESC').fetchall()
    contractor_apps = db.execute(
        'SELECT * FROM contractor_applications ORDER BY created_at DESC').fetchall()
    return render_template('recruit.html', adjuster_apps=adjuster_apps, contractor_apps=contractor_apps)


@app.route('/admin/recruit/adjuster/<int:app_id>/approve', methods=['POST'])
@login_required
@admin_required
@csrf_required
def approve_adjuster_application(app_id):
    db = get_db()
    app = db.execute('SELECT * FROM adjuster_applications WHERE id=?', (app_id,)).fetchone()
    if not app:
        flash('Application not found.', 'error')
        return redirect(url_for('recruit'))
    # Check if user already exists
    existing = db.execute('SELECT id FROM users WHERE email=?', (app['email'],)).fetchone()
    if existing:
        flash('A user with this email already exists.', 'error')
        return redirect(url_for('recruit'))
    # Create user account
    import secrets as _secrets
    temp_pw = _secrets.token_urlsafe(10)
    db.execute(
        'INSERT INTO users (email, name, password, role) VALUES (?,?,?,?)',
        (app['email'], app['name'], hash_pw(temp_pw), 'adjuster'))
    # Mark application approved
    db.execute(
        'UPDATE adjuster_applications SET status=?, reviewed_at=CURRENT_TIMESTAMP, reviewed_by=? WHERE id=?',
        ('approved', session['user_id'], app_id))
    db.commit()
    flash(f'✅ {app["name"]} approved and added to team as Adjuster. Temp password: {temp_pw}', 'success')
    return redirect(url_for('recruit'))


@app.route('/admin/recruit/contractor/<int:app_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def contractor_detail(app_id):
    db = get_db()
    if request.method == 'POST':
        action = request.form.get('action', '')
        if action == 'approve_training':
            db.execute("UPDATE contractor_applications SET status='training', reviewed_at=CURRENT_TIMESTAMP, reviewed_by=? WHERE id=?",
                       (session['user_id'], app_id))
            db.commit()
            flash('Contractor approved for training.', 'success')
        elif action == 'update_progress':
            progress = int(request.form.get('progress', 0))
            db.execute('UPDATE contractor_applications SET progress=? WHERE id=?', (progress, app_id))
            db.commit()
            flash(f'Progress updated to {progress}%.', 'success')
        elif action == 'certify':
            # Convert contractor to adjuster
            app = db.execute('SELECT * FROM contractor_applications WHERE id=?', (app_id,)).fetchone()
            if app:
                existing = db.execute('SELECT id FROM users WHERE email=?', (app['email'],)).fetchone()
                if not existing:
                    import secrets as _secrets
                    temp_pw = _secrets.token_urlsafe(10)
                    db.execute(
                        'INSERT INTO users (email, name, password, role) VALUES (?,?,?,?)',
                        (app['email'], app['name'], hash_pw(temp_pw), 'adjuster'))
                db.execute("UPDATE contractor_applications SET status='certified', progress=100, reviewed_at=CURRENT_TIMESTAMP, reviewed_by=? WHERE id=?",
                           (session['user_id'], app_id))
                db.commit()
                flash(f'✅ {app["name"]} certified and added to team as Adjuster!', 'success')
        return redirect(url_for('contractor_detail', app_id=app_id))

    app = db.execute('SELECT * FROM contractor_applications WHERE id=?', (app_id,)).fetchone()
    if not app:
        flash('Application not found.', 'error')
        return redirect(url_for('recruit'))
    return render_template('contractor_detail.html', app=app)



# ── Recruitment Invitations ─────────────────────────────────────────────────────

@app.route('/admin/recruit/send-invite', methods=['POST'])
@login_required
@admin_required
@csrf_required
def send_recruit_invite():
    """Send a recruitment invitation email to a prospective adjuster."""
    to_email = request.form.get('invite_email', '').strip().lower()
    invite_name = request.form.get('invite_name', '').strip()
    if not to_email:
        flash('Email address is required.', 'error')
        return redirect(url_for('recruit'))

    sg_key = get_setting('sendgrid_api_key') or os.environ.get('SENDGRID_API_KEY', '')
    if not sg_key or not SENDGRID_OK:
        flash('⚠️ SendGrid not configured. Set your API key in AI Integration settings first.', 'error')
        return redirect(url_for('recruit'))

    from_email = get_setting('from_email') or os.environ.get('FROM_EMAIL', '')
    if not from_email:
        flash('⚠️ No "From" email set. Set it below before sending invitations.', 'error')
        return redirect(url_for('recruit'))

    join_url = request.host_url.rstrip('/') + url_for('become_agent')
    name_greeting = f"Hi {invite_name}," if invite_name else "Hi there,"

    html_body = f'''<div style="font-family:'Plus Jakarta Sans',sans-serif;max-width:600px;margin:0 auto;color:#1e293b">
        <div style="background:linear-gradient(135deg,#06D6C7,#3B7BFF);padding:28px;border-radius:16px 16px 0 0;text-align:center;">
            <div style="font-size:40px;margin-bottom:8px;">🌊</div>
            <h1 style="color:#fff;margin:0;font-size:1.6rem;font-weight:800;">FloodClaims Pro</h1>
            <p style="color:rgba(255,255,255,.85);margin:4px 0 0;font-size:.85rem;">Professional Flood Damage Assessment</p>
        </div>
        <div style="padding:28px;background:#fff;border:1px solid #e2e8f0;border-top:none;border-radius:0 0 16px 16px;">
            <p style="font-size:1rem;margin:0 0 12px;">{name_greeting}</p>
            <p style="font-size:.9rem;line-height:1.7;color:#475569;margin:0 0 16px;">
                You\'ve been invited to join a flood damage adjustment team on <strong>FloodClaims Pro</strong>.
                Whether you\'re an experienced adjuster or looking to get licensed, we make it easy to get started.
            </p>
            <div style="background:#f0f9ff;border:1px solid #bae6fd;border-radius:10px;padding:14px 18px;margin-bottom:20px;">
                <div style="font-size:.75rem;font-weight:700;text-transform:uppercase;letter-spacing:.04em;color:#0369a1;margin-bottom:8px;">What you can do</div>
                <ul style="font-size:.82rem;color:#475569;margin:0;padding-left:18px;line-height:1.8;">
                    <li>Manage flood damage claims end-to-end</li>
                    <li>AI-powered photo damage analysis</li>
                    <li>NFIP &amp; FEMA compliance tools</li>
                    <li>Free training & certification pathway</li>
                    <li>Work from anywhere</li>
                </ul>
            </div>
            <div style="text-align:center;margin-bottom:20px;">
                <a href="{join_url}" style="display:inline-block;padding:14px 32px;background:linear-gradient(135deg,#06D6C7,#3B7BFF);color:#fff;text-decoration:none;border-radius:10px;font-weight:700;font-size:.95rem;">🚀 Get Started — It\'s Free</a>
            </div>
            <p style="font-size:.78rem;color:#94a3b8;margin:0;line-height:1.6;">
                If the button doesn\'t work, copy this link: <br>
                <a href="{join_url}" style="color:#3B7BFF;word-break:break-all;">{join_url}</a>
            </p>
            <hr style="margin:20px 0;border:none;border-top:1px solid #e2e8f0;">
            <p style="font-size:.72rem;color:#94a3b8;margin:0;">
                FloodClaims Pro · Professional Flood Damage Assessment Platform<br>
                You received this email because an admin invited you to join their team.
            </p>
        </div>
    </div>'''

    sent = send_email(to_email, "🌊 You\'re Invited — Join FloodClaims Pro", html_body)
    if sent:
        flash(f'✅ Invitation sent to {to_email}', 'success')
    else:
        flash(f'❌ Failed to send invitation to {to_email}. Check SendGrid configuration.', 'error')
    return redirect(url_for('recruit'))


# ── Aquila Chat ────────────────────────────────────────────────────────────────

@app.route('/willie')
@login_required
def willie():
    db    = get_db()
    convs = db.execute(
        'SELECT * FROM willie_conversations WHERE user_id=? ORDER BY updated DESC LIMIT 100',
        (session['user_id'],)).fetchall()
    return render_template('willie.html', conversations=convs)

@app.route('/willie/conversations', methods=['POST'])
@login_required
def willie_new_conversation():
    db  = get_db()
    cur = db.execute('INSERT INTO willie_conversations (user_id) VALUES (?)', (session['user_id'],))
    db.commit()
    return jsonify({'id': cur.lastrowid, 'title': 'New Conversation'})

@app.route('/willie/conversations/<int:conv_id>')
@login_required
def willie_get_conversation(conv_id):
    db   = get_db()
    conv = db.execute('SELECT * FROM willie_conversations WHERE id=? AND user_id=?',
                      (conv_id, session['user_id'])).fetchone()
    if not conv:
        return jsonify({'error': 'not found'}), 404
    msgs = db.execute('SELECT role,content,created FROM willie_messages WHERE conversation_id=? ORDER BY id',
                      (conv_id,)).fetchall()
    return jsonify({'conversation': dict(conv), 'messages': [dict(m) for m in msgs]})

@app.route('/willie/conversations/<int:conv_id>', methods=['DELETE'])
@login_required
def willie_delete_conversation(conv_id):
    db = get_db()
    db.execute('DELETE FROM willie_messages WHERE conversation_id=?', (conv_id,))
    db.execute('DELETE FROM willie_conversations WHERE id=? AND user_id=?', (conv_id, session['user_id']))
    db.commit()
    return jsonify({'ok': True})

@app.route('/willie/conversations/<int:conv_id>/messages', methods=['POST'])
@login_required
def willie_save_message(conv_id):
    db      = get_db()
    conv    = db.execute('SELECT * FROM willie_conversations WHERE id=? AND user_id=?',
                         (conv_id, session['user_id'])).fetchone()
    if not conv:
        return jsonify({'error': 'not found'}), 404
    data    = request.get_json(silent=True) or {}
    role    = data.get('role', 'user')
    content = data.get('content', '').strip()
    if not content:
        return jsonify({'error': 'content required'}), 400
    db.execute('INSERT INTO willie_messages (conversation_id, role, content) VALUES (?,?,?)',
               (conv_id, role, content))
    # Auto-title from first user message
    if role == 'user' and conv['title'] == 'New Conversation':
        title = content[:60] + ('...' if len(content) > 60 else '')
        db.execute('UPDATE willie_conversations SET title=?, updated=CURRENT_TIMESTAMP WHERE id=?',
                   (title, conv_id))
    else:
        db.execute('UPDATE willie_conversations SET updated=CURRENT_TIMESTAMP WHERE id=?', (conv_id,))
    db.commit()
    return jsonify({'ok': True})

@app.route('/willie/chat', methods=['POST'])
@login_required
def willie_chat():
    """Smart Aquila chat proxy — injects live app context into every message."""
    data       = request.get_json(silent=True) or {}
    message    = data.get('message', '').strip()
    history    = data.get('history', [])
    conv_id    = data.get('conversation_id')
    session_id = data.get('session_id', '')
    claim_id   = data.get('claim_id')       # passed when chatting from a claim page
    context_hint = data.get('context', '') # page context hint
    if not message:
        return jsonify({'error': 'message required'}), 400

    FLOOD_BASE = request.host_url.rstrip('/')

    # ── Build rich live context ─────────────────────────────────────────
    db = get_db()
    now_str = datetime.datetime.now().strftime('%A %B %d, %Y %I:%M %p')

    # Dashboard stats
    if session['role'] == 'admin':
        all_claims = db.execute('SELECT id, claim_number, client_name, property_address, status, total_estimate, flood_date, adjuster_id FROM claims ORDER BY created_at DESC').fetchall()
    else:
        all_claims = db.execute('SELECT id, claim_number, client_name, property_address, status, total_estimate, flood_date, adjuster_id FROM claims WHERE adjuster_id=? ORDER BY created_at DESC', (session['user_id'],)).fetchall()

    stats = {
        'total': len(all_claims),
        'new': sum(1 for c in all_claims if c['status'] == 'New'),
        'in_progress': sum(1 for c in all_claims if c['status'] == 'In Progress'),
        'submitted': sum(1 for c in all_claims if c['status'] == 'Submitted'),
        'closed': sum(1 for c in all_claims if c['status'] == 'Closed'),
        'pipeline': sum(c['total_estimate'] for c in all_claims if c['status'] != 'Closed'),
    }

    # Recent claims summary (last 10)
    recent_summary = '\n'.join(
        f'  - [{c["id"]}] {c["claim_number"]} | {c["client_name"]} | {c["property_address"][:40]} | {c["status"]} | ${c["total_estimate"]:,.0f}'
        for c in all_claims[:10]
    ) or '  (no claims yet)'

    # Current claim context (if on a claim page)
    claim_context = ''
    if claim_id:
        claim = db.execute('''
            SELECT c.*, u.name as adjuster_name
            FROM claims c LEFT JOIN users u ON c.adjuster_id=u.id
            WHERE c.id=?
        ''', (claim_id,)).fetchone()
        if claim:
            rooms = db.execute('SELECT * FROM rooms WHERE claim_id=? AND deleted_at IS NULL ORDER BY id', (claim_id,)).fetchall()
            room_lines = []
            for room in rooms:
                items = db.execute('SELECT * FROM line_items WHERE room_id=? AND deleted_at IS NULL ORDER BY id', (room['id'],)).fetchall()
                item_text = ', '.join(f'{i["description"]} ({i["quantity"]} {i["unit"]} @ ${i["unit_cost"]})' for i in items) or 'no items yet'
                room_lines.append(f'    Room [{room["id"]}] {room["name"]} (${room["subtotal"]:,.2f}): {item_text}')
            photos = db.execute('SELECT COUNT(*) as c FROM photos WHERE claim_id=? AND deleted_at IS NULL', (claim_id,)).fetchone()

            _nl = chr(10)
            rooms_text = _nl.join(room_lines) if room_lines else '    (no rooms added yet)'
            claim_context = (
                _nl + '┌─ CURRENT CLAIM (you are viewing this right now) ─────────────────────────' + _nl
                + f'| Claim ID: {claim["id"]} | Claim #: {claim["claim_number"]}' + _nl
                + f'| Client: {claim["client_name"]} | Phone: {claim["client_phone"] or "N/A"}' + _nl
                + f'| Property: {claim["property_address"]}' + _nl
                + f'| Status: {claim["status"]} | Priority: {claim["priority"] or "Normal"}' + _nl
                + f'| Flood Date: {claim["flood_date"]} | Source: {claim["flood_source"] or "N/A"}' + _nl
                + f'| Water: Category {claim["water_category"] or "?"} / Class {claim["water_class"] or "?"} / {claim["water_depth_in"] or "?"} inches' + _nl
                + f'| Insurance: {claim["insurance_company"] or "N/A"} | Policy: {claim["policy_number"] or "N/A"}' + _nl
                + f'| Coverage: Bldg ${claim["coverage_building"]:,.0f} | Contents ${claim["coverage_contents"]:,.0f} | Deductible ${claim["deductible"]:,.0f}' + _nl
                + f'| FEMA Zone: {claim["flood_zone"] or "NOT LOOKED UP"} | Map: {claim["fema_map_number"] or "N/A"}' + _nl
                + f'| Total Estimate: ${claim["total_estimate"]:,.2f} | Photos: {photos["c"]}' + _nl
                + f'| Adjuster: {claim["adjuster_name"] or "Unassigned"}' + _nl
                + f'| Rooms ({len(rooms)}):' + _nl
                + rooms_text + _nl
                + '└' + '─' * 65
            )

    # Today's inspections
    today_str = datetime.date.today().isoformat()
    todays_slots = db.execute('''
        SELECT s.*, c.claim_number, c.client_name, c.property_address
        FROM inspection_slots s JOIN claims c ON s.claim_id=c.id
        WHERE s.slot_date=? AND s.status != 'cancelled'
        ORDER BY s.slot_time
    ''', (today_str,)).fetchall()
    todays_insp = '\n'.join(
        f'  {s["slot_time"]} — {s["claim_number"]} ({s["client_name"]}) at {s["property_address"][:50]}'
        for s in todays_slots
    ) or '  None scheduled today'

    # Build the enriched system context injected as first message
    system_context = f"""You are Aquila, an expert AI flood damage adjuster assistant embedded inside FloodClaims Pro.
You have FULL CONTROL of the app and can take actions directly on behalf of the adjuster.
Always be helpful, concise, and professional. Use your knowledge of NFIP rules and flood claim procedures.

CURRENT USER: {session.get('name') or session.get('email')} (role: {session.get('role', 'adjuster')})
CURRENT TIME: {now_str}
PAGE CONTEXT: {context_hint or 'Dashboard'}

APP SNAPSHOT:
- Total claims: {stats['total']} | New: {stats['new']} | In Progress: {stats['in_progress']} | Submitted: {stats['submitted']} | Closed: {stats['closed']}
- Open pipeline value: ${stats['pipeline']:,.2f}
- Today's inspections: 
{todays_insp}

RECENT CLAIMS (use claim IDs below for API actions):
{recent_summary}
{claim_context}
YOUR CAPABILITIES (you can do all of these right now):
1. CREATE claims — just ask for client name, address, flood date
2. UPDATE any claim field — status, priority, water category, coverage, notes, etc.
3. ADD rooms and line items to any claim with exact pricing
4. LOOK UP claims by name, number, or address
5. RUN AI estimates on any claim
6. ANALYZE photos on any claim and suggest rooms/items
7. SCHEDULE inspections with date, time, adjuster
8. MOVE claims between pipeline stages
9. CHECK NFIP compliance score for any claim
10. LOOK UP FEMA flood zones by address
11. SEND client notifications
12. VIEW analytics and team data
13. ADD team members
14. ANSWER any question about NFIP rules, flood damage, water categories, pricing, procedures

NFIP EXPERTISE:
- Water Categories: Cat 1=clean, Cat 2=gray water, Cat 3=black/flood water (always Cat 3 for rising floodwater)
- Water Classes: 1=floors only, 2=walls up 24”, 3=ceiling wet, 4=hardwood/brick specialty drying
- Proof of Loss must be filed within 60 days of flood date or claim is denied
- 2026 national avg flood claim: $10,234-$11,605 | Full restoration: $5,000-$16,000
- Mitigation rates: Cat3 extraction $0.75-$1.50/sf, air movers $38-$55/day, dehumidifier $83-$110/day
- Drywall Cat3 tear-out: $1.79/sf | LVP/vinyl: $1.25-$2.00/sf | Hardwood: $5.82/sf
- Reconstruction: drywall $3.99-$5.50/sf | paint $1.50-$2.50/sf | baseboard R&R $5.51/lf
- FEMA flood zones: A/AE/AO/AH=high risk, B/X=moderate, C/X=minimal risk

When the user asks you to DO something (create a claim, add a room, update status, etc.):
1. Confirm what you are about to do
2. Use your API actions to execute it immediately
3. Report back what was done with specifics
4. Suggest the logical next step

When asked for advice, give specific, actionable NFIP-compliant guidance.
When referencing a claim, always use its claim number AND name so it's clear."""

    # Build payload with context prepended as a system message in history
    enriched_history = [
        {'role': 'system', 'content': system_context}
    ] + history[-8:]

    # ── Build brain-augmented system prompt from local DB ─────────────────
    brain_identity  = get_setting('brain_identity_md', '')
    brain_soul      = get_setting('brain_soul_md', '')
    brain_memory    = get_setting('brain_memory_md', '')
    brain_system    = get_setting('brain_system_prompt', '')

    # Default brain if nothing saved yet
    default_system = brain_system or """You are Aquila, an expert AI flood damage adjuster assistant embedded inside FloodClaims Pro.
You have FULL CONTROL of the app and can take actions directly on behalf of the adjuster.
Always be helpful, concise, and professional. Use your knowledge of NFIP rules and flood claim procedures."""

    # Prepend brain files to system context
    brain_prefix = ''
    if brain_identity:
        brain_prefix += f"\n\n## Your Identity\n{brain_identity}"
    if brain_soul:
        brain_prefix += f"\n\n## Your Soul\n{brain_soul}"
    if brain_memory:
        brain_prefix += f"\n\n## Your Memory\n{brain_memory}"

    final_system = default_system + brain_prefix

    # Replace the old system_context with the brain-augmented one
    # but keep all the live app context (claims, dashboard, etc.)
    live_context = system_context.replace(
        'You are Aquila, an expert AI flood damage adjuster assistant embedded inside FloodClaims Pro.\nYou have FULL CONTROL of the app and can take actions directly on behalf of the adjuster.\nAlways be helpful, concise, and professional. Use your knowledge of NFIP rules and flood claim procedures.',
        final_system
    )

    # Build messages for OpenRouter
    messages = [{'role': 'system', 'content': live_context}] + history[-8:]

    try:
        selected_model   = get_setting('ai_chat_model') or get_setting('ai_model', 'openrouter/owl-alpha')
        fallback_model   = get_setting('ai_fallback_model', 'anthropic/claude-sonnet-4-5')
        openrouter_key   = OPENROUTER_KEY
        if not openrouter_key:
            return jsonify({'reply': '⚠️ OpenRouter API key not configured. Go to Settings → AI Integration to set it up.'})

        reply = None
        models_to_try = [selected_model]
        if fallback_model and fallback_model != selected_model:
            models_to_try.append(fallback_model)

        for model in models_to_try:
            try:
                r = _req.post(
                    'https://openrouter.ai/api/v1/chat/completions',
                    headers={'Authorization': f'Bearer {openrouter_key}', 'Content-Type': 'application/json'},
                    json={'model': model, 'messages': messages, 'max_tokens': 2000},
                    timeout=45
                )
                d = r.json()
                if 'error' in d:
                    err = d['error']
                    if isinstance(err, dict):
                        err = err.get('message', str(err))
                    if model == models_to_try[-1]:
                        reply = f'⚠️ AI error: {err}'
                    continue
                reply = d['choices'][0]['message']['content']
                if model != selected_model:
                    reply = f'[Used fallback: {model}]\n\n{reply}'
                break
            except Exception as inner_e:
                if model == models_to_try[-1]:
                    reply = f'Aquila is unavailable right now. ({str(inner_e)[:80]})'

        reply = reply or 'Aquila is unavailable right now.'
    except Exception as e:
        reply = f'Aquila is unavailable right now. ({str(e)[:80]})'

    # Save to conversation history
    if conv_id:
        conv = db.execute('SELECT * FROM willie_conversations WHERE id=? AND user_id=?',
                          (conv_id, session['user_id'])).fetchone()
        if conv:
            db.execute('INSERT INTO willie_messages (conversation_id,role,content) VALUES (?,?,?)',
                       (conv_id, 'user', message))
            db.execute('INSERT INTO willie_messages (conversation_id,role,content) VALUES (?,?,?)',
                       (conv_id, 'assistant', reply))
            if conv['title'] == 'New Conversation':
                title = message[:60] + ('...' if len(message) > 60 else '')
                db.execute('UPDATE willie_conversations SET title=?,updated=CURRENT_TIMESTAMP WHERE id=?',
                           (title, conv_id))
            else:
                db.execute('UPDATE willie_conversations SET updated=CURRENT_TIMESTAMP WHERE id=?', (conv_id))
            db.commit()

    return jsonify({'reply': reply})

# ── Aquila External API ──────────────────────────────────────────────────────────────
# All routes accept: Authorization: Bearer <willie_token>
# Get token from: GET /willie/token (admin session required)

# ── Instant AI photo analysis (used by new claim form before submit) ────────────────

@app.route('/api/analyze-photo', methods=['POST'])
@login_required
def api_analyze_photo():
    data     = request.get_json(silent=True) or {}
    img_b64  = data.get('image', '')
    mime     = data.get('mime', 'image/jpeg')
    if not img_b64:
        return jsonify({'error': 'no image'}), 400
    key = get_setting('openrouter_api_key') or OPENROUTER_KEY
    if not key:
        return jsonify({'description': ''})
    try:
        selected_model = get_setting('ai_vision_model') or get_setting('ai_model', 'openrouter/auto')
        # Ensure vision-capable
        _text_only = {'openrouter/owl-alpha', 'openrouter/owl', 'openai/o3-mini', 'deepseek/deepseek-r1'}
        if selected_model in _text_only:
            selected_model = 'openrouter/auto'
        r = _req.post(
            'https://openrouter.ai/api/v1/chat/completions',
            headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
            json={
                'model': selected_model,
                'messages': [{'role': 'user', 'content': [
                    {'type': 'text', 'text': (
                        'You are a professional flood damage adjuster. Analyze this photo and provide '
                        'a concise 2-3 sentence assessment covering: (1) what is damaged, '
                        '(2) severity and water category if visible, '
                        '(3) immediate repair needs. Be specific and professional.'
                    )},
                    {'type': 'image_url', 'image_url': {'url': f'data:{mime};base64,{img_b64}'}}
                ]}],
                'max_tokens': 200
            }, timeout=30)
        desc = r.json()['choices'][0]['message']['content']
        return jsonify({'description': desc})
    except Exception:
        return jsonify({'description': ''})

@app.route('/willie/token')
@login_required
def willie_token():
    """Show the Willie API token to admin users."""
    if session.get('role') != 'admin':
        return jsonify({'error': 'admin required'}), 403
    return jsonify({'token': get_willie_token(),
                    'base_url': request.host_url.rstrip('/'),
                    'note': 'Use this as Authorization: Bearer <token> in Willie actions'})

@app.route('/willie/api/claims', methods=['GET'])
def willie_list_claims():
    if not willie_auth():
        return jsonify({'error': 'unauthorized'}), 401
    db = get_db()
    claims = db.execute('''
        SELECT c.id, c.claim_number, c.client_name, c.property_address,
               c.flood_date, c.status, c.total_estimate,
               u.name as adjuster_name
        FROM claims c LEFT JOIN users u ON c.adjuster_id=u.id
        ORDER BY c.created_at DESC LIMIT 50
    ''').fetchall()
    return jsonify({'ok': True, 'claims': [dict(c) for c in claims], 'count': len(claims)})

@app.route('/willie/api/claims/lookup', methods=['GET'])
def willie_lookup_claim():
    """Look up a claim by claim_number (e.g. FC-202604-FBA7C7) or partial client name."""
    if not willie_auth():
        return jsonify({'error': 'unauthorized'}), 401
    claim_number = request.args.get('claim_number', '').strip()
    client_name  = request.args.get('client_name', '').strip()
    db = get_db()
    if claim_number:
        claim = db.execute(
            'SELECT c.*, u.name as adjuster_name FROM claims c LEFT JOIN users u ON c.adjuster_id=u.id WHERE c.claim_number=?',
            (claim_number,)).fetchone()
        if not claim:
            return jsonify({'ok': False, 'error': f'No claim found with number {claim_number}'}), 404
        rooms = db.execute('SELECT * FROM rooms WHERE claim_id=? AND deleted_at IS NULL ORDER BY id', (claim['id'],)).fetchall()
        room_data = []
        for r in rooms:
            items = db.execute('SELECT * FROM line_items WHERE room_id=? AND deleted_at IS NULL ORDER BY id', (r['id'],)).fetchall()
            room_data.append({'room': dict(r), 'line_items': [dict(i) for i in items]})
        return jsonify({'ok': True, 'claim': dict(claim), 'rooms': room_data})
    elif client_name:
        claims = db.execute(
            'SELECT id, claim_number, client_name, status, total_estimate FROM claims WHERE client_name LIKE ? ORDER BY created_at DESC',
            (f'%{client_name}%',)).fetchall()
        return jsonify({'ok': True, 'claims': [dict(c) for c in claims], 'count': len(claims)})
    return jsonify({'error': 'Provide claim_number or client_name as query param'}), 400


@app.route('/willie/api/claims/<int:claim_id>/estimate', methods=['POST'])
def willie_generate_estimate(claim_id):
    """Use AI (with photo vision) to generate a full adjuster-style estimate."""
    if not willie_auth():
        return jsonify({'error': 'unauthorized'}), 401
    db = get_db()
    claim = db.execute('SELECT * FROM claims WHERE id=?', (claim_id,)).fetchone()
    if not claim:
        return jsonify({'error': 'Claim not found'}), 404
    claim = dict(claim)  # convert sqlite3.Row → dict so .get() works

    key = get_setting('openrouter_api_key') or OPENROUTER_KEY
    model = get_setting('ai_chat_model') or get_setting('ai_model', 'openrouter/owl-alpha')
    if not key:
        return jsonify({'error': 'OpenRouter API key not configured. Add it in Settings.'}), 400

    # Gather rooms + items
    rooms = db.execute('SELECT * FROM rooms WHERE claim_id=? AND deleted_at IS NULL ORDER BY id', (claim_id,)).fetchall()
    room_summary = []
    for r in rooms:
        items = db.execute('SELECT * FROM line_items WHERE room_id=? AND deleted_at IS NULL ORDER BY id', (r['id'],)).fetchall()
        item_list = '; '.join([f"{i['description']} x{i['quantity']} {i['unit']} @${i['unit_cost']:.2f}" for i in items]) or 'No line items yet'
        room_summary.append(f"  Room: {r['name']}\n  Items: {item_list}")

    # Analyze all photos with vision AI
    photos = [dict(p) for p in db.execute('SELECT * FROM photos WHERE claim_id=? AND deleted_at IS NULL ORDER BY id', (claim_id,)).fetchall()]
    photo_analyses = []
    for photo in photos[:8]:  # limit to 8 photos to avoid token overflow
        photo_path = os.path.join(UPLOAD_DIR, photo['filename'])
        desc = photo.get('ai_description', '') or ''
        # Clear cached error strings so they get retried
        if desc.startswith('AI analysis failed') or desc.startswith('Error'):
            desc = ''
            db.execute('UPDATE photos SET ai_description=NULL WHERE id=?', (photo['id'],))
            db.commit()
        if not desc and os.path.exists(photo_path):
            desc = ai_describe_photo(photo_path)
            if desc:
                db.execute('UPDATE photos SET ai_description=? WHERE id=?', (desc, photo['id']))
                db.commit()
        if desc:
            photo_analyses.append(f"Photo ({photo['caption'] or photo['filename']}): {desc}")

    photo_section = '\n'.join(photo_analyses) if photo_analyses else 'No photos uploaded yet.'
    room_section  = '\n'.join(room_summary) if room_summary else 'No rooms documented yet.'

    PRICING_KNOWLEDGE_BASE = """
=== 2026 FLOOD RESTORATION PRICING REFERENCE (USE THESE RATES) ===

NATIONAL AVERAGES (2026 data — Palm Build, NuBilt, Angi, Xactimate):
- Average water damage claim payout: $10,234–$11,605
- Typical full restoration (mitigation + rebuild): $5,000–$16,000
- Per sq ft mitigation only: $3.00–$7.50/sf
- Per sq ft full rebuild: $20.00–$37.00/sf
- Myrtle Beach / South Carolina local rate: $14–$16/sf (cleanup), $20–$30/sf (rebuild)
- 1 inch of standing floodwater → ~$25,000 in damage to a typical home (FEMA/NFIP data)

WATER CATEGORIES (IICRC):
- Cat 1 (clean water): $3.50/sf mitigation
- Cat 2 (gray water/appliance): $5.25/sf mitigation
- Cat 3 (black water/floodwater/sewage): $7.50/sf mitigation + biohazard uplift
  → Flood water from outside IS always Cat 3

WATER CLASSES:
- Class 1 (partial room, floors only): 24–48h dry-out
- Class 2 (full room, walls <24" wicking): 48–72h dry-out
- Class 3 (ceiling/walls saturated): 72–96h dry-out
- Class 4 (specialty — brick, hardwood, concrete): 120h+ dry-out

MITIGATION LINE ITEMS (Xactimate-based 2024–2026):
- Emergency service call (business hours): $271–$407 EA
- Water extraction / pumping: $0.75–$1.50/sf
- Air mover (per 24h): $38–$55 EA (typically 1 per 50–100 sf)
- Dehumidifier 70–109 ppd (per 24h): $83–$110 EA (typically 1 per 500–1,000 sf)
- Wall cavity drying — injection type (per 24h): $141 EA
- Antimicrobial treatment: $0.35–$0.50/sf
- Moisture mapping report: $250 flat
- Containment barriers: $0.18/sf
- Content manipulation / pack-out: $77/hr
- Debris hauling (dumpster): $350–$600 EA

DEMOLITION / TEAR-OUT:
- Tear out wet drywall Cat 3 (no bagging): $1.79/sf
- Tear out wet insulation (no bagging): $0.91/sf
- Tear out baseboard: $0.66/lf
- Tear out carpet + pad: $1.05–$1.50/sy (or $0.12–$0.17/sf)
- Tear out LVP/vinyl flooring: $1.25–$2.00/sf
- Tear out non-salvageable hardwood (bagged): $5.82/sf
- Tear out ceramic tile + mortar bed: $3.50–$5.00/sf
- Tear out subfloor (OSB/plywood): $2.00–$3.50/sf

DRYWALL REPLACEMENT:
- 1/2" drywall hung, taped, floated, ready for paint: $3.99–$5.50/sf
- Drywall repair (labor only, Myrtle Beach): $40–$60/hr
- Batt insulation 6" R19: $1.40–$2.00/sf
- Seal/prime + 2 coats paint walls: $1.50–$2.50/sf
- Baseboard 4-1/4" R&R: $5.51/lf
- Seal & paint baseboard: $2.75/lf

FLOORING REPLACEMENT:
- Luxury Vinyl Plank (LVP) installed: $4.00–$8.00/sf (mid-grade $5.50)
- Carpet + pad installed: $3.50–$6.50/sf (mid-grade $4.50)
- Hardwood installed (mid-grade): $8.00–$14.00/sf
- Ceramic/porcelain tile installed: $7.00–$12.00/sf
- Subfloor OSB 3/4" R&R: $4.50–$6.00/sf

MOLD REMEDIATION:
- HEPA air scrubber (per 24h): $80–$115 EA
- Antimicrobial application: $0.35–$0.75/sf
- Mold remediation (contained area): $1,200–$3,800 total; $15–$30/sf for large areas
- Encapsulation coating: $1.00–$2.50/sf

ELECTRICAL / MECHANICAL:
- Electrical safety re-inspection after flood: $150–$400
- GFCI outlet R&R: $85–$150 EA
- Electrical outlet/switch R&R (standard): $45–$90 EA

CABINETS / KITCHEN:
- Base cabinet removal & replace (per LF): $175–$350/lf
- Upper cabinet removal & replace (per LF): $125–$250/lf
- Countertop replace (laminate): $25–$40/lf

DOORS / WINDOWS:
- Interior door unit R&R: $401–$550 EA
- Vinyl window single-hung 9–12 sf R&R: $392–$550 EA
- Door frame/jamb R&R: $254–$350 EA

CONTINGENCY & OVERHEAD:
- Standard contingency: 10–15% of subtotal
- Contractor O&P (overhead & profit): 20% on top of labor + materials (standard insurance practice)
- Sales tax on materials: ~8% (SC rate)

AVERAGE TOTAL COSTS BY CLAIM TYPE (2026 insurance data):
- Single room flood (200–400 sf): $8,000–$18,000
- Two-room flood: $15,000–$30,000
- Full first-floor flood (1,000–1,500 sf): $25,000–$60,000
- Basement flood: $10,000–$30,000
- NFIP average payout for flood claims: $66,000 (severe) / $10,234 (moderate)

KEY RULES FOR ADJUSTER ESTIMATES:
1. NEVER estimate below $8,000 for any claim showing visible drywall damage + flooring damage in 2+ photos
2. Flood water from outside = Cat 3 black water ALWAYS — this triggers biohazard protocols and higher rates
3. Any peeling paint/drywall visible in photos = walls need full replacement, not patch repair
4. Rotted/torn flooring visible = full room flooring replacement, not partial
5. Always include mitigation phase (extraction/drying) AND reconstruction phase in estimate
6. Add 10% contingency + 20% O&P to all estimates
7. If mold risk present (damage >48h old), add mold remediation line items
"""

    prompt = f"""You are a licensed public adjuster and flood damage estimator with 20 years of experience.
Analyze this flood damage claim and produce a complete professional estimate like you would submit to an insurance company.

You have access to a current 2026 pricing reference — USE THESE EXACT RATES, do not guess or use outdated numbers:
{PRICING_KNOWLEDGE_BASE}

=== CLAIM DETAILS ===
Claim #: {claim['claim_number']}
Client: {claim['client_name']}
Property: {claim['property_address']}
Flood Date: {claim['flood_date']}
Flood Source: {claim.get('flood_source') or 'Not specified'}
Water Category: {claim.get('water_category') or 'Not specified'}
Water Class: {claim.get('water_class') or 'Not specified'}
Water Depth: {claim.get('water_depth_in') or 'Not specified'} inches
Insurance Co: {claim.get('insurance_company') or 'Not specified'}
FEMA Flood Zone: {claim.get('flood_zone') or 'Not determined'}

=== CURRENT ROOMS & LINE ITEMS ===
{room_section}

Current Total: ${claim['total_estimate']:.2f}

=== PHOTO ANALYSIS ===
{photo_section}

=== YOUR TASK ===
Based on the photos, claim details, and current line items, provide:

1. **PHOTO FINDINGS** — What damage did you observe in each photo? Be specific (water staining, mold, structural damage, flooring damage, etc.). Note water category/class implied by the damage.

2. **COMPLETE LINE-ITEM ESTIMATE** — Using the pricing reference above, list EVERY repair needed — both mitigation phase and reconstruction phase:
   - Description
   - Quantity + Unit (sq ft, ln ft, ea, hr)
   - Unit Cost (from the pricing reference above)
   - Line Total
   Mark items already documented with ✅, missing items with ➕
   Do NOT omit drying equipment, antimicrobial treatment, or debris removal.

3. **ESTIMATE SUMMARY**
   - Subtotal by room
   - Contractor O&P (20%)
   - Sales tax on materials (~8%)
   - 10% contingency
   - GRAND TOTAL (recommended claim amount)

4. **ADJUSTER NOTES** — Red flags, documentation gaps, items insurance will scrutinize, and whether the current estimate of ${claim['total_estimate']:.2f} is adequate.

Be thorough — this goes directly to the insurance company. Low estimates hurt the homeowner."""

    estimate = call_openrouter([{'role': 'user', 'content': prompt}], model, key)
    return jsonify({
        'ok': True,
        'claim_number': claim['claim_number'],
        'client': claim['client_name'],
        'property': claim['property_address'],
        'current_total': claim['total_estimate'],
        'photos_analyzed': len(photo_analyses),
        'estimate': estimate
    })


@app.route('/willie/api/claims', methods=['POST'])
def willie_create_claim():
    if not willie_auth():
        return jsonify({'error': 'unauthorized'}), 401
    data = request.get_json(silent=True) or {}

    # Fill in demo data for any missing fields
    client_name      = data.get('client_name', 'Demo Client').strip() or 'Demo Client'
    client_phone     = data.get('client_phone', '(555) 000-0000')
    client_email     = data.get('client_email', '')
    property_address = data.get('property_address', '123 Flood St, Liberty, NC 27298').strip() or '123 Flood St, Liberty, NC 27298'
    flood_date       = data.get('flood_date', datetime.datetime.now().strftime('%Y-%m-%d'))
    insurance_company= data.get('insurance_company', '')
    policy_number    = data.get('policy_number', '')
    notes            = data.get('notes', '')

    db = get_db()
    # Get first admin user as default adjuster
    adjuster = db.execute("SELECT id FROM users WHERE role='admin' ORDER BY id LIMIT 1").fetchone()
    adjuster_id = adjuster['id'] if adjuster else 1

    claim_num = gen_claim_number()
    db.execute('''INSERT INTO claims
        (claim_number, adjuster_id, client_name, client_phone, client_email,
         property_address, flood_date, insurance_company, policy_number, notes)
        VALUES (?,?,?,?,?,?,?,?,?,?)''',
        (claim_num, adjuster_id, client_name, client_phone, client_email,
         property_address, flood_date, insurance_company, policy_number, notes))
    db.commit()
    claim = db.execute('SELECT * FROM claims WHERE claim_number=?', (claim_num,)).fetchone()
    return jsonify({
        'ok': True,
        'claim_id': claim['id'],
        'claim_number': claim_num,
        'message': f'Claim {claim_num} created for {client_name} at {property_address}',
        'url': f'https://billy-floods.up.railway.app/claims/{claim["id"]}'
    }), 201

@app.route('/willie/api/claims/<int:claim_id>', methods=['GET'])
def willie_get_claim(claim_id):
    if not willie_auth():
        return jsonify({'error': 'unauthorized'}), 401
    db = get_db()
    claim = db.execute('''
        SELECT c.*, u.name as adjuster_name FROM claims c
        LEFT JOIN users u ON c.adjuster_id=u.id WHERE c.id=?
    ''', (claim_id,)).fetchone()
    if not claim:
        return jsonify({'error': 'Claim not found'}), 404
    rooms = db.execute('SELECT * FROM rooms WHERE claim_id=? AND deleted_at IS NULL ORDER BY id', (claim_id,)).fetchall()
    room_data = []
    for r in rooms:
        items = db.execute('SELECT * FROM line_items WHERE room_id=? AND deleted_at IS NULL ORDER BY id', (r['id'],)).fetchall()
        room_data.append({'room': dict(r), 'line_items': [dict(i) for i in items]})
    return jsonify({'ok': True, 'claim': dict(claim), 'rooms': room_data})

@app.route('/willie/api/claims/by-number/<claim_number>', methods=['DELETE'])
def willie_delete_claim_by_number(claim_number):
    """Delete a claim by claim number (e.g. FC-202604-AF52D2) in one step."""
    if not willie_auth():
        return jsonify({'error': 'unauthorized'}), 401
    db = get_db()
    claim = db.execute('SELECT id, client_name, claim_number FROM claims WHERE claim_number=?', (claim_number,)).fetchone()
    if not claim:
        return jsonify({'ok': False, 'error': f'No claim found with number {claim_number}'}), 404
    db.execute('DELETE FROM claims WHERE id=?', (claim['id'],))
    db.commit()
    # Verify it's gone
    check = db.execute('SELECT id FROM claims WHERE id=?', (claim['id'],)).fetchone()
    if check:
        return jsonify({'ok': False, 'error': 'Delete failed — claim still exists'}), 500
    remaining = db.execute('SELECT COUNT(*) as c FROM claims').fetchone()['c']
    return jsonify({'ok': True, 'message': f'Claim {claim_number} ({claim["client_name"]}) permanently deleted. {remaining} claims remaining.', 'deleted_id': claim['id'], 'deleted_client': claim['client_name'], 'remaining_claims': remaining})


@app.route('/willie/api/claims/<int:claim_id>', methods=['DELETE'])
def willie_delete_claim(claim_id):
    if not willie_auth():
        return jsonify({'error': 'unauthorized'}), 401
    db = get_db()
    claim = db.execute('SELECT id, client_name FROM claims WHERE id=?', (claim_id,)).fetchone()
    if not claim:
        return jsonify({'error': 'Claim not found'}), 404
    # CASCADE deletes rooms, line_items, photos automatically
    db.execute('DELETE FROM claims WHERE id=?', (claim_id,))
    db.commit()
    return jsonify({'ok': True, 'message': f'Claim {claim_id} ({claim["client_name"]}) and all records deleted.'})


@app.route('/willie/api/claims/<int:claim_id>/status', methods=['POST'])
def willie_update_status(claim_id):
    if not willie_auth():
        return jsonify({'error': 'unauthorized'}), 401
    data   = request.get_json(silent=True) or {}
    status = data.get('status', '').strip()
    valid  = ['New', 'In Progress', 'Submitted', 'Closed']
    if status not in valid:
        return jsonify({'error': f'status must be one of: {valid}'}), 400
    db = get_db()
    claim = db.execute('SELECT * FROM claims WHERE id=?', (claim_id,)).fetchone()
    db.execute('UPDATE claims SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?', (status, claim_id))
    db.commit()
    if claim:
        notify_client_status_change(claim, status)
    return jsonify({'ok': True, 'claim_id': claim_id, 'status': status,
                    'message': f'Claim {claim_id} status updated to {status}'})

@app.route('/willie/api/claims/<int:claim_id>/rooms', methods=['POST'])
def willie_add_room(claim_id):
    if not willie_auth():
        return jsonify({'error': 'unauthorized'}), 401
    data = request.get_json(silent=True) or {}
    name = data.get('room_name', data.get('name', '')).strip()
    if not name:
        return jsonify({'error': 'room_name required'}), 400
    db    = get_db()
    claim = db.execute('SELECT id FROM claims WHERE id=?', (claim_id,)).fetchone()
    if not claim:
        return jsonify({'error': 'Claim not found'}), 404
    cur = db.execute('INSERT INTO rooms (claim_id, name) VALUES (?,?)', (claim_id, name))
    db.commit()
    return jsonify({'ok': True, 'room_id': cur.lastrowid, 'room_name': name,
                    'message': f'Room "{name}" added to claim {claim_id}'})

@app.route('/willie/api/claims/<int:claim_id>/rooms/<int:room_id>/items', methods=['POST'])
def willie_add_item(claim_id, room_id):
    if not willie_auth():
        return jsonify({'error': 'unauthorized'}), 401
    data      = request.get_json(silent=True) or {}
    desc      = data.get('description', '').strip()
    qty       = float(data.get('quantity', 1) or 1)
    unit      = data.get('unit', 'ea')
    unit_cost = float(data.get('unit_cost', 0) or 0)
    total     = qty * unit_cost
    if not desc:
        return jsonify({'error': 'description required'}), 400
    db = get_db()
    db.execute('INSERT INTO line_items (room_id,description,quantity,unit,unit_cost,total) VALUES (?,?,?,?,?,?)',
               (room_id, desc, qty, unit, unit_cost, total))
    db.commit()
    recalc_claim(claim_id)
    return jsonify({'ok': True, 'description': desc, 'total': total,
                    'message': f'Added "{desc}" — {qty} {unit} @ ${unit_cost} = ${total:.2f}'})

@app.route('/willie/api/team', methods=['GET'])
def willie_list_team():
    if not willie_auth():
        return jsonify({'error': 'unauthorized'}), 401
    db    = get_db()
    users = db.execute(
        'SELECT id, name, email, role, created_at, '
        '(SELECT COUNT(*) FROM claims WHERE adjuster_id=users.id) as claim_count '
        'FROM users ORDER BY name'
    ).fetchall()
    return jsonify({'ok': True, 'team': [dict(u) for u in users], 'count': len(users)})

@app.route('/willie/api/team', methods=['POST'])
def willie_add_team_member():
    if not willie_auth():
        return jsonify({'error': 'unauthorized'}), 401
    data  = request.get_json(silent=True) or {}
    name  = data.get('name', '').strip()
    email = data.get('email', '').strip().lower()
    pw    = data.get('password', '').strip()
    role  = data.get('role', 'adjuster').strip().lower()
    if role not in ('admin', 'adjuster'):
        role = 'adjuster'
    if not name:
        return jsonify({'error': 'name is required'}), 400
    if not email:
        return jsonify({'error': 'email is required'}), 400
    if not pw:
        pw = secrets.token_urlsafe(10)
    ok, err = _validate_password(pw)
    if not ok:
        return jsonify({'error': err}), 400
    db = get_db()
    try:
        db.execute('INSERT INTO users (name, email, password, role) VALUES (?,?,?,?)',
                   (name, email, hash_pw(pw), role))
        db.commit()
        user = db.execute('SELECT id, name, email, role FROM users WHERE email=?', (email,)).fetchone()
        return jsonify({
            'ok': True,
            'user_id':  user['id'],
            'name':     user['name'],
            'email':    user['email'],
            'role':     user['role'],
            'password': pw,
            'message':  f'Team member {name} added as {role}. Login: {email} / {pw}'
        }), 201
    except sqlite3.IntegrityError:
        return jsonify({'error': f'Email {email} already exists'}), 409

@app.route('/willie/api/dashboard', methods=['GET'])
def willie_dashboard():
    if not willie_auth():
        return jsonify({'error': 'unauthorized'}), 401
    db = get_db()
    claims = db.execute('SELECT status, total_estimate FROM claims').fetchall()
    stats = {
        'total':       len(claims),
        'new':         sum(1 for c in claims if c['status'] == 'New'),
        'in_progress': sum(1 for c in claims if c['status'] == 'In Progress'),
        'submitted':   sum(1 for c in claims if c['status'] == 'Submitted'),
        'closed':      sum(1 for c in claims if c['status'] == 'Closed'),
        'pipeline_value': sum(c['total_estimate'] for c in claims if c['status'] != 'Closed'),
    }
    recent = db.execute('''
        SELECT c.id, c.claim_number, c.client_name, c.status, c.total_estimate
        FROM claims c ORDER BY c.created_at DESC LIMIT 5
    ''').fetchall()
    return jsonify({'ok': True, 'stats': stats, 'recent_claims': [dict(r) for r in recent]})

@app.route('/willie/api/claims/<int:claim_id>/rooms', methods=['GET'])
def willie_list_rooms(claim_id):
    if not willie_auth(): return jsonify({'error': 'unauthorized'}), 401
    db = get_db()
    claim = db.execute('SELECT id FROM claims WHERE id=?', (claim_id,)).fetchone()
    if not claim: return jsonify({'error': 'Claim not found'}), 404
    rooms = db.execute('SELECT id, name, subtotal FROM rooms WHERE claim_id=? ORDER BY id', (claim_id,)).fetchall()
    rooms_out = []
    for r in rooms:
        items = db.execute('SELECT id, room_id, description, quantity, unit, unit_cost, total FROM line_items WHERE room_id=? ORDER BY id', (r['id'],)).fetchall()
        rooms_out.append({'id': r['id'], 'name': r['name'], 'subtotal': r['subtotal'], 'line_items': [dict(i) for i in items]})
    return jsonify({'ok': True, 'claim_id': claim_id, 'rooms': rooms_out, 'count': len(rooms_out)})


@app.route('/willie/api/claims/<int:claim_id>/rooms/<int:room_id>', methods=['DELETE'])
def willie_delete_room(claim_id, room_id):
    if not willie_auth(): return jsonify({'error': 'unauthorized'}), 401
    db = get_db()
    room = db.execute('SELECT id, name FROM rooms WHERE id=? AND claim_id=?', (room_id, claim_id)).fetchone()
    if not room: return jsonify({'error': 'Room not found'}), 404
    db.execute('DELETE FROM rooms WHERE id=?', (room_id,))
    db.commit()
    return jsonify({'ok': True, 'message': f'Room "{room["name"]}" and all its line items deleted.'})


@app.route('/willie/api/line-items/<int:item_id>', methods=['DELETE'])
def willie_delete_line_item(item_id):
    if not willie_auth(): return jsonify({'error': 'unauthorized'}), 401
    db = get_db()
    item = db.execute('SELECT id, description FROM line_items WHERE id=?', (item_id,)).fetchone()
    if not item: return jsonify({'error': 'Line item not found'}), 404
    db.execute('DELETE FROM line_items WHERE id=?', (item_id,))
    db.commit()
    return jsonify({'ok': True, 'message': f'Line item "{item["description"]}" deleted.'})


@app.route('/willie/api/team/<int:user_id>', methods=['PUT', 'PATCH'])
def willie_edit_team_member(user_id):
    if not willie_auth():
        return jsonify({'error': 'unauthorized'}), 401
    data  = request.get_json(silent=True) or {}
    name  = data.get('name', '').strip()
    email = data.get('email', '').strip().lower()
    pw    = data.get('password', '').strip()
    role  = data.get('role', '').strip().lower()
    if not email:
        return jsonify({'error': 'email is required'}), 400
    if role and role not in ('admin', 'adjuster'):
        return jsonify({'error': 'role must be admin or adjuster'}), 400
    if pw:
        ok, err = _validate_password(pw)
        if not ok:
            return jsonify({'error': err}), 400
    db = get_db()
    user = db.execute('SELECT id FROM users WHERE id=?', (user_id,)).fetchone()
    if not user:
        return jsonify({'error': 'Team member not found'}), 404
    try:
        if pw and role:
            db.execute('UPDATE users SET name=?, email=?, password=?, role=? WHERE id=?',
                       (name, email, hash_pw(pw), role, user_id))
        elif pw:
            db.execute('UPDATE users SET name=?, email=?, password=? WHERE id=?',
                       (name, email, hash_pw(pw), user_id))
        elif role:
            db.execute('UPDATE users SET name=?, email=?, role=? WHERE id=?',
                       (name, email, role, user_id))
        else:
            db.execute('UPDATE users SET name=?, email=? WHERE id=?',
                       (name, email, user_id))
        db.commit()
        updated = db.execute('SELECT id, name, email, role FROM users WHERE id=?', (user_id,)).fetchone()
        return jsonify({'ok': True, 'user': dict(updated), 'message': f'Team member {name} updated.'})
    except sqlite3.IntegrityError:
        return jsonify({'error': f'Email {email} already exists'}), 409


@app.route('/willie/api/team/<int:user_id>', methods=['DELETE'])
def willie_delete_team_member(user_id):
    if not willie_auth(): return jsonify({'error': 'unauthorized'}), 401
    db = get_db()
    user = db.execute('SELECT id, name FROM users WHERE id=?', (user_id,)).fetchone()
    if not user: return jsonify({'error': 'Team member not found'}), 404
    db.execute('DELETE FROM users WHERE id=?', (user_id,))
    db.commit()
    return jsonify({'ok': True, 'message': f'Team member "{user["name"]}" deleted.'})


@app.route('/willie/api/claims/<int:claim_id>/report', methods=['GET'])
def willie_get_report(claim_id):
    if not willie_auth(): return jsonify({'error': 'unauthorized'}), 401
    db = get_db()
    claim = db.execute('SELECT * FROM claims WHERE id=?', (claim_id,)).fetchone()
    if not claim: return jsonify({'error': 'Claim not found'}), 404
    rooms = db.execute('SELECT * FROM rooms WHERE claim_id=? AND deleted_at IS NULL ORDER BY id', (claim_id,)).fetchall()
    report = dict(claim)
    report['rooms'] = []
    for r in rooms:
        items = db.execute('SELECT * FROM line_items WHERE room_id=? AND deleted_at IS NULL ORDER BY id', (r['id'],)).fetchall()
        room_data = dict(r)
        room_data['line_items'] = [dict(i) for i in items]
        report['rooms'].append(room_data)
    return jsonify({'ok': True, 'report': report})


@app.route('/willie/api/settings', methods=['GET'])
def willie_get_settings():
    if not willie_auth(): return jsonify({'error': 'unauthorized'}), 401
    db = get_db()
    settings = db.execute('SELECT key, value FROM settings').fetchall()
    return jsonify({'ok': True, 'settings': {s['key']: s['value'] for s in settings}})


@app.route('/willie/api/settings', methods=['POST'])
def willie_update_settings():
    if not willie_auth(): return jsonify({'error': 'unauthorized'}), 401
    data = request.get_json(silent=True) or {}
    db = get_db()
    # Accept both 'ai_model' and legacy 'openrouter_model' alias
    allowed = {'openrouter_api_key', 'ai_model', 'openrouter_model', 'willie_agent_key', 'willie_agent_id'}
    updated = []
    for key, value in data.items():
        if key in allowed:
            store_key = 'ai_model' if key == 'openrouter_model' else key
            db.execute('INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (store_key, value))
            updated.append(store_key)
    db.commit()
    return jsonify({'ok': True, 'updated': updated})


# ── Admin: Train Aquila (Brain Editor) — Local DB Storage ─────────────────────

@app.route('/admin/willie/brain', methods=['GET'])
@login_required
@admin_required
def willie_brain_get():
    """Fetch Aquila's brain files from local database settings."""
    db = get_db()
    keys = ['brain_identity_md', 'brain_soul_md', 'brain_memory_md', 'brain_system_prompt', 'brain_photo_prompt']
    result = {}
    for k in keys:
        row = db.execute('SELECT value FROM settings WHERE key=?', (k,)).fetchone()
        result[k] = row['value'] if row else ''
    return jsonify(result)


@app.route('/admin/willie/brain/update', methods=['POST'])
@login_required
@admin_required
@csrf_required
def willie_brain_update():
    """Save Aquila's brain files to local database settings."""
    brain_keys = ['brain_identity_md', 'brain_soul_md', 'brain_memory_md', 'brain_system_prompt']
    for key in brain_keys:
        val = request.form.get(key, '')
        set_setting(key, val)
    # Photo analysis training prompt
    photo_prompt = request.form.get('brain_photo_prompt', '')
    if photo_prompt:
        set_setting('brain_photo_prompt', photo_prompt)
    return jsonify({'ok': True, 'message': 'Brain files saved!', 'updated': brain_keys})


# ── Admin: Chat Bubble Settings ───────────────────────────────────────────────

@app.route('/admin/settings/data')
@login_required
@admin_required
def settings_data():
    """Return all settings as JSON (for AJAX loading of brain files etc.)."""
    db = get_db()
    rows = db.execute('SELECT key, value FROM settings').fetchall()
    return jsonify({r['key']: r['value'] for r in rows})


@app.route('/admin/settings/save', methods=['POST'])
@login_required
@admin_required
@csrf_required
def save_setting():
    """Generic single-setting save endpoint."""
    key = request.form.get('setting_key', '').strip()
    value = request.form.get('setting_value', '').strip()
    if not key:
        flash('Setting key missing.', 'error')
        return redirect(request.referrer or url_for('settings'))
    set_setting(key, value)
    flash(f'Setting "{key}" saved.', 'success')
    return redirect(request.referrer or url_for('settings'))


@app.route('/admin/settings/chat-bubble', methods=['POST'])
@login_required
@admin_required
@csrf_required
def save_chat_bubble():
    """Save chat bubble appearance settings."""
    set_setting('bubble_bot_name', request.form.get('bubble_bot_name', 'Aquila').strip())
    set_setting('bubble_greeting', request.form.get('bubble_greeting', '').strip())
    set_setting('bubble_emoji_icon', request.form.get('bubble_emoji_icon', '🌊').strip())

    # Handle icon upload
    icon_file = request.files.get('bubble_icon_upload')
    if icon_file and icon_file.filename:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(icon_file.read()))
        img = img.convert('RGBA')
        img.thumbnail((64, 64), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        buf.seek(0)
        import base64
        b64 = base64.b64encode(buf.read()).decode()
        set_setting('bubble_icon_data', f'data:image/png;base64,{b64}')
        set_setting('bubble_icon_type', 'upload')
    else:
        # If no upload, check if emoji was selected
        emoji = request.form.get('bubble_emoji_icon', '').strip()
        if emoji and not get_setting('bubble_icon_data'):
            set_setting('bubble_icon_type', 'emoji')
            set_setting('bubble_emoji_icon', emoji)

    flash('Chat bubble settings saved!', 'success')
    return redirect(url_for('settings'))


@app.route('/willie/api/actions/sync', methods=['POST'])
def willie_sync_actions():
    """Push all FloodClaims actions to Willie's widget so he can use them correctly.
    Requires willie_agent_key to be set in settings (Willie's own widget API key).
    Auth: Willie token OR admin session."""
    if not session.get('user_id') and not willie_auth():
        return jsonify({'error': 'unauthorized'}), 401

    WIDGET_BASE     = 'https://ai-agent-widget-production.up.railway.app'
    FLOOD_BASE      = 'https://billy-floods.up.railway.app'
    WILLIE_AGENT_ID = get_setting('willie_agent_id', 'F5J8yYT6a6GrppjviN6p8w')
    willie_key      = get_setting('willie_agent_key', '')
    flood_token     = get_willie_token()

    if not willie_key:
        return jsonify({'ok': False,
                        'error': 'aquila_agent_key not set. Go to Settings and paste Aquila\'s widget API key.'}), 400

    # Full correct action definitions — {param} placeholders get substituted by the widget engine
    ACTIONS = [
        {
            'name':        'get_dashboard',
            'description': 'Get FloodClaims Pro dashboard stats: total claims, pipeline value, status breakdown, recent claims.',
            'method':      'GET',
            'url':         f'{FLOOD_BASE}/willie/api/dashboard',
            'headers':     {'Authorization': f'Bearer {flood_token}'},
            'body':        {},
        },
        {
            'name':        'list_claims',
            'description': 'List all flood damage claims. Use this to find a claim ID from a client name or claim number.',
            'method':      'GET',
            'url':         f'{FLOOD_BASE}/willie/api/claims',
            'headers':     {'Authorization': f'Bearer {flood_token}'},
            'body':        {},
        },
        {
            'name':        'lookup_claim',
            'description': 'Look up a specific claim by claim_number (e.g. FC-202604-XXXX) or partial client name. Always do this before adding rooms/items to find the correct claim ID.',
            'method':      'GET',
            'url':         f'{FLOOD_BASE}/willie/api/claims/lookup?claim_number={{claim_number}}',
            'headers':     {'Authorization': f'Bearer {flood_token}'},
            'body':        {},
        },
        {
            'name':        'get_claim',
            'description': 'Get full details of a claim including rooms and line items. Requires numeric claim_id.',
            'method':      'GET',
            'url':         f'{FLOOD_BASE}/willie/api/claims/{{claim_id}}',
            'headers':     {'Authorization': f'Bearer {flood_token}'},
            'body':        {},
        },
        {
            'name':        'create_claim',
            'description': 'Create a new flood damage claim. Requires client_name, property_address, flood_date.',
            'method':      'POST',
            'url':         f'{FLOOD_BASE}/willie/api/claims',
            'headers':     {'Authorization': f'Bearer {flood_token}'},
            'body':        {'client_name': '{client_name}', 'property_address': '{property_address}',
                            'flood_date': '{flood_date}', 'insurance_company': '{insurance_company}'},
        },
        {
            'name':        'update_claim_status',
            'description': 'Update the status of a claim. Status must be one of: New, In Progress, Submitted, Closed.',
            'method':      'POST',
            'url':         f'{FLOOD_BASE}/willie/api/claims/{{claim_id}}/status',
            'headers':     {'Authorization': f'Bearer {flood_token}'},
            'body':        {'status': '{status}'},
        },
        {
            'name':        'add_room',
            'description': 'Add a room to a claim. ALWAYS call lookup_claim first to get the numeric claim_id. Requires claim_id (number) and room_name.',
            'method':      'POST',
            'url':         f'{FLOOD_BASE}/willie/api/claims/{{claim_id}}/rooms',
            'headers':     {'Authorization': f'Bearer {flood_token}'},
            'body':        {'room_name': '{room_name}'},
        },
        {
            'name':        'list_rooms',
            'description': 'List all rooms and line items for a claim. Requires numeric claim_id.',
            'method':      'GET',
            'url':         f'{FLOOD_BASE}/willie/api/claims/{{claim_id}}/rooms',
            'headers':     {'Authorization': f'Bearer {flood_token}'},
            'body':        {},
        },
        {
            'name':        'add_line_item',
            'description': 'Add a line item (damage item) to a room. ALWAYS call list_rooms first to get the numeric room_id. Requires claim_id, room_id, description, quantity, unit, unit_cost.',
            'method':      'POST',
            'url':         f'{FLOOD_BASE}/willie/api/claims/{{claim_id}}/rooms/{{room_id}}/items',
            'headers':     {'Authorization': f'Bearer {flood_token}'},
            'body':        {'description': '{description}', 'quantity': '{quantity}',
                            'unit': '{unit}', 'unit_cost': '{unit_cost}'},
        },
        {
            'name':        'delete_room',
            'description': 'Delete a room and all its line items from a claim.',
            'method':      'DELETE',
            'url':         f'{FLOOD_BASE}/willie/api/claims/{{claim_id}}/rooms/{{room_id}}',
            'headers':     {'Authorization': f'Bearer {flood_token}'},
            'body':        {},
        },
        {
            'name':        'delete_line_item',
            'description': 'Delete a single line item by its numeric item_id.',
            'method':      'DELETE',
            'url':         f'{FLOOD_BASE}/willie/api/line-items/{{item_id}}',
            'headers':     {'Authorization': f'Bearer {flood_token}'},
            'body':        {},
        },
        {
            'name':        'get_report',
            'description': 'Get a full damage report for a claim including all rooms and line items.',
            'method':      'GET',
            'url':         f'{FLOOD_BASE}/willie/api/claims/{{claim_id}}/report',
            'headers':     {'Authorization': f'Bearer {flood_token}'},
            'body':        {},
        },
        {
            'name':        'generate_estimate',
            'description': 'Trigger AI estimate generation for a claim. Returns a job_id to poll for results.',
            'method':      'POST',
            'url':         f'{FLOOD_BASE}/willie/api/claims/{{claim_id}}/estimate',
            'headers':     {'Authorization': f'Bearer {flood_token}'},
            'body':        {},
        },
        {
            'name':        'list_team',
            'description': 'List all adjusters and team members.',
            'method':      'GET',
            'url':         f'{FLOOD_BASE}/willie/api/team',
            'headers':     {'Authorization': f'Bearer {flood_token}'},
            'body':        {},
        },
        {
            'name':        'add_team_member',
            'description': 'Add a new adjuster or team member. Requires name, email, password, role (adjuster or admin).',
            'method':      'POST',
            'url':         f'{FLOOD_BASE}/willie/api/team',
            'headers':     {'Authorization': f'Bearer {flood_token}'},
            'body':        {'name': '{name}', 'email': '{email}',
                            'password': '{password}', 'role': '{role}'},
        },
        {
            'name':        'delete_team_member',
            'description': 'Remove a team member by their numeric user_id.',
            'method':      'DELETE',
            'url':         f'{FLOOD_BASE}/willie/api/team/{{user_id}}',
            'headers':     {'Authorization': f'Bearer {flood_token}'},
            'body':        {},
        },
        {
            'name':        'get_settings',
            'description': 'Get current FloodClaims Pro app settings (AI model, etc.)',
            'method':      'GET',
            'url':         f'{FLOOD_BASE}/willie/api/settings',
            'headers':     {'Authorization': f'Bearer {flood_token}'},
            'body':        {},
        },
        {
            'name':        'schedule_inspection',
            'description': 'Schedule an inspection for a claim. Requires claim_id, date (YYYY-MM-DD), time (HH:MM). Optional: adjuster_id, notes.',
            'method':      'POST',
            'url':         f'{FLOOD_BASE}/willie/api/claims/{{claim_id}}/schedule',
            'headers':     {'Authorization': f'Bearer {flood_token}'},
            'body':        {{'date': '{{date}}', 'time': '{{time}}', 'notes': '{{notes}}'}},
        },
        {
            'name':        'check_compliance',
            'description': 'Check NFIP compliance score for a claim. Returns percent complete, grade, and list of missing items. Use before submitting.',
            'method':      'GET',
            'url':         f'{FLOOD_BASE}/willie/api/claims/{{claim_id}}/compliance',
            'headers':     {'Authorization': f'Bearer {flood_token}'},
            'body':        {{}},
        },
        {
            'name':        'fema_flood_zone_lookup',
            'description': 'Look up the FEMA flood zone for a claim property address. Updates the claim automatically.',
            'method':      'POST',
            'url':         f'{FLOOD_BASE}/willie/api/claims/{{claim_id}}/fema-lookup',
            'headers':     {'Authorization': f'Bearer {flood_token}'},
            'body':        {{}},
        },
        {
            'name':        'notify_client',
            'description': 'Send a notification to the client. Requires claim_id and message. Optional: method (email, sms, both).',
            'method':      'POST',
            'url':         f'{FLOOD_BASE}/willie/api/claims/{{claim_id}}/notify',
            'headers':     {'Authorization': f'Bearer {flood_token}'},
            'body':        {{'message': '{{message}}', 'method': '{{method}}'}},
        },
        {
            'name':        'move_pipeline',
            'description': 'Move a claim to a new pipeline stage. Status must be: New, In Progress, Submitted, or Closed. Also notifies client.',
            'method':      'POST',
            'url':         f'{FLOOD_BASE}/willie/api/claims/{{claim_id}}/move-pipeline',
            'headers':     {'Authorization': f'Bearer {flood_token}'},
            'body':        {{'status': '{{status}}'}},
        },
        {
            'name':        'get_analytics',
            'description': 'Get business analytics: total claims, pipeline value, closed revenue, average cycle time, status breakdown.',
            'method':      'GET',
            'url':         f'{FLOOD_BASE}/willie/api/analytics',
            'headers':     {'Authorization': f'Bearer {flood_token}'},
            'body':        {{}},
        },
        {
            'name':        'get_schedule',
            'description': 'Get all upcoming scheduled inspections from today onward.',
            'method':      'GET',
            'url':         f'{FLOOD_BASE}/willie/api/schedule',
            'headers':     {'Authorization': f'Bearer {flood_token}'},
            'body':        {{}},
        },
        {
            'name':        'analyze_photos',
            'description': 'Run AI vision analysis on all claim photos. Returns water category, damage summary, suggested rooms and line items. Use update_claim to apply the recommendations.',
            'method':      'POST',
            'url':         f'{FLOOD_BASE}/willie/api/claims/{{claim_id}}/analyze',
            'headers':     {'Authorization': f'Bearer {flood_token}'},
            'body':        {{}},
        },
        {
            'name':        'update_claim_fields',
            'description': 'Update multiple fields on a claim at once. Can set water_category, water_class, flood_source, cause_of_loss, notes, priority, coverage_building, coverage_contents, deductible, policy_number, insurance_company, etc.',
            'method':      'POST',
            'url':         f'{FLOOD_BASE}/willie/api/claims/{{claim_id}}/update',
            'headers':     {'Authorization': f'Bearer {flood_token}'},
            'body':        {{'water_category': '{{water_category}}', 'water_class': '{{water_class}}', 'notes': '{{notes}}', 'priority': '{{priority}}'}},
        },
    ]

    pushed = []
    errors = []
    for action in ACTIONS:
        payload = {
            'name':        action['name'],
            'description': action['description'],
            'method':      action['method'],
            'url':         action['url'],
            'headers':     action['headers'],
            'body':        action['body'],
        }
        try:
            r = _req.post(
                f'{WIDGET_BASE}/agent/{WILLIE_AGENT_ID}/actions/api',
                headers={'Authorization': f'Bearer {willie_key}',
                         'Content-Type': 'application/json'},
                json=payload, timeout=15)
            d = r.json()
            if d.get('ok'):
                pushed.append(action['name'])
            else:
                errors.append({'action': action['name'], 'error': d.get('error', str(d))})
        except Exception as e:
            errors.append({'action': action['name'], 'error': str(e)})

    return jsonify({
        'ok':     len(errors) == 0,
        'pushed': pushed,
        'errors': errors,
        'total':  len(ACTIONS),
        'message': f'{len(pushed)}/{len(ACTIONS)} actions synced to Willie'
    })


@app.route('/willie/api/claims/<int:claim_id>/update', methods=['POST'])
@app.route('/willie/api/claims/<int:claim_id>', methods=['PATCH'])
def willie_update_claim(claim_id):
    """Update any field(s) on a claim. Accepts a JSON body with any claim columns.
    Willie uses this to fill in form fields after analyzing photos or reviewing the claim."""
    if not willie_auth(): return jsonify({'error': 'unauthorized'}), 401
    db = get_db()
    claim = db.execute('SELECT * FROM claims WHERE id=?', (claim_id,)).fetchone()
    if not claim: return jsonify({'error': 'Claim not found'}), 404

    data = request.get_json(silent=True) or {}

    # Allowed fields Willie can update
    UPDATABLE = {
        'client_name', 'client_phone', 'client_phone_alt', 'client_email',
        'property_address', 'property_type', 'property_sqft', 'year_built',
        'num_floors', 'flood_date', 'flood_source', 'water_category', 'water_class',
        'water_depth_in', 'date_water_removed', 'inspection_date',
        'insurance_company', 'policy_number', 'policy_type',
        'coverage_building', 'coverage_contents', 'deductible',
        'mortgage_company', 'mortgage_loan_number', 'cause_of_loss',
        'priority', 'notes',
    }

    updates = {k: v for k, v in data.items() if k in UPDATABLE}
    if not updates:
        return jsonify({'error': 'No valid fields provided. Updatable fields: ' + ', '.join(sorted(UPDATABLE))}), 400

    set_clause = ', '.join(f'{k}=?' for k in updates)
    values     = list(updates.values()) + [claim_id]
    db.execute(f'UPDATE claims SET {set_clause}, updated_at=CURRENT_TIMESTAMP WHERE id=?', values)
    db.commit()

    updated_claim = dict(db.execute('SELECT * FROM claims WHERE id=?', (claim_id,)).fetchone())
    return jsonify({
        'ok':      True,
        'updated': list(updates.keys()),
        'message': f'Updated {len(updates)} field(s) on claim {claim["claim_number"]}',
        'claim':   {k: updated_claim.get(k) for k in updates},
    })



# ── Client Feedback Studio ────────────────────────────────────────────────────

FEEDBACK_SYSTEM_PROMPT = """You are a Client Feedback Concierge for FloodClaims Pro, a flood insurance claims management platform owned by Liberty Emporium (Jay Alexander).

Your job is to have a natural conversation with a client to understand what they want in their custom app. You are NOT a generic assistant — you are gathering specific, actionable product requirements.

## How to conduct the conversation:

1. START by welcoming them and asking what they'd like to build or improve.
2. LISTEN carefully to everything they say — every detail matters.
3. ASK SMART FOLLOW-UP QUESTIONS based on their responses:
   - If they mention a feature, ask about specifics (who uses it, what data it needs, what the workflow looks like)
   - If they mention a problem, ask about their current process and what would make it better
   - If they're vague, give them 2-3 options to choose from based on what flood claims businesses typically need
   - If they mention integrations, ask which systems they currently use
4. DO NOT ask boring survey questions. Have a real conversation.
5. Every few messages, briefly summarize what you've understood so far so they can correct you.

## Key areas to explore (when relevant):
- **Claims management**: How they want to create, track, and process claims
- **Photo/AI analysis**: What kind of damage assessment they need
- **Reporting**: What reports they need and who sees them
- **User roles**: Who needs access (adjusters, managers, clients, contractors)
- **Payments/Billing**: How they charge and get paid
- **Integrations**: What other tools they use (insurance company APIs, accounting, etc.)
- **Mobile**: Do they need mobile access or specific mobile features
- **Client portal**: How their customers interact with them

## When the conversation winds down:
Summarize everything they've told you into a structured format:
- **WHO** they are (their business type, size)
- **WHAT** features they want
- **WHY** they need each feature (the problem it solves)
- **PRIORITIES** (what's most important vs nice-to-have)
- **CONCERNS** (any worries or constraints mentioned)

Keep responses warm, professional, and conversational. You are representing Jay's company.
"""

@app.route('/admin/feedback')
@login_required
def feedback_studio():
    """Client Feedback Studio — AI-powered requirement gathering."""
    if session.get('role') not in ('admin', 'manager'):
        abort(403)
    db = get_db()
    convs = db.execute(
        'SELECT * FROM feedback_conversations ORDER BY updated_at DESC LIMIT 100'
    ).fetchall()
    return render_template('feedback_studio.html', conversations=convs)

@app.route('/admin/feedback/conversations/list')
@login_required
def feedback_list_conversations():
    db = get_db()
    convs = db.execute(
        'SELECT id, title, client_name, client_email, created_at FROM feedback_conversations ORDER BY updated_at DESC LIMIT 100'
    ).fetchall()
    return jsonify([dict(c) for c in convs])

@app.route('/admin/feedback/conversations', methods=['POST'])
@login_required
def feedback_new_conversation():
    if session.get('role') not in ('admin', 'manager'):
        abort(403)
    db = get_db()
    cur = db.execute(
        'INSERT INTO feedback_conversations (user_id, client_name, client_email) VALUES (?,?,?)',
        (session['user_id'], '', '')
    )
    db.commit()
    return jsonify({'id': cur.lastrowid, 'title': 'Feedback Session'})

@app.route('/admin/feedback/conversations/<int:conv_id>')
@login_required
def feedback_get_conversation(conv_id):
    db = get_db()
    conv = db.execute('SELECT * FROM feedback_conversations WHERE id=?', (conv_id,)).fetchone()
    if not conv:
        return jsonify({'error': 'not found'}), 404
    msgs = db.execute(
        'SELECT role,content,created_at FROM feedback_messages WHERE conversation_id=? ORDER BY id',
        (conv_id,)
    ).fetchall()
    return jsonify({'conversation': dict(conv), 'messages': [dict(m) for m in msgs]})

@app.route('/admin/feedback/conversations/<int:conv_id>', methods=['DELETE'])
@login_required
def feedback_delete_conversation(conv_id):
    db = get_db()
    db.execute('DELETE FROM feedback_messages WHERE conversation_id=?', (conv_id,))
    db.execute('DELETE FROM feedback_conversations WHERE id=?', (conv_id,))
    db.commit()
    return jsonify({'ok': True})

@app.route('/admin/feedback/conversations/<int:conv_id>/meta', methods=['POST'])
@login_required
def feedback_update_meta(conv_id):
    """Update client name, email, title for a feedback conversation."""
    if session.get('role') not in ('admin', 'manager'):
        abort(403)
    data = request.get_json(silent=True) or {}
    db = get_db()
    if 'client_name' in data:
        db.execute('UPDATE feedback_conversations SET client_name=?, updated_at=CURRENT_TIMESTAMP WHERE id=?',
                   (data['client_name'], conv_id))
    if 'client_email' in data:
        db.execute('UPDATE feedback_conversations SET client_email=?, updated_at=CURRENT_TIMESTAMP WHERE id=?',
                   (data['client_email'], conv_id))
    if 'title' in data:
        db.execute('UPDATE feedback_conversations SET title=?, updated_at=CURRENT_TIMESTAMP WHERE id=?',
                   (data['title'], conv_id))
    if 'summary' in data:
        db.execute('UPDATE feedback_conversations SET summary=?, updated_at=CURRENT_TIMESTAMP WHERE id=?',
                   (data['summary'], conv_id))
    db.commit()
    return jsonify({'ok': True})

@app.route('/admin/feedback/conversations/<int:conv_id>/messages', methods=['POST'])
@login_required
def feedback_save_message(conv_id):
    db = get_db()
    data = request.get_json(silent=True) or {}
    role = data.get('role', 'user')
    content = data.get('content', '').strip()
    if not content:
        return jsonify({'error': 'content required'}), 400
    db.execute('INSERT INTO feedback_messages (conversation_id, role, content) VALUES (?,?,?)',
               (conv_id, role, content))
    if role == 'user':
        title = content[:60] + ('...' if len(content) > 60 else '')
        db.execute('UPDATE feedback_conversations SET title=?, updated_at=CURRENT_TIMESTAMP WHERE id=?',
                   (title, conv_id))
    else:
        db.execute('UPDATE feedback_conversations SET updated_at=CURRENT_TIMESTAMP WHERE id=?', (conv_id,))
    db.commit()
    return jsonify({'ok': True})

@app.route('/admin/feedback/chat', methods=['POST'])
@login_required
def feedback_chat():
    """AI chat endpoint for feedback studio."""
    data = request.get_json(silent=True) or {}
    message = data.get('message', '').strip()
    history = data.get('history', [])
    conv_id = data.get('conversation_id')

    if not message:
        return jsonify({'error': 'message required'}), 400

    # Build messages for OpenRouter
    messages = [{'role': 'system', 'content': FEEDBACK_SYSTEM_PROMPT}]

    # Add conversation history
    for msg in history:
        role = msg.get('role', 'user')
        if role in ('user', 'assistant'):
            messages.append({'role': role, 'content': msg.get('content', '')})

    # Add current message
    messages.append({'role': 'user', 'content': message})

    # Call OpenRouter
    api_key = os.environ.get('OPENROUTER_API_KEY', '') or get_setting('openrouter_api_key')
    if not api_key:
        return jsonify({'error': 'OpenRouter API key not configured. Please contact Jay to set it up.'}), 500

    try:
        response = _req.post(
            'https://openrouter.ai/api/v1/chat/completions',
            headers={
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json',
            },
            json={
                'model': 'openrouter/auto',
                'messages': messages,
                'max_tokens': 800,
            },
            timeout=30,
        )
        result = response.json()
        reply = result['choices'][0]['message']['content']
    except Exception as e:
        return jsonify({'error': f'AI service unavailable: {str(e)}'}), 500

    return jsonify({'reply': reply})

@app.route('/admin/feedback/report/<int:conv_id>')
@login_required
def feedback_report(conv_id):
    """Generate a structured report from a feedback conversation."""
    if session.get('role') not in ('admin', 'manager'):
        abort(403)
    db = get_db()
    conv = db.execute('SELECT * FROM feedback_conversations WHERE id=?', (conv_id,)).fetchone()
    if not conv:
        abort(404)
    msgs = db.execute(
        'SELECT role,content,created_at FROM feedback_messages WHERE conversation_id=? ORDER BY id',
        (conv_id,)
    ).fetchall()
    conversation_text = '\n\n'.join([f"[{m['role']}]: {m['content']}" for m in msgs])

    api_key = os.environ.get('OPENROUTER_API_KEY', '') or get_setting('openrouter_api_key')
    if not api_key:
        return jsonify({'error': 'OpenRouter API key not configured.'}), 500

    report_prompt = f"""Based on this client feedback conversation, create a structured requirements document.

CONVERSATION:
{conversation_text}

OUTPUT FORMAT:
# Client Requirements Report
**Client:** {conv['client_name'] or 'Not specified'} ({conv['client_email'] or 'No email'})
**Date:** {conv['created_at']}

## Who They Are
[Describe their business, role, and size]

## What They Want
[List all specific features and capabilities requested]

## Why They Need It
[For each major feature, explain the problem it solves]

## Priorities
- **Must Have:** [Critical features]
- **Nice to Have:** [Would be good but not essential]
- **Future:** [Can wait]

## Concerns & Constraints
[Any worries, limitations, or special requirements mentioned]

## Recommended Next Steps
[What Jay should do first based on this feedback]

## Raw Notes
[Any other useful details from the conversation]
"""

    try:
        response = _req.post(
            'https://openrouter.ai/api/v1/chat/completions',
            headers={
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json',
            },
            json={
                'model': 'openrouter/auto',
                'messages': [{'role': 'user', 'content': report_prompt}],
                'max_tokens': 1500,
            },
            timeout=30,
        )
        result = response.json()
        report = result['choices'][0]['message']['content']
    except Exception as e:
        return jsonify({'error': f'AI service unavailable: {str(e)}'}), 500

    # Save report as summary
    db.execute('UPDATE feedback_conversations SET summary=? WHERE id=?', (report, conv_id))
    db.commit()

    return jsonify({'report': report, 'conversation': dict(conv)})


# ── Willie: Schedule Inspection ────────────────────────────────────────────────
@app.route('/willie/api/claims/<int:claim_id>/schedule', methods=['POST'])
def willie_schedule_inspection(claim_id):
    if not willie_auth(): return jsonify({'error': 'unauthorized'}), 401
    db    = get_db()
    claim = db.execute('SELECT * FROM claims WHERE id=?', (claim_id,)).fetchone()
    if not claim: return jsonify({'error': 'Claim not found'}), 404
    data      = request.get_json(silent=True) or {}
    slot_date = data.get('date', '').strip()
    slot_time = data.get('time', '09:00').strip()
    adj_id    = data.get('adjuster_id') or claim['adjuster_id']
    notes     = data.get('notes', '').strip()
    if not slot_date:
        return jsonify({'error': 'date is required (YYYY-MM-DD)'}), 400
    db.execute(
        'INSERT INTO inspection_slots (claim_id, adjuster_id, slot_date, slot_time, notes) VALUES (?,?,?,?,?)',
        (claim_id, adj_id, slot_date, slot_time, notes)
    )
    db.execute('UPDATE claims SET sched_date=?, sched_time=?, inspection_date=?, updated_at=CURRENT_TIMESTAMP WHERE id=?',
               (slot_date, slot_time, slot_date, claim_id))
    db.commit()
    _log_activity(claim_id, f'Inspection scheduled for {slot_date} at {slot_time} (by Aquila)', 'Aquila')
    return jsonify({'ok': True, 'claim_id': claim_id, 'date': slot_date, 'time': slot_time,
                    'message': f'Inspection scheduled for {claim["claim_number"]} on {slot_date} at {slot_time}'})


# ── Willie: Compliance Score ────────────────────────────────────────────────
@app.route('/willie/api/claims/<int:claim_id>/compliance', methods=['GET'])
def willie_compliance_check(claim_id):
    if not willie_auth(): return jsonify({'error': 'unauthorized'}), 401
    db    = get_db()
    claim = db.execute('SELECT * FROM claims WHERE id=?', (claim_id,)).fetchone()
    if not claim: return jsonify({'error': 'Claim not found'}), 404
    rooms  = db.execute('SELECT id FROM rooms WHERE claim_id=?', (claim_id,)).fetchall()
    photos = db.execute('SELECT id FROM photos WHERE claim_id=?', (claim_id,)).fetchall()
    checks = {
        'policy_number':     bool(claim['policy_number']),
        'policy_type':       bool(claim['policy_type']),
        'coverage_building': bool(claim['coverage_building']),
        'coverage_contents': bool(claim['coverage_contents']),
        'deductible':        bool(claim['deductible']),
        'flood_date':        bool(claim['flood_date']),
        'flood_source':      bool(claim['flood_source']),
        'water_category':    bool(claim['water_category']),
        'water_class':       bool(claim['water_class']),
        'water_depth':       bool(claim['water_depth_in']),
        'date_water_removed':bool(claim['date_water_removed']),
        'inspection_date':   bool(claim['inspection_date']),
        'flood_zone':        bool(claim['flood_zone'] and claim['flood_zone'] != 'Unknown'),
        'fema_map':          bool(claim['fema_map_number']),
        'photos_3plus':      len(photos) >= 3,
        'rooms_documented':  len(rooms) >= 1,
        'estimate_done':     bool(claim['total_estimate']),
    }
    score   = sum(1 for v in checks.values() if v)
    total   = len(checks)
    pct     = round(score / total * 100)
    missing = [k for k, v in checks.items() if not v]
    grade   = 'Excellent' if pct >= 90 else 'Good' if pct >= 75 else 'Needs Work' if pct >= 50 else 'Incomplete'
    # 60-day deadline
    deadline = None
    if claim['flood_date']:
        try:
            dl = datetime.datetime.strptime(claim['flood_date'], '%Y-%m-%d') + datetime.timedelta(days=60)
            deadline = dl.strftime('%B %d, %Y')
        except Exception:
            pass
    return jsonify({'ok': True, 'claim_id': claim_id, 'claim_number': claim['claim_number'],
                    'score': score, 'total': total, 'percent': pct, 'grade': grade,
                    'missing': missing, 'proof_of_loss_deadline': deadline,
                    'message': f'{claim["claim_number"]} is {pct}% complete ({grade}). Missing: {missing if missing else "nothing"}'})


# ── Willie: FEMA Lookup ────────────────────────────────────────────────────
@app.route('/willie/api/claims/<int:claim_id>/fema-lookup', methods=['POST'])
def willie_fema_lookup(claim_id):
    if not willie_auth(): return jsonify({'error': 'unauthorized'}), 401
    db    = get_db()
    claim = db.execute('SELECT * FROM claims WHERE id=?', (claim_id,)).fetchone()
    if not claim: return jsonify({'error': 'Claim not found'}), 404
    result = lookup_fema_flood_zone(claim['property_address'])
    if result and result.get('flood_zone'):
        db.execute('''
            UPDATE claims SET flood_zone=?, fema_map_number=?, lat=?, lng=?,
            maps_embed_url=?, updated_at=CURRENT_TIMESTAMP WHERE id=?
        ''', (result['flood_zone'], result.get('fema_map_number',''),
              result.get('lat',0), result.get('lng',0), result.get('maps_embed_url',''), claim_id))
        db.commit()
        _log_activity(claim_id, f'FEMA flood zone looked up: Zone {result["flood_zone"]} (by Aquila)', 'Aquila')
        return jsonify({'ok': True, 'flood_zone': result['flood_zone'],
                        'fema_map_number': result.get('fema_map_number'),
                        'message': f'FEMA lookup complete. Zone: {result["flood_zone"]}, Map Panel: {result.get("fema_map_number","N/A")}'})
    return jsonify({'ok': False, 'error': 'Could not determine flood zone for this address'})


# ── Willie: Send Notification ────────────────────────────────────────────────
@app.route('/willie/api/claims/<int:claim_id>/notify', methods=['POST'])
def willie_notify_client(claim_id):
    if not willie_auth(): return jsonify({'error': 'unauthorized'}), 401
    db    = get_db()
    claim = db.execute('SELECT * FROM claims WHERE id=?', (claim_id,)).fetchone()
    if not claim: return jsonify({'error': 'Claim not found'}), 404
    data    = request.get_json(silent=True) or {}
    message = data.get('message', '').strip()
    method  = data.get('method', 'email').lower()  # email or sms
    if not message:
        return jsonify({'error': 'message is required'}), 400
    sent_email = sent_sms_flag = False
    if method in ('email', 'both') and claim['client_email']:
        subject = f'Update on your FloodClaims — {claim["claim_number"]}'
        html = f'<div style="font-family:sans-serif"><h2>FloodClaims Pro Update</h2><p>Hello {claim["client_name"]},</p><p>{message}</p><hr><small>Claim: {claim["claim_number"]}</small></div>'
        sent_email = send_email(claim['client_email'], subject, html)
        if sent_email:
            _log_notification(claim_id, 'manual', claim['client_email'], message)
    if method in ('sms', 'both'):
        sent_sms_flag = notify_client_sms(claim, f'FloodClaims Pro | {claim["claim_number"]}: {message}')
    result = []
    if sent_email: result.append('email')
    if sent_sms_flag: result.append('SMS')
    if not result:
        return jsonify({'ok': False, 'message': 'Notification not sent — check SendGrid/Twilio config in Settings'})
    _log_activity(claim_id, f'Notification sent via {" and ".join(result)} (by Aquila)', 'Aquila')
    return jsonify({'ok': True, 'sent_via': result,
                    'message': f'Notification sent to {claim["client_name"]} via {" and ".join(result)}'})


# ── Willie: Move Pipeline Status ────────────────────────────────────────────────
@app.route('/willie/api/claims/<int:claim_id>/move-pipeline', methods=['POST'])
def willie_move_pipeline(claim_id):
    if not willie_auth(): return jsonify({'error': 'unauthorized'}), 401
    data   = request.get_json(silent=True) or {}
    status = data.get('status', '').strip()
    valid  = ['New', 'In Progress', 'Submitted', 'Closed']
    if status not in valid:
        return jsonify({'error': f'status must be one of {valid}'}), 400
    db    = get_db()
    claim = db.execute('SELECT * FROM claims WHERE id=?', (claim_id,)).fetchone()
    if not claim: return jsonify({'error': 'Claim not found'}), 404
    old_status = claim['status']
    db.execute('UPDATE claims SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?', (status, claim_id))
    db.commit()
    if old_status != status:
        notify_client_status_change(claim, status)
        _log_activity(claim_id, f'Moved from {old_status} → {status} (by Aquila)', 'Aquila')
    return jsonify({'ok': True, 'old_status': old_status, 'new_status': status,
                    'message': f'{claim["claim_number"]} moved from {old_status} to {status}'})


# ── Willie: Analytics Summary ────────────────────────────────────────────────
@app.route('/willie/api/analytics', methods=['GET'])
def willie_analytics():
    if not willie_auth(): return jsonify({'error': 'unauthorized'}), 401
    db     = get_db()
    claims = db.execute('SELECT * FROM claims ORDER BY created_at ASC').fetchall()
    closed = [c for c in claims if c['status'] == 'Closed']
    open_c = [c for c in claims if c['status'] != 'Closed']
    cycle_times = []
    for c in closed:
        try:
            s = datetime.datetime.fromisoformat(c['created_at'])
            e = datetime.datetime.fromisoformat(c['updated_at'])
            diff = (e - s).days
            if diff >= 0: cycle_times.append(diff)
        except Exception:
            pass
    avg_cycle = round(sum(cycle_times)/len(cycle_times), 1) if cycle_times else None
    return jsonify({'ok': True,
                    'total_claims':    len(claims),
                    'open_pipeline':   sum(c['total_estimate'] for c in open_c),
                    'closed_revenue':  sum(c['total_estimate'] for c in closed),
                    'avg_cycle_days':  avg_cycle,
                    'new':             sum(1 for c in claims if c['status'] == 'New'),
                    'in_progress':     sum(1 for c in claims if c['status'] == 'In Progress'),
                    'submitted':       sum(1 for c in claims if c['status'] == 'Submitted'),
                    'closed':          len(closed),
                    'message': f'{len(claims)} total claims. Pipeline: ${sum(c["total_estimate"] for c in open_c):,.2f}. Avg cycle: {avg_cycle} days.'})


# ── Willie: Get Upcoming Schedule ───────────────────────────────────────────────
@app.route('/willie/api/schedule', methods=['GET'])
def willie_get_schedule():
    if not willie_auth(): return jsonify({'error': 'unauthorized'}), 401
    db    = get_db()
    today = datetime.date.today().isoformat()
    slots = db.execute('''
        SELECT s.*, c.claim_number, c.client_name, c.property_address, u.name as adjuster_name
        FROM inspection_slots s
        JOIN claims c ON s.claim_id=c.id
        LEFT JOIN users u ON s.adjuster_id=u.id
        WHERE s.slot_date >= ? AND s.status != 'cancelled'
        ORDER BY s.slot_date, s.slot_time LIMIT 20
    ''', (today,)).fetchall()
    return jsonify({'ok': True, 'today': today, 'upcoming': [dict(s) for s in slots],
                    'count': len(slots),
                    'message': f'{len(slots)} upcoming inspection(s) from today onward'})


@app.route('/willie/api/claims/<int:claim_id>/analyze', methods=['POST'])
def willie_analyze_claim(claim_id):
    """Run vision AI on all claim photos and return structured field recommendations.
    Willie uses this to fill in water_category, water_class, flood_source, damage description,
    and suggested rooms/line items based purely on what the photos show."""
    if not willie_auth(): return jsonify({'error': 'unauthorized'}), 401
    db  = get_db()
    claim = db.execute('SELECT * FROM claims WHERE id=?', (claim_id,)).fetchone()
    if not claim: return jsonify({'error': 'Claim not found'}), 404
    claim = dict(claim)

    key   = get_setting('openrouter_api_key') or OPENROUTER_KEY
    model = get_setting('ai_model') or 'openai/gpt-4o-mini'
    if not key:
        return jsonify({'error': 'OpenRouter API key not configured'}), 400

    photos = [dict(p) for p in db.execute(
        'SELECT * FROM photos WHERE claim_id=? ORDER BY id', (claim_id,)).fetchall()]

    if not photos:
        return jsonify({'ok': False, 'error': 'No photos on this claim yet. Upload photos first so I can analyze them.'}), 400

    # Run fresh vision analysis on all photos (up to 8)
    photo_analyses = []
    for photo in photos[:8]:
        photo_path = os.path.join(UPLOAD_DIR, photo['filename'])
        if not os.path.exists(photo_path):
            continue
        # Always re-run for analyze endpoint — we want fresh detailed descriptions
        desc = ai_describe_photo_detailed(photo_path, key, model)
        if desc:
            label = photo.get('caption') or photo['filename']
            photo_analyses.append({'label': label, 'description': desc, 'photo_id': photo['id']})
            db.execute('UPDATE photos SET ai_description=? WHERE id=?', (desc, photo['id']))
    db.commit()

    if not photo_analyses:
        return jsonify({'ok': False, 'error': 'Could not analyze photos. Check your OpenRouter API key in Settings.'}), 400

    # Build structured analysis prompt
    photos_text = '\n'.join(f'Photo [{p["label"]}]: {p["description"]}' for p in photo_analyses)

    analysis_prompt = f"""You are a licensed flood damage adjuster analyzing photos of a flood-damaged property.

Claim: {claim['claim_number']} | Client: {claim['client_name']} | Address: {claim['property_address']}
Flood Date: {claim['flood_date']}

PHOTO ANALYSES:
{photos_text}

Based ONLY on what you can see in these photos, provide a structured JSON response with your assessment.
Return ONLY valid JSON, no other text:

{{
  "water_category": "1, 2, or 3 (3=floodwater/black water, 2=gray water, 1=clean)",
  "water_class": "1, 2, 3, or 4 (4=hardwood/brick/specialty, 3=ceiling saturated, 2=full room walls, 1=floors only)",
  "flood_source": "brief description of flood source visible in photos",
  "water_depth_in": "estimated water depth in inches based on water lines visible, or empty string",
  "cause_of_loss": "what caused the damage (e.g. Storm surge, Pipe burst, Roof leak, Rising floodwater)",
  "property_type": "Single Family, Condo, Commercial, Mobile Home, or empty",
  "damage_summary": "2-3 sentence professional summary of all damage visible across all photos",
  "suggested_rooms": [
    {{
      "name": "room name",
      "damage_notes": "what needs to be done in this room",
      "line_items": [
        {{"description": "work item", "quantity": number, "unit": "sf/lf/ea", "unit_cost": dollar_amount}}
      ]
    }}
  ],
  "recommended_field_updates": {{
    "water_category": "value",
    "water_class": "value",
    "flood_source": "value",
    "water_depth_in": "value or empty",
    "cause_of_loss": "value",
    "notes": "professional damage summary for claim notes"
  }}
}}"""

    raw = call_openrouter([{'role': 'user', 'content': analysis_prompt}], model, key, max_tokens=2000)

    # Parse JSON from response
    import re as _re
    json_match = _re.search(r'\{[\s\S]+\}', raw)
    if not json_match:
        return jsonify({'ok': False, 'error': 'AI returned non-JSON response', 'raw': raw[:300]}), 500

    try:
        analysis = json.loads(json_match.group(0))
    except Exception:
        return jsonify({'ok': False, 'error': 'Could not parse AI response as JSON', 'raw': raw[:300]}), 500

    return jsonify({
        'ok':             True,
        'claim_id':       claim_id,
        'claim_number':   claim['claim_number'],
        'photos_analyzed': len(photo_analyses),
        'analysis':       analysis,
        'message':        f'Analyzed {len(photo_analyses)} photo(s). Use update_claim_fields to apply the recommendations.',
    })


def ai_describe_photo_detailed(image_path, key, model):
    """Run vision AI on a photo with a detailed damage-focused prompt, customized by brain training."""
    try:
        # Load custom photo prompt from brain training if available
        custom_prompt = get_setting('brain_photo_prompt', '')
        if custom_prompt:
            prompt_text = custom_prompt
        else:
            prompt_text = (
                'You are a certified flood damage assessor. Analyze this photo in extreme detail. '
                'Describe EVERYTHING you see:\n'
                '• List each item/structure visible (walls, floors, ceilings, cabinets, appliances, furniture, doors, windows, etc.)\n'
                '• For each item, note its CONDITION (undamaged / minor water staining / moderate damage / severe damage / destroyed)\n'
                '• Describe WATER EVIDENCE: water lines on walls, standing water depth, moisture marks, discoloration\n'
                '• Note MOLD/MILDEW: presence, color, location, estimated coverage\n'
                '• Describe STRUCTURAL CONCERNS: warping, buckling, cracking, delamination, foundation shifts\n'
                '• Note the FLOORING type and damage level (hardwood/tile/carpet/cork/concrete — buckled/stained/warped/destroyed)\n'
                '• Note WALL/DRYWALL condition: water line height, peeling paint, soft spots, holes, texture damage\n'
                '• Note CEILING condition: staining, sagging, holes, collapse risk\n'
                '• Identify any PERSONAL PROPERTY/CONTENTS visible and their damage state\n'
                '• Estimate water category (1=clean, 2=gray, 3=blackwater) and water class (1-4)\n'
                '• Be extremely specific — describe dimensions, materials, colors, textures where visible\n'
                '• Format as a structured inspection report with clear sections'
            )

        with open(image_path, 'rb') as f:
            img_b64 = base64.b64encode(f.read()).decode()
        ext  = image_path.rsplit('.', 1)[-1].lower()
        mime = f'image/{ext}' if ext != 'jpg' else 'image/jpeg'
        r = _req.post(
            'https://openrouter.ai/api/v1/chat/completions',
            headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
            json={
                'model': model,
                'messages': [{
                    'role': 'user',
                    'content': [
                        {'type': 'text', 'text': prompt_text},
                        {'type': 'image_url', 'image_url': {'url': f'data:{mime};base64,{img_b64}'}}
                    ]
                }],
                'max_tokens': 1500
            }, timeout=60)
        return r.json()['choices'][0]['message']['content']
    except Exception:
        return ''


# ─────────────────────────────────────────────────────────────────────────────
# CLAIM DUPLICATE
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/claims/<int:claim_id>/duplicate', methods=['POST'])
@login_required
@csrf_required
def duplicate_claim(claim_id):
    db    = get_db()
    src   = db.execute('SELECT * FROM claims WHERE id=?', (claim_id,)).fetchone()
    if not src:
        flash('Claim not found.', 'error')
        return redirect(url_for('dashboard'))
    new_num = gen_claim_number()
    db.execute('''
        INSERT INTO claims
          (claim_number, adjuster_id, client_name, client_phone, client_phone_alt, client_email,
           property_address, property_type, property_sqft, year_built, num_floors,
           flood_date, flood_source, water_category, water_class, water_depth_in,
           date_water_removed, inspection_date, insurance_company, policy_number, policy_type,
           coverage_building, coverage_contents, deductible, mortgage_company, mortgage_loan_number,
           cause_of_loss, priority, notes, flood_zone, fema_map_number)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    ''', (new_num, src['adjuster_id'], src['client_name'], src['client_phone'],
          src['client_phone_alt'], src['client_email'], src['property_address'],
          src['property_type'], src['property_sqft'], src['year_built'], src['num_floors'],
          src['flood_date'], src['flood_source'], src['water_category'], src['water_class'],
          src['water_depth_in'], src['date_water_removed'], src['inspection_date'],
          src['insurance_company'], src['policy_number'], src['policy_type'],
          src['coverage_building'], src['coverage_contents'], src['deductible'],
          src['mortgage_company'], src['mortgage_loan_number'],
          src['cause_of_loss'], src['priority'],
          f'[Duplicated from {src["claim_number"]}] {src["notes"]}',
          src['flood_zone'], src['fema_map_number']))
    db.commit()
    new_claim = db.execute('SELECT id FROM claims WHERE claim_number=?', (new_num,)).fetchone()
    # Copy rooms + line items (not photos)
    rooms = db.execute('SELECT * FROM rooms WHERE claim_id=?', (claim_id,)).fetchall()
    for room in rooms:
        db.execute('INSERT INTO rooms (claim_id, name, description) VALUES (?,?,?)',
                   (new_claim['id'], room['name'], room['description']))
        db.commit()
        new_room = db.execute('SELECT id FROM rooms WHERE claim_id=? ORDER BY id DESC LIMIT 1',
                              (new_claim['id'],)).fetchone()
        items = db.execute('SELECT * FROM line_items WHERE room_id=?', (room['id'],)).fetchall()
        for item in items:
            db.execute('INSERT INTO line_items (room_id, description, quantity, unit, unit_cost, total) VALUES (?,?,?,?,?,?)',
                       (new_room['id'], item['description'], item['quantity'], item['unit'], item['unit_cost'], item['total']))
    db.commit()
    recalc_claim(new_claim['id'])
    _log_activity(new_claim['id'], 'Claim duplicated from ' + src['claim_number'])
    flash(f'Claim duplicated as {new_num}.', 'success')
    return redirect(url_for('claim_detail', claim_id=new_claim['id']))


# ─────────────────────────────────────────────────────────────────────────────
# ACTIVITY / AUDIT LOG
# ─────────────────────────────────────────────────────────────────────────────

def _log_activity(claim_id, action, user_name=None):
    """Write an entry to the claim activity log."""
    try:
        db   = get_db()
        who  = user_name or session.get('name', 'System')
        db.execute(
            'INSERT INTO activity_log (claim_id, actor, action) VALUES (?,?,?)',
            (claim_id, who, action)
        )
        db.commit()
    except Exception as e:
        print(f'_log_activity error: {e}')


@app.route('/claims/<int:claim_id>/activity')
@login_required
def claim_activity(claim_id):
    db    = get_db()
    claim = db.execute('SELECT * FROM claims WHERE id=?', (claim_id,)).fetchone()
    if not claim:
        flash('Claim not found.', 'error')
        return redirect(url_for('dashboard'))
    logs = db.execute(
        'SELECT * FROM activity_log WHERE claim_id=? ORDER BY created_at DESC LIMIT 100',
        (claim_id,)
    ).fetchall()
    return render_template('activity.html', claim=claim, logs=logs)


# ─────────────────────────────────────────────────────────────────────────────
# SMS VIA TWILIO
# ─────────────────────────────────────────────────────────────────────────────

def send_sms(to_number, body):
    """Send SMS via Twilio. Returns True on success."""
    sid   = get_setting('twilio_account_sid')  or os.environ.get('TWILIO_ACCOUNT_SID', '')
    token = get_setting('twilio_auth_token')   or os.environ.get('TWILIO_AUTH_TOKEN', '')
    from_ = get_setting('twilio_from_number')  or os.environ.get('TWILIO_FROM_NUMBER', '')
    if not sid or not token or not from_:
        print(f'[SMS] Twilio not configured. To: {to_number} | {body}')
        return False
    try:
        r = _req.post(
            f'https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json',
            auth=(sid, token),
            data={'From': from_, 'To': to_number, 'Body': body},
            timeout=10
        )
        return r.status_code in (200, 201)
    except Exception as e:
        print(f'Twilio error: {e}')
        return False


def notify_client_sms(claim, message):
    """Send SMS to client if they have a phone number."""
    phone = claim['client_phone'] or claim['client_phone_alt'] or ''
    if not phone:
        return False
    # Normalize to E.164 (basic: strip non-digits, add +1 for US)
    digits = ''.join(c for c in phone if c.isdigit())
    if len(digits) == 10:
        digits = '1' + digits
    if not digits.startswith('1') or len(digits) != 11:
        return False
    e164 = '+' + digits
    sent = send_sms(e164, message)
    if sent:
        _log_notification(claim['id'], 'sms', e164, message)
    return sent


@app.route('/claims/<int:claim_id>/sms', methods=['POST'])
@login_required
@csrf_required
def send_claim_sms(claim_id):
    """Manually send an SMS update to the claim client."""
    db    = get_db()
    claim = db.execute('SELECT * FROM claims WHERE id=?', (claim_id,)).fetchone()
    if not claim:
        return jsonify({'ok': False, 'error': 'Claim not found'}), 404
    msg = request.form.get('message', '').strip()
    if not msg:
        return jsonify({'ok': False, 'error': 'Message required'}), 400
    full_msg = f'FloodClaims Pro | {claim["claim_number"]}: {msg}'
    sent = notify_client_sms(claim, full_msg)
    if sent:
        flash(f'SMS sent to {claim["client_phone"]}.', 'success')
    else:
        flash('SMS not sent — configure Twilio in Settings.', 'error')
    return redirect(url_for('claim_detail', claim_id=claim_id))


# ─────────────────────────────────────────────────────────────────────────────
# MOBILE PHOTO UPLOAD (QR code portal)
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/claims/<int:claim_id>/mobile-upload')
def mobile_upload_page(claim_id):
    """Public mobile upload page — no login, accessed via QR code."""
    token = request.args.get('t', '')
    db    = get_db()
    # Validate token matches claim
    row = db.execute(
        'SELECT claim_id FROM client_portal_tokens WHERE token=? AND claim_id=?',
        (token, claim_id)
    ).fetchone()
    if not row:
        return '<h2 style="font-family:sans-serif;text-align:center;margin-top:4rem">Link expired or invalid.</h2>', 403
    claim = db.execute('SELECT * FROM claims WHERE id=?', (claim_id,)).fetchone()
    rooms = db.execute('SELECT * FROM rooms WHERE claim_id=? AND deleted_at IS NULL ORDER BY id', (claim_id,)).fetchall()
    return render_template('mobile_upload.html', claim=claim, rooms=rooms, token=token)


@app.route('/claims/<int:claim_id>/mobile-upload', methods=['POST'])
def mobile_upload_post(claim_id):
    """Accept photo uploads from mobile upload page."""
    token = request.args.get('t', '')
    db    = get_db()
    row = db.execute(
        'SELECT claim_id FROM client_portal_tokens WHERE token=? AND claim_id=?',
        (token, claim_id)
    ).fetchone()
    if not row:
        return jsonify({'ok': False, 'error': 'Invalid token'}), 403
    files   = request.files.getlist('photos')
    room_id = request.form.get('room_id') or None
    caption = request.form.get('caption', 'Mobile upload')
    saved   = 0
    for f in files:
        if f and allowed_file(f.filename):
            ext      = f.filename.rsplit('.', 1)[1].lower()
            filename = f'{secrets.token_hex(12)}.{ext}'
            path     = os.path.join(UPLOAD_DIR, filename)
            f.save(path)
            ai_desc = ai_describe_photo(path)
            db.execute(
                'INSERT INTO photos (claim_id, room_id, filename, caption, ai_description) VALUES (?,?,?,?,?)',
                (claim_id, room_id, filename, caption, ai_desc)
            )
            saved += 1
    db.commit()
    return jsonify({'ok': True, 'saved': saved})


@app.route('/claims/<int:claim_id>/qr')
@login_required
def claim_qr(claim_id):
    """Show a QR code for mobile photo upload — generates/reuses the portal token."""
    db    = get_db()
    claim = db.execute('SELECT * FROM claims WHERE id=?', (claim_id,)).fetchone()
    if not claim:
        flash('Claim not found.', 'error')
        return redirect(url_for('dashboard'))
    # Reuse or create portal token
    row = db.execute('SELECT token FROM client_portal_tokens WHERE claim_id=?', (claim_id,)).fetchone()
    if row:
        token = row['token']
    else:
        token = secrets.token_urlsafe(32)
        db.execute('INSERT INTO client_portal_tokens (claim_id, token) VALUES (?,?)', (claim_id, token))
        db.commit()
    upload_url = url_for('mobile_upload_page', claim_id=claim_id, t=token, _external=True)
    # Generate QR as SVG using a simple URL-based QR service (no lib needed)
    qr_img_url = f'https://api.qrserver.com/v1/create-qr-code/?size=250x250&data={upload_url}'
    return render_template('qr_upload.html', claim=claim, upload_url=upload_url, qr_img_url=qr_img_url)


# ─────────────────────────────────────────────────────────────────────────────
# BULK ACTIONS + DASHBOARD SEARCH
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/claims/bulk', methods=['POST'])
@login_required
@csrf_required
def bulk_action():
    action   = request.form.get('bulk_action', '')
    ids_raw  = request.form.getlist('claim_ids')
    ids      = [int(i) for i in ids_raw if i.isdigit()]
    if not ids:
        flash('No claims selected.', 'error')
        return redirect(url_for('dashboard'))
    db = get_db()
    if action == 'delete':
        for cid in ids:
            claim = db.execute('SELECT claim_number, client_name FROM claims WHERE id=?', (cid,)).fetchone()
            photos = db.execute('SELECT filename FROM photos WHERE claim_id=?', (cid,)).fetchall()
            for p in photos:
                try:
                    path = os.path.join(UPLOAD_DIR, p['filename'])
                    if os.path.exists(path): os.remove(path)
                except Exception: pass
            db.execute('DELETE FROM claims WHERE id=?', (cid,))
            if claim:
                _log_activity(cid, f'Claim {claim["claim_number"]} bulk-deleted')
        db.commit()
        flash(f'Deleted {len(ids)} claim(s).', 'success')
    elif action in ('set_new', 'set_in_progress', 'set_submitted', 'set_closed'):
        status_map = {'set_new': 'New', 'set_in_progress': 'In Progress',
                      'set_submitted': 'Submitted', 'set_closed': 'Closed'}
        new_status = status_map[action]
        for cid in ids:
            claim = db.execute('SELECT * FROM claims WHERE id=?', (cid,)).fetchone()
            db.execute('UPDATE claims SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?',
                       (new_status, cid))
            if claim:
                _log_activity(cid, f'Status changed to {new_status} (bulk)')
            if claim and claim['client_email']:
                notify_client_status_change(claim, new_status)
        db.commit()
        flash(f'Updated {len(ids)} claim(s) to "{new_status}".', 'success')
    elif action == 'assign':
        adj_id = request.form.get('assign_adjuster')
        if adj_id:
            adj_name = db.execute('SELECT name FROM users WHERE id=?', (adj_id,)).fetchone()
            for cid in ids:
                db.execute('UPDATE claims SET adjuster_id=?, updated_at=CURRENT_TIMESTAMP WHERE id=?',
                           (adj_id, cid))
                _log_activity(cid, f'Assigned to {adj_name["name"] if adj_name else adj_id} (bulk)')
            db.commit()
            flash(f'Assigned {len(ids)} claim(s).', 'success')
    else:
        flash('Unknown action.', 'error')
    return redirect(url_for('dashboard'))


# ─────────────────────────────────────────────────────────────────────────────
# WEEKLY SUMMARY REPORT (email)
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/admin/weekly-report', methods=['POST'])
@login_required
@admin_required
@csrf_required
def send_weekly_report():
    db     = get_db()
    claims = db.execute('SELECT * FROM claims').fetchall()
    week_ago = (datetime.datetime.now() - datetime.timedelta(days=7)).isoformat()
    new_this_week    = [c for c in claims if c['created_at'] >= week_ago]
    closed_this_week = [c for c in claims if c['status'] == 'Closed' and c['updated_at'] >= week_ago]
    open_claims      = [c for c in claims if c['status'] != 'Closed']
    pipeline         = sum(c['total_estimate'] for c in open_claims)
    admin_email      = get_setting('admin_report_email') or ADMIN_EMAIL
    html = f'''
    <div style="font-family:sans-serif;max-width:640px;margin:0 auto">
      <div style="background:#0a1628;color:#fff;padding:1.5rem 2rem;border-radius:12px 12px 0 0">
        <h2 style="margin:0;font-size:1.3rem">FloodClaims Pro — Weekly Summary</h2>
        <p style="margin:.25rem 0 0;opacity:.7;font-size:.85rem">{datetime.datetime.now().strftime("%B %d, %Y")}</p>
      </div>
      <div style="background:#f8fafc;padding:1.5rem 2rem;border:1px solid #e2e8f0;border-top:none">
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:1rem;margin-bottom:1.5rem">
          <div style="background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:1rem;text-align:center">
            <div style="font-size:1.8rem;font-weight:800;color:#3b82f6">{len(new_this_week)}</div>
            <div style="font-size:.75rem;color:#64748b;font-weight:700;text-transform:uppercase">New This Week</div>
          </div>
          <div style="background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:1rem;text-align:center">
            <div style="font-size:1.8rem;font-weight:800;color:#10b981">{len(closed_this_week)}</div>
            <div style="font-size:.75rem;color:#64748b;font-weight:700;text-transform:uppercase">Closed This Week</div>
          </div>
          <div style="background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:1rem;text-align:center">
            <div style="font-size:1.8rem;font-weight:800;color:#f59e0b">{len(open_claims)}</div>
            <div style="font-size:.75rem;color:#64748b;font-weight:700;text-transform:uppercase">Open Claims</div>
          </div>
          <div style="background:#fff;border:1px solid #e2e8f0;border-radius:10px;padding:1rem;text-align:center">
            <div style="font-size:1.8rem;font-weight:800;color:#6366f1">${pipeline:,.0f}</div>
            <div style="font-size:.75rem;color:#64748b;font-weight:700;text-transform:uppercase">Pipeline Value</div>
          </div>
        </div>
        <p style="font-size:.85rem;color:#64748b">Log in to <a href="https://billy-floods.up.railway.app">FloodClaims Pro</a> to view full details.</p>
      </div>
    </div>'''
    sent = send_email(admin_email, f'FloodClaims Pro — Weekly Report ({datetime.datetime.now().strftime("%b %d")})', html)
    if sent:
        flash(f'📧 Weekly report sent to {admin_email}.', 'success')
    else:
        flash('Email not sent — configure SendGrid in Settings.', 'error')
    return redirect(url_for('settings'))


# ─────────────────────────────────────────────────────────────────────────────
# FEATURE 1: KANBAN PIPELINE VIEW
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/pipeline')
@login_required
def pipeline():
    db = get_db()
    if session['role'] == 'admin':
        claims = db.execute('''
            SELECT c.*, u.name as adjuster_name
            FROM claims c LEFT JOIN users u ON c.adjuster_id=u.id
            ORDER BY c.kanban_order ASC, c.created_at DESC
        ''').fetchall()
    else:
        claims = db.execute('''
            SELECT c.*, u.name as adjuster_name
            FROM claims c LEFT JOIN users u ON c.adjuster_id=u.id
            WHERE c.adjuster_id=?
            ORDER BY c.kanban_order ASC, c.created_at DESC
        ''', (session['user_id'],)).fetchall()
    cols = ['New', 'In Progress', 'Submitted', 'Closed']
    board = {col: [c for c in claims if c['status'] == col] for col in cols}
    return render_template('pipeline.html', board=board, cols=cols)


@app.route('/pipeline/move', methods=['POST'])
@login_required
def pipeline_move():
    """AJAX: move a claim to a new status column."""
    data      = request.get_json() or {}
    claim_id  = data.get('claim_id')
    new_status = data.get('status')
    valid = ['New', 'In Progress', 'Submitted', 'Closed']
    if not claim_id or new_status not in valid:
        return jsonify({'ok': False, 'error': 'Invalid'}), 400
    db = get_db()
    claim = db.execute('SELECT * FROM claims WHERE id=?', (claim_id,)).fetchone()
    if not claim:
        return jsonify({'ok': False, 'error': 'Not found'}), 404
    if session['role'] != 'admin' and claim['adjuster_id'] != session['user_id']:
        return jsonify({'ok': False, 'error': 'Forbidden'}), 403
    old_status = claim['status']
    db.execute('UPDATE claims SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?',
               (new_status, claim_id))
    db.commit()
    # Fire status-change notification
    if old_status != new_status and claim['client_email']:
        notify_client_status_change(claim, new_status)
        _log_notification(claim_id, 'status_change',
                          claim['client_email'],
                          f'Claim {claim["claim_number"]} moved to {new_status}')
    return jsonify({'ok': True})


# ─────────────────────────────────────────────────────────────────────────────
# FEATURE 2: INSPECTION SCHEDULER
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/schedule')
@login_required
def schedule():
    db = get_db()
    if session['role'] == 'admin':
        slots = db.execute('''
            SELECT s.*, c.claim_number, c.client_name, c.property_address,
                   u.name as adjuster_name
            FROM inspection_slots s
            JOIN claims c ON s.claim_id = c.id
            LEFT JOIN users u ON s.adjuster_id = u.id
            ORDER BY s.slot_date ASC, s.slot_time ASC
        ''').fetchall()
        claims = db.execute('''
            SELECT id, claim_number, client_name, property_address, adjuster_id
            FROM claims WHERE status != 'Closed' ORDER BY created_at DESC
        ''').fetchall()
        adjusters = db.execute('SELECT id, name FROM users ORDER BY name').fetchall()
    else:
        slots = db.execute('''
            SELECT s.*, c.claim_number, c.client_name, c.property_address,
                   u.name as adjuster_name
            FROM inspection_slots s
            JOIN claims c ON s.claim_id = c.id
            LEFT JOIN users u ON s.adjuster_id = u.id
            WHERE s.adjuster_id = ?
            ORDER BY s.slot_date ASC, s.slot_time ASC
        ''', (session['user_id'],)).fetchall()
        claims = db.execute('''
            SELECT id, claim_number, client_name, property_address, adjuster_id
            FROM claims WHERE adjuster_id=? AND status != 'Closed'
            ORDER BY created_at DESC
        ''', (session['user_id'],)).fetchall()
        adjusters = []
    today = datetime.date.today().isoformat()
    return render_template('schedule.html', slots=slots, claims=claims,
                           adjusters=adjusters, today=today)


@app.route('/schedule/add', methods=['POST'])
@login_required
@csrf_required
def schedule_add():
    db        = get_db()
    claim_id  = request.form.get('claim_id')
    slot_date = request.form.get('slot_date')
    slot_time = request.form.get('slot_time')
    notes     = request.form.get('notes', '')
    adj_id    = request.form.get('adjuster_id') or session['user_id']
    if not claim_id or not slot_date or not slot_time:
        flash('Claim, date and time are required.', 'error')
        return redirect(url_for('schedule'))
    db.execute('''
        INSERT INTO inspection_slots (claim_id, adjuster_id, slot_date, slot_time, notes)
        VALUES (?,?,?,?,?)
    ''', (claim_id, adj_id, slot_date, slot_time, notes))
    # Update claim's sched fields
    db.execute('UPDATE claims SET sched_date=?, sched_time=?, inspection_date=?, updated_at=CURRENT_TIMESTAMP WHERE id=?',
               (slot_date, slot_time, slot_date, claim_id))
    db.commit()
    _log_activity(claim_id, f'Inspection scheduled: {slot_date} at {slot_time}')
    claim = db.execute('SELECT * FROM claims WHERE id=?', (claim_id,)).fetchone()
    # Email adjuster
    adj = db.execute('SELECT * FROM users WHERE id=?', (adj_id,)).fetchone()
    if adj and adj['email']:
        send_email(adj['email'],
                   f'Inspection Scheduled — {claim["claim_number"]}',
                   f'<p>An inspection has been scheduled for claim <strong>{claim["claim_number"]}</strong> '
                   f'({claim["client_name"]}) on <strong>{slot_date} at {slot_time}</strong>.</p>'
                   f'<p>Property: {claim["property_address"]}</p>'
                   f'<p>Notes: {notes or "None"}</p>')
    flash(f'Inspection scheduled for {slot_date} at {slot_time}.', 'success')
    return redirect(url_for('schedule'))


@app.route('/schedule/<int:slot_id>/status', methods=['POST'])
@login_required
def schedule_update_status(slot_id):
    new_status = request.form.get('status', 'pending')
    db = get_db()
    db.execute('UPDATE inspection_slots SET status=? WHERE id=?', (new_status, slot_id))
    db.commit()
    return redirect(url_for('schedule'))


@app.route('/schedule/<int:slot_id>/delete', methods=['POST'])
@login_required
@csrf_required
def schedule_delete(slot_id):
    db = get_db()
    db.execute('DELETE FROM inspection_slots WHERE id=?', (slot_id,))
    db.commit()
    flash('Inspection removed.', 'success')
    return redirect(url_for('schedule'))


# ─────────────────────────────────────────────────────────────────────────────
# FEATURE 3: AUTOMATED NOTIFICATIONS
# ─────────────────────────────────────────────────────────────────────────────

def _log_notification(claim_id, notif_type, recipient, message):
    """Log a sent notification to the DB."""
    try:
        db = get_db()
        db.execute(
            'INSERT INTO notifications_log (claim_id, type, recipient, message) VALUES (?,?,?,?)',
            (claim_id, notif_type, recipient, message)
        )
        db.commit()
    except Exception as e:
        print(f'_log_notification error: {e}')


@app.route('/notifications')
@login_required
def notifications():
    db = get_db()
    if session['role'] == 'admin':
        logs = db.execute('''
            SELECT n.*, c.claim_number, c.client_name
            FROM notifications_log n
            LEFT JOIN claims c ON n.claim_id = c.id
            ORDER BY n.sent_at DESC LIMIT 200
        ''').fetchall()
    else:
        logs = db.execute('''
            SELECT n.*, c.claim_number, c.client_name
            FROM notifications_log n
            LEFT JOIN claims c ON n.claim_id = c.id
            WHERE c.adjuster_id = ?
            ORDER BY n.sent_at DESC LIMIT 200
        ''', (session['user_id'],)).fetchall()
    return render_template('notifications.html', logs=logs)


@app.route('/notifications/send', methods=['POST'])
@login_required
@csrf_required
def notifications_send():
    """Manually send a status update email for a claim."""
    claim_id = request.form.get('claim_id')
    message  = request.form.get('message', '').strip()
    db = get_db()
    claim = db.execute('SELECT * FROM claims WHERE id=?', (claim_id,)).fetchone()
    if not claim:
        flash('Claim not found.', 'error')
        return redirect(url_for('notifications'))
    if not claim['client_email']:
        flash('No client email on this claim.', 'error')
        return redirect(url_for('notifications'))
    subject = f'Update on your FloodClaims — {claim["claim_number"]}'
    html = f'''<div style="font-family:sans-serif;max-width:600px;margin:0 auto">
        <h2 style="color:#0a1628">FloodClaims Pro — Claim Update</h2>
        <p>Hello {claim["client_name"]},</p>
        <p>{message}</p>
        <p style="background:#f0fdf4;padding:12px;border-radius:8px;border-left:4px solid #10b981">
            <strong>Claim #: {claim["claim_number"]}</strong><br>
            Status: {claim["status"]}
        </p>
        <hr style="margin:24px 0;border:none;border-top:1px solid #e2e8f0">
        <p style="font-size:12px;color:#94a3b8">FloodClaims Pro · Professional Flood Damage Assessment</p>
    </div>'''
    sent = send_email(claim['client_email'], subject, html)
    if sent:
        _log_notification(claim_id, 'manual', claim['client_email'], message)
        flash(f'Notification sent to {claim["client_email"]}.', 'success')
    else:
        flash('Email not sent — configure SendGrid in Settings.', 'error')
    return redirect(url_for('notifications'))


# ─────────────────────────────────────────────────────────────────────────────
# FEATURE 4: NFIP COMPLIANCE CHECKLIST
# ─────────────────────────────────────────────────────────────────────────────

NFIP_CHECKLIST = [
    ('policy',         'Policy number recorded'),
    ('policy_type',    'Policy type identified (Building/Contents/Both)'),
    ('coverage_bldg',  'Building coverage amount documented'),
    ('coverage_cont',  'Contents coverage amount documented'),
    ('deductible',     'Deductible amount confirmed'),
    ('flood_date',     'Date of loss (flood date) recorded'),
    ('flood_source',   'Flood source documented (river, storm, sewer, etc.)'),
    ('water_cat',      'Water category assigned (Cat 1/2/3)'),
    ('water_class',    'Water class assigned (Class 1–4)'),
    ('water_depth',    'Water depth documented (inches)'),
    ('water_removed',  'Date water removed recorded'),
    ('inspection',     'Inspection date scheduled/completed'),
    ('flood_zone',     'FEMA flood zone looked up'),
    ('fema_map',       'FEMA map panel number recorded'),
    ('photos',         'Damage photos uploaded'),
    ('rooms',          'Room-by-room scope documented'),
    ('estimate',       'AI estimate generated'),
    ('mortgage',       'Mortgage company/loan # recorded (if applicable)'),
]


@app.route('/claims/<int:claim_id>/compliance')
@login_required
def compliance(claim_id):
    db    = get_db()
    claim = db.execute('SELECT * FROM claims WHERE id=?', (claim_id,)).fetchone()
    if not claim:
        flash('Claim not found.', 'error')
        return redirect(url_for('dashboard'))
    rooms  = db.execute('SELECT id FROM rooms WHERE claim_id=?', (claim_id,)).fetchall()
    photos = db.execute('SELECT id FROM photos WHERE claim_id=?', (claim_id,)).fetchall()
    checks = {
        'policy':        bool(claim['policy_number']),
        'policy_type':   bool(claim['policy_type']),
        'coverage_bldg': bool(claim['coverage_building']),
        'coverage_cont': bool(claim['coverage_contents']),
        'deductible':    bool(claim['deductible']),
        'flood_date':    bool(claim['flood_date']),
        'flood_source':  bool(claim['flood_source']),
        'water_cat':     bool(claim['water_category']),
        'water_class':   bool(claim['water_class']),
        'water_depth':   bool(claim['water_depth_in']),
        'water_removed': bool(claim['date_water_removed']),
        'inspection':    bool(claim['inspection_date'] or claim['sched_date']),
        'flood_zone':    bool(claim['flood_zone'] and claim['flood_zone'] != 'Unknown'),
        'fema_map':      bool(claim['fema_map_number']),
        'photos':        len(photos) >= 1,
        'rooms':         len(rooms) >= 1,
        'estimate':      bool(claim['total_estimate']),
        'mortgage':      True,  # optional — always pass
    }
    score  = sum(1 for v in checks.values() if v)
    total  = len(checks)
    pct    = round(score / total * 100)
    return render_template('compliance.html', claim=claim, checklist=NFIP_CHECKLIST,
                           checks=checks, score=score, total=total, pct=pct)


# ─────────────────────────────────────────────────────────────────────────────
# FEATURE 5: ANALYTICS DASHBOARD
# ─────────────────────────────────────────────────────────────────────────────

@app.route('/analytics')
@login_required
def analytics():
    db = get_db()
    # All claims visible to this user
    if session['role'] == 'admin':
        claims = db.execute('SELECT * FROM claims ORDER BY created_at ASC').fetchall()
    else:
        claims = db.execute('SELECT * FROM claims WHERE adjuster_id=? ORDER BY created_at ASC',
                            (session['user_id'],)).fetchall()

    total   = len(claims)
    closed  = [c for c in claims if c['status'] == 'Closed']
    open_c  = [c for c in claims if c['status'] != 'Closed']
    pipeline_val = sum(c['total_estimate'] for c in open_c)
    closed_val   = sum(c['total_estimate'] for c in closed)

    # Avg cycle time (created_at → last updated_at for closed claims)
    cycle_times = []
    for c in closed:
        try:
            start = datetime.datetime.fromisoformat(c['created_at'])
            end   = datetime.datetime.fromisoformat(c['updated_at'])
            diff  = (end - start).days
            if diff >= 0:
                cycle_times.append(diff)
        except Exception:
            pass
    avg_cycle = round(sum(cycle_times) / len(cycle_times), 1) if cycle_times else None

    # Claims by month (last 12 months)
    from collections import defaultdict
    monthly = defaultdict(lambda: {'count': 0, 'value': 0.0})
    for c in claims:
        try:
            mo = c['created_at'][:7]  # 'YYYY-MM'
            monthly[mo]['count'] += 1
            monthly[mo]['value'] += c['total_estimate']
        except Exception:
            pass
    months_sorted = sorted(monthly.keys())[-12:]
    chart_labels  = months_sorted
    chart_counts  = [monthly[m]['count'] for m in months_sorted]
    chart_values  = [round(monthly[m]['value'], 2) for m in months_sorted]

    # Status breakdown
    status_counts = {
        'New':         sum(1 for c in claims if c['status'] == 'New'),
        'In Progress': sum(1 for c in claims if c['status'] == 'In Progress'),
        'Submitted':   sum(1 for c in claims if c['status'] == 'Submitted'),
        'Closed':      sum(1 for c in claims if c['status'] == 'Closed'),
    }

    # Top adjusters (admin only)
    top_adjusters = []
    if session['role'] == 'admin':
        rows = db.execute('''
            SELECT u.name, COUNT(c.id) as cnt, COALESCE(SUM(c.total_estimate),0) as total
            FROM claims c JOIN users u ON c.adjuster_id=u.id
            GROUP BY c.adjuster_id ORDER BY cnt DESC LIMIT 10
        ''').fetchall()
        top_adjusters = [dict(r) for r in rows]

    return render_template('analytics.html',
                           total=total, pipeline_val=pipeline_val, closed_val=closed_val,
                           avg_cycle=avg_cycle, status_counts=status_counts,
                           chart_labels=json.dumps(chart_labels),
                           chart_counts=json.dumps(chart_counts),
                           chart_values=json.dumps(chart_values),
                           top_adjusters=top_adjusters)


# ─────────────────────────────────────────────────────────────────────────────
# SUBMIT PACKAGE — one-click carrier submission export
# ─────────────────────────────────────────────────────────────────────────────

def _build_proof_of_loss_text(claim, rooms, room_data):
    """Generate a plain-text Proof of Loss document pre-filled with claim data."""
    now  = datetime.datetime.now().strftime('%B %d, %Y')
    lines = [
        'PROOF OF LOSS',
        'NATIONAL FLOOD INSURANCE PROGRAM (NFIP)',
        'Standard Flood Insurance Policy',
        '=' * 60,
        '',
        f'Date of Statement:        {now}',
        f'Claim Number:             {claim["claim_number"]}',
        f'Policy Number:            {claim["policy_number"] or "_________________________"}',
        f'Policy Type:              {claim["policy_type"] or "Building + Contents"}',
        f'Insurance Company:        {claim["insurance_company"] or "_________________________"}',
        '',
        'INSURED (POLICYHOLDER)',
        '-' * 40,
        f'Name:                     {claim["client_name"]}',
        f'Phone:                    {claim["client_phone"] or "_________________________"}',
        f'Email:                    {claim["client_email"] or "_________________________"}',
        '',
        'PROPERTY',
        '-' * 40,
        f'Property Address:         {claim["property_address"]}',
        f'Property Type:            {claim["property_type"] or "_________________________"}',
        f'Year Built:               {claim["year_built"] or "_________________________"}',
        f'Square Footage:           {claim["property_sqft"] or "_________________________"}',
        f'FEMA Flood Zone:          {claim["flood_zone"] or "_________________________"}',
        f'FEMA Map Panel Number:    {claim["fema_map_number"] or "_________________________"}',
        '',
        'LOSS INFORMATION',
        '-' * 40,
        f'Date of Loss:             {claim["flood_date"]}',
        f'Cause of Loss:            {claim["flood_source"] or claim["cause_of_loss"] or "Flood"}',
        f'Water Category:           Category {claim["water_category"] or "3"} (Flood water)',
        f'Water Class:              Class {claim["water_class"] or "_"}',
        f'Water Depth (inches):     {claim["water_depth_in"] or "_________________________"}',
        f'Date Water Removed:       {claim["date_water_removed"] or "_________________________"}',
        f'Inspection Date:          {claim["inspection_date"] or "_________________________"}',
        '',
        'COVERAGE',
        '-' * 40,
        f'Building Coverage:        ${claim["coverage_building"]:,.2f}',
        f'Contents Coverage:        ${claim["coverage_contents"]:,.2f}',
        f'Deductible:               ${claim["deductible"]:,.2f}',
        '',
        'MORTGAGE',
        '-' * 40,
        f'Mortgage Company:         {claim["mortgage_company"] or "N/A"}',
        f'Loan Number:              {claim["mortgage_loan_number"] or "N/A"}',
        '',
        'DAMAGE SUMMARY BY ROOM',
        '-' * 40,
    ]
    for rd in room_data:
        room  = rd['room']
        items = rd['line_items']
        lines.append(f'\n  {room["name"]}   Subtotal: ${room["subtotal"]:,.2f}')
        for item in items:
            lines.append(f'    - {item["description"]:40s}  {item["quantity"]} {item["unit"]:8s}  '
                         f'@ ${item["unit_cost"]:8.2f}  =  ${item["total"]:10.2f}')
    lines += [
        '',
        '=' * 60,
        f'TOTAL CLAIM ESTIMATE:     ${claim["total_estimate"]:,.2f}',
        f'Less Deductible:          ${claim["deductible"]:,.2f}',
        f'NET CLAIM AMOUNT:         ${max(0, claim["total_estimate"] - claim["deductible"]):,.2f}',
        '=' * 60,
        '',
        'CERTIFICATION',
        '-' * 40,
        'The insured hereby certifies that the above information is true and',
        'accurate to the best of their knowledge and belief, and that this Proof',
        'of Loss is submitted pursuant to the Standard Flood Insurance Policy.',
        '',
        f'Insured Signature:        _________________________ Date: __________',
        f'Printed Name:             {claim["client_name"]}',
        '',
        f'Adjuster Name:            {claim["adjuster_name"] or "_________________________"}',
        f'Adjuster Signature:       _________________________ Date: __________',
        f'Flood Control Number:     _________________________',
        '',
        '--- Generated by FloodClaims Pro ---',
    ]
    return '\n'.join(lines)


def _build_building_worksheet_text(claim, room_data):
    """Generate NFIP-style Building Property Worksheet."""
    now = datetime.datetime.now().strftime('%B %d, %Y')
    lines = [
        'NFIP BUILDING PROPERTY WORKSHEET',
        '=' * 60,
        f'Claim Number:     {claim["claim_number"]}',
        f'Policy Number:    {claim["policy_number"] or "_________________________"}',
        f'Insured:          {claim["client_name"]}',
        f'Property:         {claim["property_address"]}',
        f'Date of Loss:     {claim["flood_date"]}',
        f'Prepared:         {now}',
        f'Adjuster:         {claim["adjuster_name"] or "_________________________"}',
        '',
        'BUILDING CHARACTERISTICS',
        '-' * 40,
        f'Type:             {claim["property_type"] or "_________________________"}',
        f'Year Built:       {claim["year_built"] or "_________________________"}',
        f'Square Footage:   {claim["property_sqft"] or "_________________________"} sq ft',
        f'Floors:           {claim["num_floors"] or "_________________________"}',
        f'Flood Zone:       {claim["flood_zone"] or "_________________________"}',
        f'FIRM Panel:       {claim["fema_map_number"] or "_________________________"}',
        '',
        'WATER/DAMAGE DETAILS',
        '-' * 40,
        f'Water Source:     {claim["flood_source"] or "Flood"}',
        f'Water Category:   Category {claim["water_category"] or "3"}',
        f'Water Class:      Class {claim["water_class"] or "_"}',
        f'Depth (in):       {claim["water_depth_in"] or "_"}',
        f'Date Removed:     {claim["date_water_removed"] or "_________________________"}',
        '',
        'ROOM-BY-ROOM DAMAGE',
        '-' * 40,
    ]
    for rd in room_data:
        room  = rd['room']
        items = rd['line_items']
        lines.append(f'\nRoom: {room["name"]}  (Subtotal: ${room["subtotal"]:,.2f})')
        lines.append(f'  {"Description":<42} {"Qty":>6} {"Unit":<8} {"Unit $":>9} {"Total":>10}')
        lines.append('  ' + '-' * 78)
        for item in items:
            lines.append(f'  {item["description"]:<42} {item["quantity"]:>6} '
                         f'{item["unit"]:<8} {item["unit_cost"]:>9.2f} {item["total"]:>10.2f}')
    lines += [
        '',
        '=' * 60,
        f'TOTAL BUILDING ESTIMATE:  ${claim["total_estimate"]:,.2f}',
        '=' * 60,
    ]
    return '\n'.join(lines)


def _build_xactimate_esx(claim, rooms_data):
    """Build Xactimate-compatible ESX (XML) content."""
    now = datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<XactimateEstimate version="1.0" xmlns="http://www.xactware.com/xactimate">',
        '  <Header>',
        f'    <FileType>ESX</FileType>',
        f'    <CreatedBy>FloodClaims Pro</CreatedBy>',
        f'    <ExportDate>{now}</ExportDate>',
        '  </Header>',
        '  <ClaimInfo>',
        f'    <ClaimNumber>{claim["claim_number"]}</ClaimNumber>',
        f'    <TypeOfLoss>Flood</TypeOfLoss>',
        f'    <DateOfLoss>{claim["flood_date"]}</DateOfLoss>',
        f'    <InsuredName>{claim["client_name"]}</InsuredName>',
        f'    <InsuredPhone>{claim["client_phone"] or ""}</InsuredPhone>',
        f'    <InsuredEmail>{claim["client_email"] or ""}</InsuredEmail>',
        f'    <LossAddress>{claim["property_address"]}</LossAddress>',
        f'    <InsuranceCompany>{claim["insurance_company"] or ""}</InsuranceCompany>',
        f'    <PolicyNumber>{claim["policy_number"] or ""}</PolicyNumber>',
        f'    <PolicyType>{claim["policy_type"] or ""}</PolicyType>',
        f'    <CoverageBuilding>{claim["coverage_building"]:.2f}</CoverageBuilding>',
        f'    <CoverageContents>{claim["coverage_contents"]:.2f}</CoverageContents>',
        f'    <Deductible>{claim["deductible"]:.2f}</Deductible>',
        f'    <FloodZone>{claim["flood_zone"] or ""}</FloodZone>',
        f'    <FIRMPanelNumber>{claim["fema_map_number"] or ""}</FIRMPanelNumber>',
        f'    <WaterCategory>{claim["water_category"] or ""}</WaterCategory>',
        f'    <WaterClass>{claim["water_class"] or ""}</WaterClass>',
        f'    <WaterDepthInches>{claim["water_depth_in"] or ""}</WaterDepthInches>',
        f'    <MortgageCompany>{claim["mortgage_company"] or ""}</MortgageCompany>',
        f'    <MortgageLoanNumber>{claim["mortgage_loan_number"] or ""}</MortgageLoanNumber>',
        f'    <AdjusterName>{claim["adjuster_name"] or ""}</AdjusterName>',
        f'    <TotalEstimate>{claim["total_estimate"]:.2f}</TotalEstimate>',
        '  </ClaimInfo>',
        '  <Rooms>',
    ]
    for rd in rooms_data:
        room  = rd['room']
        items = rd['line_items']
        lines += [
            '    <Room>',
            f'      <Name>{room["name"]}</Name>',
            f'      <Description>{room["description"] or ""}</Description>',
            f'      <Subtotal>{room["subtotal"]:.2f}</Subtotal>',
            '      <LineItems>',
        ]
        for item in items:
            lines += [
                '        <LineItem>',
                f'          <Description>{item["description"]}</Description>',
                f'          <Quantity>{item["quantity"]}</Quantity>',
                f'          <Unit>{item["unit"]}</Unit>',
                f'          <UnitCost>{item["unit_cost"]:.2f}</UnitCost>',
                f'          <Total>{item["total"]:.2f}</Total>',
                '        </LineItem>',
            ]
        lines += ['      </LineItems>', '    </Room>']
    lines += ['  </Rooms>', '</XactimateEstimate>']
    return '\n'.join(lines)


def _build_photo_manifest(claim, room_data, unassigned):
    """Build a plain-text photo manifest listing all photos and their rooms."""
    lines = [
        f'PHOTO MANIFEST',
        f'Claim: {claim["claim_number"]} — {claim["client_name"]}',
        f'Property: {claim["property_address"]}',
        f'Generated: {datetime.datetime.now().strftime("%B %d, %Y %I:%M %p")}',
        '=' * 60, ''
    ]
    total = 0
    for rd in room_data:
        photos = rd.get('room_photos', [])
        if photos:
            lines.append(f'Room: {rd["room"]["name"]} ({len(photos)} photos)')
            for p in photos:
                lines.append(f'  - {p["filename"]}  |  {p["caption"] or "No caption"}')
                if p['ai_description']:
                    lines.append(f'    AI: {p["ai_description"][:120]}')
            total += len(photos)
    if unassigned:
        lines.append(f'Unassigned Photos ({len(unassigned)})')
        for p in unassigned:
            lines.append(f'  - {p["filename"]}  |  {p["caption"] or "No caption"}')
        total += len(unassigned)
    lines += ['', '=' * 60, f'Total Photos: {total}']
    return '\n'.join(lines)


@app.route('/claims/<int:claim_id>/submit')
@login_required
def submit_package_page(claim_id):
    """Show the Submit Package page for a claim."""
    db    = get_db()
    claim = db.execute('''SELECT c.*, u.name as adjuster_name, u.email as adjuster_email
        FROM claims c LEFT JOIN users u ON c.adjuster_id=u.id WHERE c.id=?''', (claim_id,)).fetchone()
    if not claim:
        flash('Claim not found.', 'error')
        return redirect(url_for('dashboard'))
    rooms = db.execute('SELECT * FROM rooms WHERE claim_id=? AND deleted_at IS NULL ORDER BY id', (claim_id,)).fetchall()
    room_data = []
    photo_count = 0
    for room in rooms:
        items  = db.execute('SELECT * FROM line_items WHERE room_id=? ORDER BY id', (room['id'],)).fetchall()
        photos = db.execute('SELECT * FROM photos WHERE room_id=? AND deleted_at IS NULL ORDER BY id', (room['id'],)).fetchall()
        room_data.append({'room': room, 'line_items': items, 'room_photos': photos})
        photo_count += len(photos)
    unassigned = db.execute('SELECT * FROM photos WHERE claim_id=? AND room_id IS NULL AND deleted_at IS NULL', (claim_id,)).fetchall()
    photo_count += len(unassigned)
    recalc_claim(claim_id)
    claim = db.execute('''SELECT c.*, u.name as adjuster_name, u.email as adjuster_email
        FROM claims c LEFT JOIN users u ON c.adjuster_id=u.id WHERE c.id=?''', (claim_id,)).fetchone()

    # Compliance score
    photos_all = db.execute('SELECT id FROM photos WHERE claim_id=?', (claim_id,)).fetchall()
    checks = {
        'policy':       bool(claim['policy_number']),
        'flood_zone':   bool(claim['flood_zone'] and claim['flood_zone'] != 'Unknown'),
        'photos':       len(photos_all) >= 3,
        'rooms':        len(rooms) >= 1,
        'estimate':     bool(claim['total_estimate']),
        'flood_date':   bool(claim['flood_date']),
        'water_cat':    bool(claim['water_category']),
        'insurance':    bool(claim['insurance_company']),
    }
    compliance_pct = round(sum(checks.values()) / len(checks) * 100)

    CARRIERS = [
        {'id': 'generic',     'name': 'Generic / Any Carrier',     'icon': '📦', 'desc': 'ZIP with all documents + photos'},
        {'id': 'wright',      'name': 'Wright Flood',               'icon': '🏗️', 'desc': 'Wright Flood adjuster package format'},
        {'id': 'nfip_direct', 'name': 'NFIP Direct (FEMA)',         'icon': '🏙️', 'desc': 'NFIP Direct claim submission package'},
        {'id': 'allstate',    'name': 'Allstate',                   'icon': '🛡️', 'desc': 'Allstate adjuster submission package'},
        {'id': 'statefarm',   'name': 'State Farm',                 'icon': '🧱', 'desc': 'State Farm flood claim package'},
        {'id': 'assurant',    'name': 'Assurant',                   'icon': '📊', 'desc': 'Assurant flood claim package'},
        {'id': 'nationwide',  'name': 'Nationwide',                 'icon': '🏦', 'desc': 'Nationwide flood claim package'},
        {'id': 'xactanalysis','name': 'XactAnalysis (Xactimate)',   'icon': '📝', 'desc': 'ESX estimate + supporting docs for XactAnalysis'},
    ]
    # 60-day Proof of Loss deadline
    flood_date_deadline = None
    if claim['flood_date']:
        try:
            dl = datetime.datetime.strptime(claim['flood_date'], '%Y-%m-%d') + datetime.timedelta(days=60)
            flood_date_deadline = dl.strftime('%B %d, %Y')
        except Exception:
            pass

    return render_template('submit_package.html', claim=claim, room_data=room_data,
                           unassigned=unassigned, photo_count=photo_count,
                           compliance_pct=compliance_pct, checks=checks,
                           carriers=CARRIERS, flood_date_deadline=flood_date_deadline)


@app.route('/claims/<int:claim_id>/submit/download', methods=['POST'])
@login_required
@csrf_required
def submit_package_download(claim_id):
    """Generate and download the submission ZIP package."""
    db      = get_db()
    carrier = request.form.get('carrier', 'generic')
    include_photos = request.form.get('include_photos', 'yes') == 'yes'

    claim = db.execute('''SELECT c.*, u.name as adjuster_name, u.email as adjuster_email
        FROM claims c LEFT JOIN users u ON c.adjuster_id=u.id WHERE c.id=?''', (claim_id,)).fetchone()
    if not claim:
        flash('Claim not found.', 'error')
        return redirect(url_for('dashboard'))
    rooms = db.execute('SELECT * FROM rooms WHERE claim_id=? AND deleted_at IS NULL ORDER BY id', (claim_id,)).fetchall()
    room_data = []
    for room in rooms:
        items  = db.execute('SELECT * FROM line_items WHERE room_id=? ORDER BY id', (room['id'],)).fetchall()
        photos = db.execute('SELECT * FROM photos WHERE room_id=? AND deleted_at IS NULL ORDER BY id', (room['id'],)).fetchall()
        room_data.append({'room': room, 'line_items': items, 'room_photos': photos})
    unassigned = db.execute('SELECT * FROM photos WHERE claim_id=? AND room_id IS NULL AND deleted_at IS NULL', (claim_id,)).fetchall()
    recalc_claim(claim_id)
    claim = db.execute('''SELECT c.*, u.name as adjuster_name, u.email as adjuster_email
        FROM claims c LEFT JOIN users u ON c.adjuster_id=u.id WHERE c.id=?''', (claim_id,)).fetchone()

    cn = claim['claim_number']
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:

        # 1. Proof of Loss
        pol_text = _build_proof_of_loss_text(claim, rooms, room_data)
        zf.writestr(f'{cn}/01_Proof_of_Loss_{cn}.txt', pol_text)

        # 2. Building Worksheet
        bw_text = _build_building_worksheet_text(claim, room_data)
        zf.writestr(f'{cn}/02_Building_Worksheet_{cn}.txt', bw_text)

        # 3. Xactimate ESX estimate
        esx_content = _build_xactimate_esx(claim, room_data)
        zf.writestr(f'{cn}/03_Estimate_{cn}.esx', esx_content)

        # 4. Photo manifest
        manifest = _build_photo_manifest(claim, room_data, unassigned)
        zf.writestr(f'{cn}/04_Photo_Manifest_{cn}.txt', manifest)

        # 5. Claim summary JSON (for portal APIs)
        summary = {
            'claim_number':      claim['claim_number'],
            'client_name':       claim['client_name'],
            'client_phone':      claim['client_phone'],
            'client_email':      claim['client_email'],
            'property_address':  claim['property_address'],
            'flood_date':        claim['flood_date'],
            'flood_source':      claim['flood_source'],
            'water_category':    claim['water_category'],
            'water_class':       claim['water_class'],
            'water_depth_in':    claim['water_depth_in'],
            'insurance_company': claim['insurance_company'],
            'policy_number':     claim['policy_number'],
            'policy_type':       claim['policy_type'],
            'coverage_building': claim['coverage_building'],
            'coverage_contents': claim['coverage_contents'],
            'deductible':        claim['deductible'],
            'flood_zone':        claim['flood_zone'],
            'fema_map_number':   claim['fema_map_number'],
            'total_estimate':    claim['total_estimate'],
            'net_claim':         max(0, claim['total_estimate'] - claim['deductible']),
            'adjuster_name':     claim['adjuster_name'],
            'adjuster_email':    claim['adjuster_email'],
            'status':            claim['status'],
            'generated':         datetime.datetime.now().isoformat(),
            'carrier_format':    carrier,
            'rooms': [
                {
                    'name':     rd['room']['name'],
                    'subtotal': rd['room']['subtotal'],
                    'items': [
                        {'description': i['description'], 'quantity': i['quantity'],
                         'unit': i['unit'], 'unit_cost': i['unit_cost'], 'total': i['total']}
                        for i in rd['line_items']
                    ]
                } for rd in room_data
            ]
        }
        zf.writestr(f'{cn}/05_Claim_Summary_{cn}.json', json.dumps(summary, indent=2))

        # 6. README with carrier-specific instructions
        carrier_instructions = {
            'generic':      'Upload to your carrier\'s adjuster portal. Include all files.',
            'wright':       'Upload to Wright Flood adjuster portal at wrightflood.com/claims\n'
                            'Required: Proof of Loss, ESX estimate, all photos.\n'
                            'Submit within 60 days of date of loss.',
            'nfip_direct':  'Submit to NFIP Direct via the FEMA adjuster portal.\n'
                            'Email: FEMA-NFIPFROMailbox@fema.dhs.gov\n'
                            'Required: Signed Proof of Loss, Building Worksheet, photos, ESX.',
            'allstate':     'Upload via Allstate Business Insurance adjuster portal.\n'
                            'Required: ESX estimate, Proof of Loss, photos.',
            'statefarm':    'Submit via State Farm Claims portal or email to your assigned examiner.\n'
                            'Required: ESX estimate, signed Proof of Loss, all damage photos.',
            'assurant':     'Submit via Assurant adjuster portal at assurant.com/claims\n'
                            'Required: ESX estimate, Proof of Loss, Building Worksheet, photos.',
            'nationwide':   'Submit via Nationwide adjuster portal.\n'
                            'Required: Signed Proof of Loss, ESX estimate, photos.',
            'xactanalysis': 'Upload your ESX file directly to XactAnalysis at xactanalysis.com\n'
                            'File: 03_Estimate_{cn}.esx\n'
                            'Attach remaining documents as supporting files in XactAnalysis.',
        }
        readme = f'''FLOODCLAIM PRO — SUBMISSION PACKAGE
{'=' * 60}
Claim:    {cn}
Client:   {claim['client_name']}
Property: {claim['property_address']}
Carrier:  {carrier.upper()}
Generated: {datetime.datetime.now().strftime('%B %d, %Y %I:%M %p')}

FILES IN THIS PACKAGE
{'-' * 40}
01_Proof_of_Loss_{cn}.txt         — Signed Proof of Loss (print + sign)
02_Building_Worksheet_{cn}.txt    — NFIP Building Property Worksheet
03_Estimate_{cn}.esx              — Xactimate-compatible estimate
04_Photo_Manifest_{cn}.txt        — Photo index with AI descriptions
05_Claim_Summary_{cn}.json        — Machine-readable claim data
Photos/                           — All damage photos organized by room

SUBMISSION INSTRUCTIONS
{'-' * 40}
{carrier_instructions.get(carrier, carrier_instructions['generic'])}

IMPORTANT DEADLINES
{'-' * 40}
• File Proof of Loss within 60 days of date of loss ({claim['flood_date']})
• Deadline: {(datetime.datetime.strptime(claim['flood_date'], '%Y-%m-%d') + datetime.timedelta(days=60)).strftime('%B %d, %Y') if claim['flood_date'] else 'Check policy'}
• Keep copies of all submitted documents
• Note your claim number: {cn}

Generated by FloodClaims Pro — Professional Flood Damage Assessment
'''
        zf.writestr(f'{cn}/README_{carrier.upper()}.txt', readme)

        # 7. Photos (organized by room folder)
        if include_photos:
            for rd in room_data:
                room_name = rd['room']['name'].replace('/', '-').replace(' ', '_')
                for photo in rd['room_photos']:
                    path = os.path.join(UPLOAD_DIR, photo['filename'])
                    if os.path.exists(path):
                        zf.write(path, f'{cn}/Photos/{room_name}/{photo["filename"]}')
            for photo in unassigned:
                path = os.path.join(UPLOAD_DIR, photo['filename'])
                if os.path.exists(path):
                    zf.write(path, f'{cn}/Photos/General/{photo["filename"]}')

    buf.seek(0)
    _log_activity(claim_id, f'Submission package downloaded (carrier: {carrier})')
    resp = make_response(buf.read())
    resp.headers['Content-Type']        = 'application/zip'
    resp.headers['Content-Disposition'] = f'attachment; filename="{cn}-{carrier}-submission.zip"'
    return resp


@app.route('/sales')
def sales_page():
    """Hidden sales/pitch page — no login required, no nav link.
    Remove this route when Billy buys."""
    return render_template('sales.html')

@app.route('/health')
def health():
    return jsonify({'status': 'ok'})

@app.route('/ready')
def ready():
    """Readiness probe — checks DB connectivity."""
    try:
        db = get_db()
        db.execute('SELECT 1 FROM claims LIMIT 1')
        return jsonify({'ready': True, 'db': 'ok'})
    except Exception as e:
        return jsonify({'ready': False, 'db': str(e)}), 503

# ── Phase 3: Standardized /api/status ───────────────────────────────────
@app.route('/api/status', methods=['GET'])
def api_status():
    """Simple status endpoint — no rate limiting, no complex queries."""
    return jsonify({
        'app': 'FloodClaims Pro',
        'version': '1.0',
        'status': 'ok',
    })

# ── Claims List (redirect to dashboard) ──────────────────────────────────
@app.route('/claims', methods=['GET'])
def claims_list():
    """Claims list — redirects to dashboard which shows claims."""
    return redirect(url_for('dashboard'))

# ── Phase 3: Cross-app — Pet Vet AI photo analysis ──────────────────────────
    """Phase 3: Ask Pet Vet AI to analyze a damage photo via the app network.
    Falls back to local OpenRouter analysis if network call fails.
    """
    try:
        with open(image_path, 'rb') as f:
            img_b64 = base64.b64encode(f.read()).decode()
        ext  = image_path.rsplit('.', 1)[-1].lower()
        mime = f'image/{ext}' if ext != 'jpg' else 'image/jpeg'

        result = _call_pet_vet_ai('/api/analyze-damage',
                                  data={'image_b64': img_b64, 'mime_type': mime,
                                        'context': 'flood damage'})
        if result and result.get('success'):
            analysis = result.get('analysis', {})
            if isinstance(analysis, dict):
                return analysis.get('description') or analysis.get('diagnosis', '')
            return str(analysis)
    except Exception:
        pass
    # Fallback to local
    return ai_describe_photo(image_path)

# ── PHASE 1 BACKEND: Batch Photo Analysis — Added by OWL ──────────────────────

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


def _get_vision_model():
    """Return a vision-capable model for batch photo analysis."""
    configured = get_setting('ai_vision_model', '')
    if configured:
        return configured
    # Default: use OpenRouter auto-router for vision
    return 'openrouter/auto'


def _analyze_photo_vision(image_paths, claim=None, room_id=None):
    """Send multiple photos to a vision-capable AI model for flood damage analysis.
    Returns structured JSON with detected items, water category/class, high-value flags.
    """
    key = OPENROUTER_KEY
    if not key:
        return {'error': 'no_api_key', 'items': []}

    model = _get_vision_model()

    # Build the content array with all images
    content = [
        {
            'type': 'text',
            'text': (
                'You are a certified flood damage assessor. Analyze the following photos of a room '
                'affected by flood damage. For each visible item:\n'
                '1. Identify the item and its material\n'
                '2. Rate damage severity (1-5): 1=minor cosmetic, 5=total loss\n'
                '3. Estimate replacement cost: low/medium/high/luxury\n'
                '4. Flag high-value items (jewelry, designer, electronics >$500)\n'
                '5. Determine water category: Category 1 (clean), 2 (gray), 3 (black)\n'
                '6. Determine water class: Class 1 (minimal), 2 (significant), 3 (gross), 4 (extreme)\n'
                'Respond ONLY with valid JSON: '
                '{"items": [{"name": "...", "material": "...", "severity": N, "cost": "...", "high_value": true/false}], '
                '"water_category": "1|2|3", "water_class": "1|2|3|4", '
                '"notes": "brief summary"}'
            )
        }
    ]

    for img_path in image_paths:
        try:
            with open(img_path, 'rb') as f:
                img_b64 = base64.b64encode(f.read()).decode()
            ext = img_path.rsplit('.', 1)[-1].lower()
            mime = f'image/{ext}' if ext != 'jpg' else 'image/jpeg'
            content.append({
                'type': 'image_url',
                'image_url': {'url': f'data:{mime};base64,{img_b64}', 'detail': 'high'}
            })
        except Exception as e:
            print(f'Error loading image {img_path}: {e}')

    try:
        result = call_openrouter(
            messages=[{'role': 'user', 'content': content}],
            model=model,
            key=key,
            max_tokens=4000
        )
        if result.startswith('Error:'):
            return {'error': result, 'items': []}
        if result.startswith('[Used fallback:'):
            result = result.split(']\n\n', 1)[-1] if ']\n\n' in result else result

        try:
            parsed = json.loads(result)
            return parsed
        except json.JSONDecodeError:
            # Try to extract JSON from the response
            import re
            json_match = re.search(r'\{.*\}', result, re.DOTALL)
            if json_match:
                return json.loads(json_match.group())
            return {'items': [], 'raw_text': result}
    except Exception as e:
        print(f'Vision analysis error: {e}')
        return {'error': str(e), 'items': []}


@app.route('/claim/<int:claim_id>/room/<int:room_id>/batch-analyze', methods=['POST'])
@login_required
def batch_analyze_claim_room(claim_id, room_id):
    """Analyze a batch of uploaded photos for a specific room."""
    if 'user_id' not in session:
        return jsonify({'error': 'auth_required'}), 401

    # Verify claim access
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    claim = db.execute('SELECT * FROM claims WHERE id=?', (claim_id,)).fetchone()
    if not claim:
        db.close()
        return jsonify({'error': 'claim_not_found'}), 404

    # Get uploaded files
    photos = request.files.getlist('photos')
    if not photos:
        db.close()
        return jsonify({'error': 'no_photos'}), 400

    # Save photos and collect paths (with size check + compression)
    upload_dir = os.path.join(app.config.get('UPLOAD_FOLDER', 'static/uploads'), str(claim_id))
    os.makedirs(upload_dir, exist_ok=True)
    saved_paths = []
    _batch_errors = []

    for i, photo in enumerate(photos):
        if not photo or not photo.filename:
            continue
        # Size check (10MB per file)
        photo.seek(0, 2)
        psize = photo.tell()
        photo.seek(0)
        if psize > 10 * 1024 * 1024:
            _batch_errors.append(f'{photo.filename}: too large (>10MB)')
            continue
        _ext = photo.filename.rsplit('.', 1)[1].lower() if '.' in photo.filename else 'jpg'
        if _ext not in ('png', 'jpg', 'jpeg', 'gif', 'webp'):
            _ext = 'jpg'
        filename = f'batch_{room_id}_{int(datetime.datetime.now().timestamp())}_{i}_{secure_filename(photo.filename.rsplit(".", 1)[0])}.{_ext}'
        filepath = os.path.join(upload_dir, filename)
        # Auto-compress
        try:
            from PIL import Image as _PIL
            img = _PIL.Image.open(photo)
            if max(img.size) > 2048:
                sc = 2048 / max(img.size)
                img = img.resize((int(img.size[0]*sc), int(img.size[1]*sc)), _PIL.LANCZOS)
            if img.mode == 'RGBA' and _ext in ('jpg', 'jpeg'):
                img = img.convert('RGB')
            _sk = {'optimize': True, 'quality': 80} if _ext in ('jpg', 'jpeg') else {'optimize': True}
            img.save(filepath, **_sk)
        except Exception:
            photo.save(filepath)

        # Insert photo record
        db.execute(
            'INSERT INTO photos (claim_id, room_id, filename, ai_description, customer_submitted) VALUES (?, ?, ?, ?, ?)',
            (claim_id, room_id, filename, '', 0))
        saved_paths.append(filepath)

    db.commit()

    # Run vision analysis
    analysis = _analyze_photo_vision(saved_paths, claim=claim, room_id=room_id)

    # Store results on the last photo
    if saved_paths:
        last_photo = db.execute(
            'SELECT id FROM photos WHERE claim_id=? AND room_id=? ORDER BY id DESC LIMIT 1',
            (claim_id, room_id)
        ).fetchone()
        if last_photo:
            db.execute(
                'UPDATE photos SET ai_raw_json=?, detected_items=?, is_high_value=?, '
                'water_category=?, water_class=?, analysis_status=? WHERE id=?',
                (
                    json.dumps(analysis),
                    json.dumps(analysis.get('items', [])),
                    1 if any(item.get('high_value') for item in analysis.get('items', [])) else 0,
                    analysis.get('water_category', ''),
                    analysis.get('water_class', ''),
                    'completed',
                    last_photo['id']
                )
            )
            db.commit()

    db.close()

    return jsonify({
        'success': True,
        'analysis': analysis,
        'photos_processed': len(saved_paths),
        'errors': _batch_errors if _batch_errors else None
    })


@app.route('/claim/<int:claim_id>/ai-populate', methods=['POST'])
@login_required
def ai_populate_claim(claim_id):
    """Auto-populate claim line items from batch analysis results."""
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    claim = db.execute('SELECT * FROM claims WHERE id=?', (claim_id,)).fetchone()
    if not claim:
        db.close()
        return jsonify({'error': 'claim_not_found'}), 404

    # Get all analyzed photos for this claim
    photos = db.execute(
        'SELECT * FROM photos WHERE claim_id=? AND analysis_status="completed" AND detected_items!=""',
        (claim_id,)
    ).fetchall()

    items_added = []
    for photo in photos:
        try:
            items = json.loads(photo['detected_items'])
            for item in items:
                name = item.get('name', 'Unknown item')
                severity = item.get('severity', 3)
                cost_tier = item.get('cost', 'medium')

                # Estimate price based on cost tier
                price_map = {'low': 50, 'medium': 150, 'high': 500, 'luxury': 2000}
                unit_price = price_map.get(cost_tier, 150)
                qty = 1

                db.execute(
                    'INSERT INTO line_items (claim_id, room_id, description, quantity, unit_price, total, source) '
                    'VALUES (?, ?, ?, ?, ?, ?, ?)',
                    (claim_id, photo['room_id'], f"[AI] {name} (severity {severity}/5)", qty, unit_price, unit_price * qty, 'ai_batch')
                )
                items_added.append(name)
        except (json.JSONDecodeError, KeyError) as e:
            continue

    db.commit()
    recalc_claim(claim_id)
    db.close()

    return jsonify({
        'success': True,
        'items_added': len(items_added),
        'items': items_added
    })


@app.route('/customer/upload/<token>', methods=['GET'])
def customer_upload_page(token):
    """Render the customer upload page (no auth required)."""
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    portal = db.execute(
        'SELECT * FROM client_portal_tokens WHERE token=? AND used=0',
        (token,)
    ).fetchone()
    db.close()
    if not portal:
        return render_template('portal_invalid.html'), 403
    return render_template('customer_upload.html', token=token)


@app.route('/customer/upload/<token>', methods=['POST'])
def customer_upload_photos(token):
    """Customer uploads photos via SMS link. No login required."""
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row

    # Validate token
    portal = db.execute(
        'SELECT * FROM client_portal_tokens WHERE token=? AND used=0',
        (token,)
    ).fetchone()
    if not portal:
        db.close()
        return jsonify({'error': 'invalid_or_expired_token'}), 403

    claim_id = portal['claim_id']
    photos = request.files.getlist('photos')

    if not photos:
        db.close()
        return jsonify({'error': 'no_photos'}), 400

    upload_dir = os.path.join(app.config.get('UPLOAD_FOLDER', 'static/uploads'), str(claim_id))
    os.makedirs(upload_dir, exist_ok=True)

    saved_count = 0
    for i, photo in enumerate(photos):
        if photo and photo.filename:
            filename = f'customer_{token[:8]}_{int(datetime.datetime.now().timestamp())}_{i}_{secure_filename(photo.filename)}'
            filepath = os.path.join(upload_dir, filename)
            photo.save(filepath)
            db.execute(
                'INSERT INTO photos (claim_id, filename, ai_description, customer_submitted, analysis_status) '
                'VALUES (?, ?, ?, ?, ?)',
                (claim_id, filename, '', 1, 'pending')
            )
            saved_count += 1

    # Mark token as used
    db.execute('UPDATE client_portal_tokens SET used=1 WHERE token=?', (token,))
    db.commit()
    db.close()

    return jsonify({
        'success': True,
        'photos_uploaded': saved_count,
        'message': f'{saved_count} photos uploaded successfully'
    })


@app.route('/claim/<int:claim_id>/generate-upload-link', methods=['POST'])
@login_required
def generate_upload_link(claim_id):
    """Generate a customer upload link (token + SMS)."""
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    claim = db.execute('SELECT * FROM claims WHERE id=?', (claim_id,)).fetchone()
    if not claim:
        db.close()
        return jsonify({'error': 'claim_not_found'}), 404

    # Generate unique token
    token = secrets.token_urlsafe(32)
    db.execute(
        'INSERT INTO client_portal_tokens (claim_id, token, used, created_at) VALUES (?, ?, 0, ?)',
        (claim_id, token, datetime.datetime.now().isoformat())
    )
    db.commit()
    db.close()

    upload_url = url_for('customer_upload_photos', token=token, _external=True)

    return jsonify({
        'success': True,
        'upload_url': upload_url,
        'token': token,
        'sms_message': f'FloodClaims Pro: Upload photos of your damage here: {upload_url}'
    })


# ═══════════════════════════════════════════════════════════════════════════
# TRAINING CLASSES — LMS System ($50/class, Stripe, certificates)
# ═══════════════════════════════════════════════════════════════════════════

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


@app.route('/training')
@login_required
def training_classes():
    """Browse all available training classes."""
    db = get_db()
    classes = db.execute('''
        SELECT tc.*,
               COUNT(DISTINCT tl.id) AS num_lessons,
               COUNT(DISTINCT te.id) AS num_enrolled
        FROM training_classes tc
        LEFT JOIN training_lessons tl ON tl.class_id = tc.id
        LEFT JOIN training_enrollments te ON te.class_id = tc.id AND te.payment_status='completed'
        WHERE tc.status = 'active'
        GROUP BY tc.id
        ORDER BY tc.created_at DESC
    ''').fetchall()
    return render_template('training_classes.html', classes=classes)


@app.route('/training/<int:class_id>/enroll', methods=['POST'])
@login_required
@csrf_required
def enroll_class(class_id):
    """Enroll in a training class — free, no payment required."""
    db = get_db()
    tc = db.execute('SELECT * FROM training_classes WHERE id=? AND status=?', (class_id, 'active')).fetchone()
    if not tc:
        flash('Training class not found.', 'error')
        return redirect(url_for('training_classes'))
    # Check if already enrolled
    existing = db.execute('SELECT * FROM training_enrollments WHERE user_id=? AND class_id=?',
                          (session['user_id'], class_id)).fetchone()
    if existing:
        flash('You are already enrolled in this class.', 'info')
        return redirect(url_for('training_learn', enroll_id=existing['id']))
    # Free enrollment — no payment needed
    db.execute('''INSERT OR REPLACE INTO training_enrollments (user_id, class_id, payment_status)
                  VALUES (?,?,?)''',
               (session['user_id'], class_id, 'completed'))
    db.commit()
    flash('🎉 Enrolled! Start learning now.', 'success')
    return redirect(url_for('training_learn', enroll_id=db.execute('SELECT last_insert_rowid()').fetchone()[0]))


@app.route('/training/<int:enroll_id>/learn')
@login_required
def training_learn(enroll_id):
    """View and progress through enrolled training class."""
    db = get_db()
    enrollment = db.execute('''
        SELECT te.*, tc.title AS class_title, tc.description AS class_desc
        FROM training_enrollments te
        JOIN training_classes tc ON tc.id = te.class_id
        WHERE te.id=? AND te.user_id=?
    ''', (enroll_id, session['user_id'])).fetchone()
    if not enrollment:
        flash('Enrollment not found.', 'error')
        return redirect(url_for('training_classes'))
    lessons = db.execute('SELECT * FROM training_lessons WHERE class_id=? ORDER BY lesson_order', (enrollment['class_id'],)).fetchall()
    progress = db.execute('SELECT lesson_id, completed FROM training_progress WHERE enrollment_id=?', (enroll_id,)).fetchall()
    completed_ids = {p['lesson_id'] for p in progress if p['completed']}
    has_exam = db.execute('SELECT COUNT(*) FROM training_exam_questions WHERE class_id=?', (enrollment['class_id'],)).fetchone()[0] > 0
    cert = db.execute('SELECT * FROM training_certificates WHERE enrollment_id=?', (enroll_id,)).fetchone()
    return render_template('training_learn.html', enrollment=enrollment, lessons=lessons,
                           completed_ids=completed_ids, has_exam=has_exam, cert=cert)


@app.route('/training/<int:enroll_id>/lesson/<int:lesson_id>/complete', methods=['POST'])
@login_required
@csrf_required
def complete_lesson(enroll_id, lesson_id):
    """Mark a lesson as completed."""
    db = get_db()
    enrollment = db.execute('SELECT * FROM training_enrollments WHERE id=? AND user_id=?',
                            (enroll_id, session['user_id'])).fetchone()
    if not enrollment:
        return jsonify({'error': 'not_enrolled'}), 403
    db.execute('''INSERT INTO training_progress (enrollment_id, lesson_id, completed, completed_at)
                  VALUES (?,?,1,?)
                  ON CONFLICT(enrollment_id,lesson_id) DO UPDATE SET completed=1, completed_at=excluded.completed_at''',
               (enroll_id, lesson_id, datetime.datetime.now().isoformat()))
    # Recalculate progress
    total = db.execute('SELECT COUNT(*) FROM training_lessons WHERE class_id=?', (enrollment['class_id'],)).fetchone()[0]
    done = db.execute('SELECT COUNT(*) FROM training_progress WHERE enrollment_id=? AND completed=1', (enroll_id,)).fetchone()[0]
    pct = int((done / total) * 100) if total else 0
    db.execute('UPDATE training_enrollments SET progress_pct=? WHERE id=?', (pct, enroll_id))
    db.commit()
    return jsonify({'progress': pct, 'completed': True})


@app.route('/training/<int:enroll_id>/exam')
@login_required
def training_exam(enroll_id):
    """Take the certification exam."""
    db = get_db()
    enrollment = db.execute('''
        SELECT te.*, tc.title AS class_title
        FROM training_enrollments te
        JOIN training_classes tc ON tc.id = te.class_id
        WHERE te.id=? AND te.user_id=? AND te.payment_status='completed'
    ''', (enroll_id, session['user_id'])).fetchone()
    if not enrollment:
        flash('Enrollment not found.', 'error')
        return redirect(url_for('training_classes'))
    questions = db.execute('SELECT * FROM training_exam_questions WHERE class_id=? ORDER BY RANDOM() LIMIT 20',
                           (enrollment['class_id'],)).fetchall()
    import random
    for q in questions:
        options = [q['option_a'], q['option_b'], q['option_c'], q['option_d']]
        random.shuffle(options)
    return render_template('training_exam.html', enrollment=enrollment, questions=questions)


@app.route('/training/<int:enroll_id>/exam/submit', methods=['POST'])
@login_required
@csrf_required
def submit_exam(enroll_id):
    """Submit exam answers and calculate score."""
    db = get_db()
    enrollment = db.execute('SELECT * FROM training_enrollments WHERE id=? AND user_id=?',
                            (enroll_id, session['user_id'])).fetchone()
    if not enrollment:
        return jsonify({'error': 'not_enrolled'}), 403
    questions = db.execute('SELECT * FROM training_exam_questions WHERE class_id=?', (enrollment['class_id'],)).fetchall()
    score = 0
    total = len(questions) or 1
    for q in questions:
        submitted = request.form.get(f'q_{q["id"]}', '')
        if submitted.lower() == q['correct_answer'].lower():
            score += 1
    pct = int((score / total) * 100)
    passed = pct >= 80
    if passed:
        cert_id = secrets.token_hex(16)
        db.execute('''INSERT OR REPLACE INTO training_certificates (user_id, class_id, enrollment_id, score, certificate_id, issued_at)
                      VALUES (?,?,?,?,?,?)''',
                   (session['user_id'], enrollment['class_id'], enroll_id, pct, cert_id, datetime.datetime.now().isoformat()))
        db.execute('UPDATE training_enrollments SET completed_at=?, progress_pct=100 WHERE id=?',
                   (datetime.datetime.now().isoformat(), enroll_id))
        db.commit()
        return jsonify({'passed': True, 'score': pct, 'certificate_id': cert_id})
    db.commit()
    return jsonify({'passed': False, 'score': pct, 'message': 'You need 80% to pass. Review the material and try again.'})


@app.route('/training/certificate/<cert_id>')
@login_required
def view_certificate(cert_id):
    """View a training certificate."""
    db = get_db()
    cert = db.execute('''
        SELECT tc.*, tcl.title AS class_title, u.name AS student_name, u.email AS student_email
        FROM training_certificates tc
        JOIN training_classes tcl ON tcl.id = tc.class_id
        JOIN users u ON u.id = tc.user_id
        WHERE tc.certificate_id=?
    ''', (cert_id,)).fetchone()
    if not cert:
        flash('Certificate not found.', 'error')
        return redirect(url_for('training_classes'))
    return render_template('training_certificate.html', cert=cert)


# ── ADMIN: Training Class Management ───────────────────────────────────────

@app.route('/admin/training')
@login_required
def admin_training():
    """Admin: list all training classes."""
    if session.get('role') not in ('admin', 'manager'):
        abort(403)
    db = get_db()
    classes = db.execute('SELECT tc.*, COUNT(DISTINCT tl.id) AS num_lessons, COUNT(DISTINCT te.id) AS num_enrolled FROM training_classes tc LEFT JOIN training_lessons tl ON tl.class_id = tc.id LEFT JOIN training_enrollments te ON te.class_id = tc.id GROUP BY tc.id ORDER BY tc.created_at DESC').fetchall()
    return render_template('admin/training.html', classes=classes)


@app.route('/admin/training/new', methods=['GET', 'POST'])
@login_required
def admin_new_class():
    """Admin: create a new training class."""
    if session.get('role') not in ('admin', 'manager'):
        abort(403)
    if request.method == 'POST':
        db = get_db()
        db.execute('INSERT INTO training_classes (title, description, price_cents, status) VALUES (?,?,?,?)',
                   (request.form['title'], request.form['description'], int(request.form.get('price_cents', 5000)),
                    request.form.get('status', 'active')))
        class_id = db.execute('SELECT last_insert_rowid()').fetchone()[0]
        _seed_training_questions(db, class_id)
        db.commit()
        flash('✅ Training class created! Default exam questions added.', 'success')
        return redirect(url_for('admin_training'))
    return render_template('admin/training_edit.html', tc=None)


@app.route('/admin/training/<int:class_id>/lessons', methods=['GET', 'POST'])
@login_required
def admin_manage_lessons(class_id):
    """Admin: manage lessons for a class."""
    if session.get('role') not in ('admin', 'manager'):
        abort(403)
    db = get_db()
    tc = db.execute('SELECT * FROM training_classes WHERE id=?', (class_id,)).fetchone()
    if not tc:
        abort(404)
    if request.method == 'POST':
        order = int(request.form.get('lesson_order', 0))
        db.execute('INSERT INTO training_lessons (class_id, title, content, lesson_order, video_url) VALUES (?,?,?,?,?)',
                   (class_id, request.form['title'], request.form.get('content',''), order, request.form.get('video_url','')))
        db.commit()
        flash('Lesson added.', 'success')
        return redirect(url_for('admin_manage_lessons', class_id=class_id))
    lessons = db.execute('SELECT * FROM training_lessons WHERE class_id=? ORDER BY lesson_order', (class_id,)).fetchall()
    return render_template('admin/training_lessons.html', tc=tc, lessons=lessons)


@app.route('/admin/training/<int:class_id>/lesson/<int:lesson_id>/delete', methods=['POST'])
@login_required
def admin_delete_lesson(class_id, lesson_id):
    """Admin: delete a lesson."""
    if session.get('role') not in ('admin', 'manager'):
        abort(403)
    db = get_db()
    db.execute('DELETE FROM training_lessons WHERE id=? AND class_id=?', (lesson_id, class_id))
    db.commit()
    flash('Lesson deleted.', 'success')
    return redirect(url_for('admin_manage_lessons', class_id=class_id))


@app.errorhandler(404)
def not_found(e):
    return render_template('errors/404.html'), 404

@app.errorhandler(403)
def forbidden(e):
    return render_template('errors/403.html'), 403

@app.errorhandler(500)
def server_error(e):
    return render_template('errors/500.html'), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))

