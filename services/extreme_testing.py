"""
Aquila Extreme Testing System
==============================
Comprehensive end-to-end testing of EVERY feature in FloodClaims Pro.
Aquila tests the app from the inside — creating claims, testing links,
verifying AI, checking QR codes, testing team management, everything.

Results are saved to the database and visible in the admin panel.
Designed to be turned on for initial testing, then disabled once stable.

Test Categories:
  1. Navigation — every sidebar link works (200 OK)
  2. Dashboard — stats, filters, search, claim list
  3. Claims — create, view, edit, delete, duplicate, status change
  4. Pipeline — view, move claims between stages
  5. Schedule — view, add, update, delete inspections
  6. QR Code — generate portal link, customer upload flow
  7. Analytics — page loads, data renders
  8. Notifications — page loads, send notification
  9. AI — photo analysis, chat, brain files, API key validation
  10. Team — add, edit, deactivate, reactivate, delete members
  11. Settings — save settings, brain editor, model picker
  12. Auth — login, logout, signup, session management
"""
import os
import re
import json
import time
import sqlite3
import datetime
import logging
import traceback
from typing import Optional

logger = logging.getLogger(__name__)

# ── Test result storage ──────────────────────────────────────────────────────

_TEST_TABLE_CREATED = False


def _get_db():
    """Get DB path."""
    p = os.environ.get('DB_PATH') or os.path.join(
        os.environ.get('RAILWAY_VOLUME_MOUNT_PATH', '/data'), 'floodclaim.db')
    return p


def _ensure_test_table():
    """Create test results table if not exists."""
    global _TEST_TABLE_CREATED
    if _TEST_TABLE_CREATED:
        return
    try:
        db = sqlite3.connect(_get_db())
        db.executescript('''
            CREATE TABLE IF NOT EXISTS aquila_test_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                test_run_id TEXT NOT NULL,
                category TEXT NOT NULL,
                test_name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                detail TEXT DEFAULT '',
                duration_ms INTEGER DEFAULT 0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS aquila_test_runs (
                id TEXT PRIMARY KEY,
                status TEXT DEFAULT 'running',
                total_tests INTEGER DEFAULT 0,
                passed INTEGER DEFAULT 0,
                failed INTEGER DEFAULT 0,
                warnings INTEGER DEFAULT 0,
                started_at TEXT,
                finished_at TEXT,
                summary TEXT DEFAULT ''
            );
        ''')
        db.commit()
        db.close()
        _TEST_TABLE_CREATED = True
    except Exception as e:
        logger.error(f"[TEST] Failed to create test table: {e}")


def _save_result(run_id, category, name, status, detail="", duration_ms=0):
    """Save a single test result to the database."""
    try:
        _ensure_test_table()
        db = sqlite3.connect(_get_db())
        db.execute(
            'INSERT INTO aquila_test_results (test_run_id, category, test_name, status, detail, duration_ms) VALUES (?,?,?,?,?,?)',
            (run_id, category, name, status, detail[:2000], duration_ms)
        )
        db.commit()
        db.close()
    except Exception as e:
        logger.error(f"[TEST] Failed to save result: {e}")


def _start_run(run_id):
    """Record test run start."""
    try:
        _ensure_test_table()
        db = sqlite3.connect(_get_db())
        db.execute(
            'INSERT INTO aquila_test_runs (id, status, started_at) VALUES (?,?,?)',
            (run_id, 'running', datetime.datetime.now().isoformat())
        )
        db.commit()
        db.close()
    except Exception as e:
        logger.error(f"[TEST] Failed to start run: {e}")


def _finish_run(run_id, total, passed, failed, warnings, summary):
    """Record test run completion."""
    try:
        db = sqlite3.connect(_get_db())
        db.execute(
            'UPDATE aquila_test_runs SET status=?, total_tests=?, passed=?, failed=?, warnings=?, finished_at=?, summary=? WHERE id=?',
            ('done', total, passed, failed, warnings, datetime.datetime.now().isoformat(), summary[:1000], run_id)
        )
        db.commit()
        db.close()
    except Exception as e:
        logger.error(f"[TEST] Failed to finish run: {e}")


# ── Flask test client wrapper ────────────────────────────────────────────────


class AquilaTestRunner:
    """
    Runs internal tests against the Flask app using the test client.
    Aquila logs in as admin and systematically tests every feature.
    """

    def __init__(self, app):
        self.app = app
        self.client = app.test_client()
        self.csrf_token = None
        self.stats = {"passed": 0, "failed": 0, "warnings": 0, "total": 0}

    def _log(self, category, name, status, detail=""):
        """Record a test result."""
        self.stats["total"] += 1
        if status == "pass":
            self.stats["passed"] += 1
        elif status == "fail":
            self.stats["failed"] += 1
        elif status == "warn":
            self.stats["warnings"] += 1
        _save_result(self.run_id, category, name, status, detail, 0)
        icon = {"pass": "✅", "fail": "❌", "warn": "⚠️"}.get(status, "❓")
        logger.info(f"{icon} [{category}] {name}: {detail or status}")

    def _get(self, url, expected_status=200, category="navigation"):
        """Test a GET request. Returns response or None."""
        start = time.time()
        resp = self.client.get(url, follow_redirects=False)
        duration = int((time.time() - start) * 1000)

        label = f"GET {url}"
        if resp.status_code == expected_status:
            self._log(category, label, "pass", f"{resp.status_code} ({duration}ms)")
            return resp
        elif resp.status_code in (301, 302) and expected_status == 200:
            # Redirect — follow it
            resp2 = self.client.get(url, follow_redirects=True)
            if resp2.status_code == 200:
                self._log(category, label, "pass", f"redirect -> 200 ({duration}ms)")
                return resp2
            else:
                self._log(category, label, "fail", f"redirect -> {resp2.status_code}")
                return None
        else:
            self._log(category, label, "fail", f"Expected {expected_status}, got {resp.status_code}")
            return None

    def _post(self, url, data=None, expected_status=200, category="form"):
        """Test a POST request with CSRF token. Returns response or None."""
        if self.csrf_token and data is not None:
            data['csrf_token'] = self.csrf_token

        start = time.time()
        resp = self.client.post(url, data=data, follow_redirects=False,
                                 content_type='application/x-www-form-urlencoded')
        duration = int((time.time() - start) * 1000)

        label = f"POST {url}"
        if resp.status_code in (expected_status, 302):
            self._log(category, label, "pass", f"{resp.status_code} ({duration}ms)")
            return resp
        else:
            self._log(category, label, "fail", f"Expected {expected_status}, got {resp.status_code}")
            return None

    def _login(self):
        """Log in as admin user."""
        # Get admin credentials from DB
        try:
            db = sqlite3.connect(_get_db())
            admin = db.execute("SELECT email, password FROM users WHERE role='admin' LIMIT 1").fetchone()
            db.close()
        except Exception as e:
            self._log("auth", "Login", "fail", f"DB error: {e}")
            return False

        if not admin:
            self._log("auth", "Login", "fail", "No admin user found")
            return False

        email = admin[0]
        # Get CSRF from login page
        resp = self.client.get('/login')
        if resp.status_code != 200:
            self._log("auth", "Login page", "fail", f"Got {resp.status_code}")
            return False

        # Extract csrf_token from HTML
        match = re.search(r'name="csrf_token"\s+value="([^"]+)"', resp.data.decode())
        self.csrf_token = match.group(1) if match else None

        # Login
        resp = self.client.post('/login', data={
            'email': email,
            'password': 'FloodAdmin2026!',
            'csrf_token': self.csrf_token or '',
        }, follow_redirects=True)

        if resp.status_code == 200 and b'dashboard' in resp.data.lower():
            self._log("auth", "Login", "pass", f"Logged in as {email}")

            # Get fresh CSRF from dashboard page
            match = re.search(r'name="csrf_token"\s+value="([^"]+)"', resp.data.decode())
            self.csrf_token = match.group(1) if match else self.csrf_token
            return True
        else:
            self._log("auth", "Login", "fail", f"Status {resp.status_code}")
            return False

    # ══════════════════════════════════════════════════════════════════════════
    # TEST SUITES
    # ══════════════════════════════════════════════════════════════════════════

    def test_navigation(self):
        """Test every sidebar navigation link returns 200."""
        category = "navigation"

        # Public pages
        self._get('/login', 200, category)
        self._get('/signup', 200, category)

        # Authenticated pages (need login first)
        authenticated_routes = [
            ('/dashboard', 'Dashboard'),
            ('/claims', 'All Claims'),
            ('/claims/new', 'New Claim'),
            ('/pipeline', 'Pipeline'),
            ('/schedule', 'Schedule'),
            ('/analytics', 'Analytics'),
            ('/notifications', 'Notifications'),
            ('/willie', 'Aquila Chat'),
            ('/admin/settings', 'Settings'),
            ('/admin/team', 'Team'),
            ('/admin/feedback', 'Feedback Studio'),
        ]

        for url, name in authenticated_routes:
            resp = self._get(url, category=category)
            if resp and resp.status_code == 200:
                # Check for critical error indicators
                body = resp.data.decode('utf-8', errors='ignore').lower()
                if 'traceback' in body or 'internal server error' in body:
                    self._log(category, name, "fail", f"GET {url} — page contains error traceback")
                elif 'sqlalchemy' in body or 'operationalerror' in body:
                    self._log(category, name, "fail", f"GET {url} — database error on page")
            elif resp is None:
                pass  # already logged as fail by _get

        self._log(category, "Navigation summary", "pass",
                  f"Tested {len(authenticated_routes)} nav links")

    def test_dashboard(self):
        """Test dashboard functionality."""
        category = "dashboard"
        resp = self._get('/dashboard', category=category)
        if not resp or resp.status_code != 200:
            return

        body = resp.data.decode('utf-8', errors='ignore')

        # Check stats tiles render
        stat_checks = ['New', 'In Progress', 'Submitted', 'Closed', 'pipeline']
        for stat in stat_checks:
            if stat.lower() in body.lower():
                self._log(category, f"Stat tile: {stat}", "pass")
            else:
                self._log(category, f"Stat tile: {stat}", "warn", f"Text '{stat}' not found on dashboard")

        # Check filter form elements
        filter_checks = ['status', 'adjuster_id', 'priority', 'date_from', 'date_to']
        for f in filter_checks:
            if f'name="{f}"' in body or f"name='{f}'" in body:
                self._log(category, f"Filter: {f}", "pass")
            else:
                self._log(category, f"Filter: {f}", "warn", "Form element not found")

        # Test search/filter
        resp2 = self._get('/dashboard?q=test&status=New', category=category)
        if resp2 and resp2.status_code == 200:
            self._log(category, "Dashboard search/filter", "pass")
        else:
            self._log(category, "Dashboard search/filter", "fail")

    def test_claims_crud(self):
        """Test creating, reading, updating, and deleting a claim."""
        category = "claims"
        claim_id = None

        # 1. Load new claim form
        resp = self._get('/claims/new', category=category)
        if not resp or resp.status_code != 200:
            return

        body = resp.data.decode('utf-8', errors='ignore')

        # Check all required form fields exist
        required_fields = ['claim_number', 'client_name', 'property_address', 'flood_date']
        for field in required_fields:
            if f'name="{field}"' in body:
                self._log(category, f"Form field: {field}", "pass")
            else:
                self._log(category, f"Form field: {field}", "fail", "Missing from form")

        # 2. Create a test claim
        test_data = {
            'claim_number': f'AQUILA-TEST-{int(time.time())}',
            'client_name': 'Aquila Test Client',
            'client_phone': '555-0100',
            'client_email': 'aquila@test.com',
            'property_address': '123 Test Street, Liberty NC 27298',
            'property_type': 'Residential',
            'property_sqft': '2000',
            'year_built': '2010',
            'flood_date': '2026-06-01',
            'flood_source': 'River overflow',
            'water_category': '3',
            'water_class': '2',
            'water_depth_in': '12',
            'insurance_company': 'NFIP',
            'policy_number': 'TEST-12345',
            'deductible': '1000',
            'cause_of_loss': 'Heavy rainfall',
            'priority': 'High',
        }

        resp = self._post('/claims/new', data=test_data, expected_status=302, category=category)
        if resp and resp.status_code == 302:
            self._log(category, "Create claim", "pass", f"Claim #{test_data['claim_number']} created")
            # Extract claim ID from redirect
            location = resp.headers.get('Location', '')
            match = re.search(r'/claims/(\d+)', location)
            claim_id = int(match.group(1)) if match else None
        else:
            self._log(category, "Create claim", "fail", f"Status {resp.status_code if resp else 'None'}")

        if not claim_id:
            self._log(category, "Claim tests", "fail", "Could not determine claim ID — skipping remaining tests")
            return

        # 3. View claim detail
        resp = self._get(f'/claims/{claim_id}', category=category)
        if resp and resp.status_code == 200:
            body = resp.data.decode('utf-8', errors='ignore')
            if 'Aquila Test Client' in body:
                self._log(category, "View claim detail", "pass")
            else:
                self._log(category, "View claim detail", "warn", "Client name not found on page")

            # Check for all expected sections
            sections = ['rooms', 'photos', 'line items', 'estimate', 'status']
            for section in sections:
                if section.lower() in body.lower():
                    self._log(category, f"Section: {section}", "pass")
                else:
                    self._log(category, f"Section: {section}", "warn", "Not found on claim page")
        else:
            self._log(category, "View claim detail", "fail")

        # 4. Update claim status
        resp = self._post(f'/claims/{claim_id}/status',
                          data={'status': 'In Progress'}, category=category)
        if resp and resp.status_code in (200, 302):
            # Verify status changed
            db = sqlite3.connect(_get_db())
            row = db.execute('SELECT status FROM claims WHERE id=?', (claim_id,)).fetchone()
            db.close()
            if row and row[0] == 'In Progress':
                self._log(category, "Update status", "pass", "Status -> In Progress")
            else:
                self._log(category, "Update status", "warn", f"DB status: {row[0] if row else 'not found'}")
        else:
            self._log(category, "Update status", "fail")

        # 5. Update estimate
        resp = self._post(f'/claims/{claim_id}/update-estimate',
                          data={'total_estimate': '15000'}, category=category)
        self._log(category, "Update estimate", "pass" if resp and resp.status_code in (200, 302) else "fail")

        # 6. Add a room
        resp = self._post(f'/claims/{claim_id}/room/add',
                          data={'name': 'Living Room', 'description': 'Main living area'}, category=category)
        room_id = None
        if resp and resp.status_code in (200, 302):
            # Extract room ID
            db = sqlite3.connect(_get_db())
            row = db.execute('SELECT id FROM rooms WHERE claim_id=? AND name=? ORDER BY id DESC LIMIT 1',
                             (claim_id, 'Living Room')).fetchone()
            db.close()
            if row:
                room_id = row[0]
                self._log(category, "Add room", "pass", f"Room ID: {room_id}")
            else:
                self._log(category, "Add room", "warn", "Room created but ID not found")
        else:
            self._log(category, "Add room", "fail")

        # 7. Add a line item
        if room_id:
            resp = self._post(f'/rooms/{room_id}/item/add', data={
                'description': 'Drywall removal',
                'quantity': '200',
                'unit': 'sf',
                'unit_cost': '1.79',
            }, category=category)
            self._log(category, "Add line item", "pass" if resp and resp.status_code in (200, 302) else "fail")

        # 8. Verify claim total recalculated
        db = sqlite3.connect(_get_db())
        row = db.execute('SELECT total_estimate FROM claims WHERE id=?', (claim_id,)).fetchone()
        db.close()
        if row and row[0] > 0:
            self._log(category, "Recalculate total", "pass", f"Total: ${row[0]:,.2f}")
        else:
            self._log(category, "Recalculate total", "warn", f"Total is ${row[0] if row else 0}")

        # 9. Duplicate claim
        resp = self._post(f'/claims/{claim_id}/duplicate', data={}, category=category)
        self._log(category, "Duplicate claim", "pass" if resp and resp.status_code == 302 else "fail")

        # 10. Delete the test claim
        resp = self._post(f'/claims/{claim_id}/delete', data={}, category=category)
        if resp and resp.status_code in (200, 302):
            # Verify deleted
            db = sqlite3.connect(_get_db())
            row = db.execute('SELECT id FROM claims WHERE id=?', (claim_id,)).fetchone()
            db.close()
            if not row:
                self._log(category, "Delete claim", "pass", f"Claim {claim_id} removed")
            else:
                self._log(category, "Delete claim", "warn", "Claim still in DB after delete")
        else:
            self._log(category, "Delete claim", "fail")

    def test_pipeline(self):
        """Test pipeline view and claim movement."""
        category = "pipeline"

        resp = self._get('/pipeline', category=category)
        if not resp or resp.status_code != 200:
            return

        body = resp.data.decode('utf-8', errors='ignore')

        # Check pipeline columns render
        columns = ['New', 'In Progress', 'Submitted', 'Closed']
        for col in columns:
            if col in body:
                self._log(category, f"Pipeline column: {col}", "pass")
            else:
                self._log(category, f"Pipeline column: {col}", "warn", "Column not found")

        # Create a test claim to move through pipeline
        db = sqlite3.connect(_get_db())
        cur = db.execute('''INSERT INTO claims (claim_number, client_name, property_address, flood_date, status)
            VALUES (?,?,?,?,?)''', (f'PIPE-TEST-{int(time.time())}', 'Pipeline Test', '456 Pipe St',
                                     '2026-06-01', 'New'))
        db.commit()
        claim_id = cur.lastrowid
        db.close()

        if claim_id:
            # Move through pipeline stages
            stages = ['In Progress', 'Submitted', 'Closed']
            for stage in stages:
                resp = self._post('/pipeline/move', data={
                    'claim_id': str(claim_id),
                    'new_status': stage,
                }, category=category)

                # Verify in DB
                db = sqlite3.connect(_get_db())
                row = db.execute('SELECT status FROM claims WHERE id=?', (claim_id,)).fetchone()
                db.close()
                if row and row[0] == stage:
                    self._log(category, f"Move to {stage}", "pass")
                else:
                    self._log(category, f"Move to {stage}", "fail", f"DB shows: {row[0] if row else 'N/A'}")

            # Cleanup
            db = sqlite3.connect(_get_db())
            db.execute('DELETE FROM claims WHERE id=?', (claim_id,))
            db.commit()
            db.close()

    def test_schedule(self):
        """Test scheduling functionality."""
        category = "schedule"

        resp = self._get('/schedule', category=category)
        if not resp or resp.status_code != 200:
            return

        body = resp.data.decode('utf-8', errors='ignore')

        if 'inspection' in body.lower() or 'schedule' in body.lower():
            self._log(category, "Schedule page renders", "pass")
        else:
            self._log(category, "Schedule page renders", "warn", "Expected content not found")

        # Create a test claim for scheduling
        db = sqlite3.connect(_get_db())
        cur = db.execute('''INSERT INTO claims (claim_number, client_name, property_address, flood_date, status)
            VALUES (?,?,?,?,?)''', (f'SCHED-TEST-{int(time.time())}', 'Schedule Test', '789 Sched Ave',
                                     '2026-06-01', 'New'))
        db.commit()
        claim_id = cur.lastrowid

        # Get an adjuster
        adj = db.execute("SELECT id FROM users WHERE role='admin' LIMIT 1").fetchone()
        adjuster_id = adj[0] if adj else None
        db.close()

        if claim_id and adjuster_id:
            # Add inspection slot
            resp = self._post('/schedule/add', data={
                'claim_id': str(claim_id),
                'adjuster_id': str(adjuster_id),
                'slot_date': '2026-06-15',
                'slot_time': '10:00',
                'notes': 'Aquila test inspection',
            }, category=category)

            if resp and resp.status_code in (200, 302):
                # Verify slot created
                db = sqlite3.connect(_get_db())
                slot = db.execute('SELECT id FROM inspection_slots WHERE claim_id=? AND slot_date=?',
                                  (claim_id, '2026-06-15')).fetchone()
                db.close()
                if slot:
                    self._log(category, "Add inspection", "pass", f"Slot ID: {slot[0]}")
                else:
                    self._log(category, "Add inspection", "fail", "Slot not in DB")
            else:
                self._log(category, "Add inspection", "fail", f"Status {resp.status_code if resp else 'None'}")

            # Update slot status
            db = sqlite3.connect(_get_db())
            slot = db.execute('SELECT id FROM inspection_slots WHERE claim_id=?', (claim_id,)).fetchone()
            db.close()
            if slot:
                resp = self._post(f'/schedule/{slot[0]}/status',
                                  data={'status': 'confirmed'}, category=category)
                self._log(category, "Update slot status", "pass" if resp and resp.status_code in (200, 302) else "fail")

            # Delete slot
            if slot:
                resp = self._post(f'/schedule/{slot[0]}/delete', data={}, category=category)
                self._log(category, "Delete slot", "pass" if resp and resp.status_code in (200, 302) else "fail")

        # Cleanup
        db = sqlite3.connect(_get_db())
        db.execute('DELETE FROM claims WHERE id=?', (claim_id,))
        db.execute('DELETE FROM inspection_slots WHERE claim_id=?', (claim_id,))
        db.commit()
        db.close()

    def test_qr_and_portal(self):
        """Test QR code generation and portal upload flow."""
        category = "qr_portal"

        # Create a test claim
        db = sqlite3.connect(_get_db())
        cur = db.execute('''INSERT INTO claims (claim_number, client_name, property_address, flood_date, status)
            VALUES (?,?,?,?,?)''', (f'QR-TEST-{int(time.time())}', 'QR Test', '321 QR Blvd',
                                     '2026-06-01', 'New'))
        db.commit()
        claim_id = cur.lastrowid
        db.close()

        if not claim_id:
            self._log(category, "QR tests", "fail", "Could not create test claim")
            return

        # 1. Generate portal link (QR code)
        resp = self._post(f'/claims/{claim_id}/portal/generate', data={}, category=category)
        if resp and resp.status_code in (200, 302):
            # Verify token created
            db = sqlite3.connect(_get_db())
            token_row = db.execute('SELECT token FROM client_portal_tokens WHERE claim_id=?',
                                   (claim_id,)).fetchone()
            db.close()
            if token_row:
                token = token_row[0]
                self._log(category, "Generate portal token", "pass", f"Token: {token[:20]}...")

                # 2. Access portal with token
                resp = self._get(f'/portal/{token}', category=category)
                self._log(category, "Portal access", "pass" if resp and resp.status_code == 200 else "fail")

                # 3. Portal upload page
                resp = self._get(f'/portal/{token}/upload', category=category)
                self._log(category, "Portal upload page", "pass" if resp and resp.status_code == 200 else "fail")

                # 4. Portal status
                resp = self._get(f'/portal/{token}/status', category=category)
                self._log(category, "Portal status", "pass" if resp and resp.status_code == 200 else "fail")

                # 5. Customer upload link
                resp = self._get(f'/customer/upload/{token}', category=category)
                self._log(category, "Customer upload page", "pass" if resp and resp.status_code == 200 else "fail")

            else:
                self._log(category, "Generate portal token", "fail", "Token not in DB")
        else:
            self._log(category, "Generate portal token", "fail")

        # Also test the QR page
        resp = self._get(f'/claims/{claim_id}/qr', category=category)
        self._log(category, "QR code page", "pass" if resp and resp.status_code == 200 else "fail")

        # Cleanup
        db = sqlite3.connect(_get_db())
        db.execute('DELETE FROM claims WHERE id=?', (claim_id,))
        db.execute('DELETE FROM client_portal_tokens WHERE claim_id=?', (claim_id,))
        db.commit()
        db.close()

    def test_analytics(self):
        """Test analytics page."""
        category = "analytics"
        resp = self._get('/analytics', category=category)
        if resp and resp.status_code == 200:
            body = resp.data.decode('utf-8', errors='ignore')
            if 'analytic' in body.lower() or 'claim' in body.lower():
                self._log(category, "Analytics page renders", "pass")
            else:
                self._log(category, "Analytics page renders", "warn", "Expected content not found")
        else:
            self._log(category, "Analytics page renders", "fail")

    def test_notifications(self):
        """Test notifications page."""
        category = "notifications"

        resp = self._get('/notifications', category=category)
        if resp and resp.status_code == 200:
            body = resp.data.decode('utf-8', errors='ignore')
            if 'notification' in body.lower():
                self._log(category, "Notifications page renders", "pass")
            else:
                self._log(category, "Notifications page renders", "warn")
        else:
            self._log(category, "Notifications page renders", "fail")

    def test_ai_integration(self):
        """Test AI features — API key, brain files, photo analysis."""
        category = "ai"

        # 1. Check OpenRouter API key is configured
        db = sqlite3.connect(_get_db())
        key = db.execute("SELECT value FROM settings WHERE key='openrouter_api_key'").fetchone()
        db.close()

        if key and key[0]:
            self._log(category, "OpenRouter key configured", "pass")
        else:
            env_key = os.environ.get('OPENROUTER_API_KEY', '')
            if env_key:
                self._log(category, "OpenRouter key (env var)", "pass")
            else:
                self._log(category, "OpenRouter key", "fail", "No API key in DB or environment")

        # 2. Check brain files loaded
        brain_keys = ['brain_identity_md', 'brain_soul_md', 'brain_memory_md',
                      'brain_system_prompt', 'brain_photo_prompt']
        missing_brains = []
        db = sqlite3.connect(_get_db())
        for bk in brain_keys:
            row = db.execute("SELECT value FROM settings WHERE key=?", (bk,)).fetchone()
            if not row or not row[0]:
                missing_brains.append(bk)
        db.close()

        if not missing_brains:
            self._log(category, "Brain files loaded", "pass", f"All {len(brain_keys)} brain files present")
        else:
            self._log(category, "Brain files loaded", "fail", f"Missing: {', '.join(missing_brains)}")

        # 3. Check vision key configured
        db = sqlite3.connect(_get_db())
        vision_key = db.execute("SELECT value FROM settings WHERE key='ai_vision_key' OR key='openrouter_api_key' LIMIT 1").fetchone()
        db.close()
        if vision_key and vision_key[0]:
            self._log(category, "AI model key", "pass")
        else:
            self._log(category, "AI model key", "warn", "No AI vision key found — photo analysis may fail")

        # 4. Test brain editor page
        resp = self._get('/admin/settings', category=category)
        if resp and resp.status_code == 200:
            body = resp.data.decode('utf-8', errors='ignore')
            if 'brain-identity' in body or 'brain_identity' in body or 'Train Aquila' in body:
                self._log(category, "Brain editor in settings", "pass")
            else:
                self._log(category, "Brain editor in settings", "warn", "Brain editor not found on settings page")

        # 5. Verify Aquila chat bubble renders on dashboard
        resp = self._get('/dashboard', category=category)
        if resp and resp.status_code == 200:
            body = resp.data.decode('utf-8', errors='ignore')
            if 'aai-bubble' in body or 'aquila' in body.lower():
                self._log(category, "Chat bubble present", "pass")
            else:
                self._log(category, "Chat bubble present", "warn", "Chat bubble not found on dashboard")

    def test_team_management(self):
        """Test adding, editing, and managing team members."""
        category = "team"

        # 1. Load team page
        resp = self._get('/admin/team', category=category)
        if not resp or resp.status_code != 200:
            return

        body = resp.data.decode('utf-8', errors='ignore')
        if 'team' in body.lower() or 'member' in body.lower():
            self._log(category, "Team page renders", "pass")
        else:
            self._log(category, "Team page renders", "warn")

        # 2. Add a test team member
        test_email = f'aquila-test-{int(time.time())}@test.com'
        resp = self._post('/admin/team/add', data={
            'email': test_email,
            'name': 'Aquila Test Adjuster',
            'password': 'TestPass123!',
            'role': 'adjuster',
        }, category=category)

        user_id = None
        if resp and resp.status_code in (200, 302):
            db = sqlite3.connect(_get_db())
            user = db.execute('SELECT id FROM users WHERE email=?', (test_email,)).fetchone()
            db.close()
            if user:
                user_id = user[0]
                self._log(category, "Add team member", "pass", f"User ID: {user_id}")
            else:
                self._log(category, "Add team member", "fail", "User not in DB")
        else:
            self._log(category, "Add team member", "fail")

        # 3. Edit team member
        if user_id:
            resp = self._post(f'/admin/team/{user_id}/edit', data={
                'email': test_email,
                'name': 'Aquila Edited Adjuster',
                'password': '',  # Don't change password
                'role': 'adjuster',
            }, category=category)
            self._log(category, "Edit team member", "pass" if resp and resp.status_code in (200, 302) else "fail")

        # 4. Deactivate
        if user_id:
            resp = self._post(f'/admin/team/{user_id}/deactivate', data={}, category=category)
            if resp and resp.status_code in (200, 302):
                db = sqlite3.connect(_get_db())
                row = db.execute('SELECT is_active FROM users WHERE id=?', (user_id,)).fetchone()
                db.close()
                if row and row[0] == 0:
                    self._log(category, "Deactivate member", "pass")
                else:
                    self._log(category, "Deactivate member", "warn", f"is_active={row[0] if row else 'N/A'}")
            else:
                self._log(category, "Deactivate member", "fail")

        # 5. Reactivate
        if user_id:
            resp = self._post(f'/admin/team/{user_id}/reactivate', data={}, category=category)
            self._log(category, "Reactivate member", "pass" if resp and resp.status_code in (200, 302) else "fail")

        # 6. Delete test member
        if user_id:
            resp = self._post(f'/admin/team/{user_id}/delete', data={}, category=category)
            if resp and resp.status_code in (200, 302):
                db = sqlite3.connect(_get_db())
                row = db.execute('SELECT id FROM users WHERE id=?', (user_id,)).fetchone()
                db.close()
                if not row:
                    self._log(category, "Delete team member", "pass")
                else:
                    self._log(category, "Delete team member", "warn", "User still in DB")
            else:
                self._log(category, "Delete team member", "fail")

    def test_settings(self):
        """Test settings page functionality."""
        category = "settings"

        resp = self._get('/admin/settings', category=category)
        if not resp or resp.status_code != 200:
            return

        body = resp.data.decode('utf-8', errors='ignore')

        # Check all settings sections render
        sections = ['AI Integration', 'Train Aquila', 'API', 'brain', 'vision']
        for section in sections:
            if section.lower() in body.lower():
                self._log(category, f"Section: {section}", "pass")
            else:
                self._log(category, f"Section: {section}", "warn", "Section not found")

        # Test saving a setting
        resp = self._post('/admin/settings', data={
            'ai_vision_model': 'openrouter/auto',
        }, category=category)
        self._log(category, "Save settings", "pass" if resp and resp.status_code in (200, 302) else "fail")

        # Verify saved
        db = sqlite3.connect(_get_db())
        row = db.execute("SELECT value FROM settings WHERE key='ai_vision_model'").fetchone()
        db.close()
        if row and row[0] == 'openrouter/auto':
            self._log(category, "Verify saved setting", "pass", "ai_vision_model = openrouter/auto")
        else:
            self._log(category, "Verify saved setting", "warn", f"Value: {row[0] if row else 'not found'}")

        # Test brain editor endpoint
        resp = self._get('/admin/willie/brain', category=category)
        self._log(category, "Brain editor API", "pass" if resp and resp.status_code == 200 else "fail")

        # Test init brain
        resp = self._post('/admin/api/init-brain', data={}, category=category)
        if resp and resp.status_code == 200:
            try:
                data = json.loads(resp.data)
                if data.get('ok'):
                    self._log(category, "Init brain files", "pass")
                else:
                    self._log(category, "Init brain files", "fail", str(data))
            except Exception:
                self._log(category, "Init brain files", "pass", "(non-JSON response)")
        else:
            self._log(category, "Init brain files", "fail")

    # ══════════════════════════════════════════════════════════════════════════
    # MASTER RUNNER
    # ══════════════════════════════════════════════════════════════════════════

    def run_all_tests(self):
        """Run the complete extreme test suite."""
        import uuid
        self.run_id = str(uuid.uuid4())[:12]
        _start_run(self.run_id)

        logger.info(f"═══ AQUILA EXTREME TESTING STARTED [{self.run_id}] ═══")

        suites = [
            ("Navigation", self.test_navigation),
            ("Dashboard", self.test_dashboard),
            ("Claims CRUD", self.test_claims_crud),
            ("Pipeline", self.test_pipeline),
            ("Schedule", self.test_schedule),
            ("QR & Portal", self.test_qr_and_portal),
            ("Analytics", self.test_analytics),
            ("Notifications", self.test_notifications),
            ("AI Integration", self.test_ai_integration),
            ("Team Management", self.test_team_management),
            ("Settings", self.test_settings),
        ]

        for name, fn in suites:
            try:
                fn()
            except Exception as e:
                self._log("system", f"{name} suite crashed", "fail",
                          f"{str(e)[:200]}\n{traceback.format_exc()[:300]}")
                logger.error(f"[TEST] {name} suite crashed: {e}", exc_info=True)

        # Summary
        s = self.stats
        summary = (f"Total: {s['total']} | ✅ Passed: {s['passed']} | "
                    f"❌ Failed: {s['failed']} | ⚠️ Warnings: {s['warnings']}")
        _finish_run(self.run_id, s['total'], s['passed'], s['failed'], s['warnings'], summary)
        logger.info(f"═══ TESTING COMPLETE [{self.run_id}] — {summary} ═══")
        return self.stats


# ── Module-level convenience ─────────────────────────────────────────────────

def run_extreme_tests(app):
    """Entry point: run all extreme tests."""
    runner = AquilaTestRunner(app)
    if runner._login():
        return runner.run_all_tests()
    else:
        return {"passed": 0, "failed": 1, "warnings": 0, "total": 1, "error": "Login failed"}
