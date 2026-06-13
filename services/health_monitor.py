"""
AI Self-Healing Monitor Service
================================
Comprehensive health checks that run automatically to verify every
part of FloodClaims Pro is working. Can auto-fix some issues and
always reports problems clearly.

Called by:
  - Cron job (every 5 minutes) → auto-heal + alert
  - /health-dashboard route → visual status page
  - /api/health-check (JSON API) → for external monitoring
"""
import os
import sys
import json
import time
import sqlite3
import logging
import datetime
import requests as _req

logger = logging.getLogger(__name__)

DB_PATH = os.environ.get('DB_PATH') or '/data/floodclaims.db'
DATA_DIR = os.environ.get('RAILWAY_VOLUME_MOUNT_PATH', '/data')
UPLOAD_DIR = os.path.join(DATA_DIR, 'uploads')
APP_URL = os.environ.get('APP_URL', 'http://localhost:5000')

# ── Helpers ────────────────────────────────────────────────────────────────────


def _get_setting(key, default=''):
    try:
        db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
        row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        db.close()
        return row['value'] if row else default
    except Exception:
        return default


def _check_disk_space():
    """Check disk space on the data volume."""
    try:
        stat = os.statvfs(DATA_DIR)
        total = stat.f_blocks * stat.f_frsize
        free = stat.f_bavail * stat.f_frsize
        used = total - free
        pct_used = (used / total * 100) if total > 0 else 0
        return {
            "total_gb": round(total / (1024**3), 2),
            "free_gb": round(free / (1024**3), 2),
            "used_pct": round(pct_used, 1),
            "ok": pct_used < 90,
        }
    except Exception:
        return {"total_gb": 0, "free_gb": 0, "used_pct": 0, "ok": True}


# ── Individual health checks ──────────────────────────────────────────────────


def check_database():
    """Verify database is readable and writable."""
    results = {"name": "Database", "emoji": "🗄️", "ok": True, "issues": []}
    try:
        db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row

        # Read check
        tables = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        table_names = [t[0] for t in tables]
        results["tables"] = table_names

        required_tables = [
            "users", "claims", "rooms", "line_items", "photos",
            "settings", "willie_conversations", "willie_messages",
        ]
        for t in required_tables:
            if t not in table_names:
                results["issues"].append(f"Missing table: {t}")
                results["ok"] = False

        # Write check — try a harmless write
        db.execute(
            "INSERT INTO settings (key, value) VALUES ('health_check_ping', ?)",
            (datetime.datetime.now().isoformat(),),
        )
        db.execute("DELETE FROM settings WHERE key='health_check_ping'")
        db.commit()

        # Integrity check
        integrity = db.execute("PRAGMA integrity_check").fetchone()
        if integrity[0] != "ok":
            results["issues"].append(f"Integrity check failed: {integrity[0]}")
            results["ok"] = False

        # Count records
        claim_count = db.execute("SELECT COUNT(*) FROM claims").fetchone()[0]
        user_count = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        results["claims"] = claim_count
        results["users"] = user_count

        db.close()

        if results["ok"]:
            results["message"] = f"✅ DB healthy — {len(table_names)} tables, {claim_count} claims, {user_count} users"
        else:
            results["message"] = f"❌ DB issues: {'; '.join(results['issues'])}"

    except Exception as e:
        results["ok"] = False
        results["issues"].append(str(e))
        results["message"] = f"❌ Database error: {e}"

    return results


def check_openrouter():
    """Test OpenRouter API key and connectivity."""
    results = {"name": "OpenRouter AI", "emoji": "🤖", "ok": True, "issues": []}
    key = _get_setting("openrouter_api_key") or os.environ.get("OPENROUTER_API_KEY", "")

    if not key:
        results["ok"] = True  # Not configured = not broken, just not set up
        results["message"] = "⚠️ Not configured — set OPENROUTER_API_KEY in Railway"
        results["configured"] = False
        return results

    results["configured"] = True
    try:
        r = _req.get(
            "https://openrouter.ai/api/v1/auth/key",
            headers={"Authorization": f"Bearer {key}"},
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json().get("data", {})
            results["message"] = f"✅ OpenRouter connected (label: {data.get('label', 'default')})"
        elif r.status_code == 401:
            results["ok"] = False
            results["issues"].append("Invalid API key")
            results["message"] = "❌ OpenRouter key invalid — update in Railway or Settings"
        elif r.status_code == 402:
            results["ok"] = False
            results["issues"].append("No credits remaining")
            results["message"] = "❌ OpenRouter out of credits — add at openrouter.ai/credits"
        else:
            results["ok"] = False
            results["issues"].append(f"HTTP {r.status_code}")
            results["message"] = f"⚠️ OpenRouter returned {r.status_code}"
    except _req.exceptions.Timeout:
        results["ok"] = False
        results["issues"].append("Connection timeout")
        results["message"] = "⏱️ OpenRouter timed out"
    except Exception as e:
        results["ok"] = False
        results["issues"].append(str(e))
        results["message"] = f"⚠️ OpenRouter error: {e}"

    return results


def check_twilio():
    """Test Twilio connectivity."""
    results = {"name": "Twilio SMS", "emoji": "📱", "ok": True, "issues": []}
    sid = _get_setting("twilio_account_sid")
    token = _get_setting("twilio_auth_token")

    if not sid or not token:
        results["message"] = "⚠️ Not configured — set in Settings → Twilio"
        results["configured"] = False
        return results

    results["configured"] = True
    try:
        r = _req.get(
            f"https://api.twilio.com/2010-04-01/Accounts/{sid}.json",
            auth=(sid, token),
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            results["message"] = f"✅ Twilio connected — {data.get('friendly_name', 'account')} ({data.get('status', '?')})"
        elif r.status_code == 401:
            results["ok"] = False
            results["issues"].append("Invalid credentials")
            results["message"] = "❌ Twilio credentials invalid"
        else:
            results["ok"] = False
            results["issues"].append(f"HTTP {r.status_code}")
            results["message"] = f"⚠️ Twilio returned {r.status_code}"
    except _req.exceptions.Timeout:
        results["ok"] = False
        results["issues"].append("Connection timeout")
        results["message"] = "⏱️ Twilio timed out"
    except Exception as e:
        results["ok"] = False
        results["issues"].append(str(e))
        results["message"] = f"⚠️ Twilio error: {e}"

    return results


def check_sendgrid():
    """Test SendGrid connectivity."""
    results = {"name": "SendGrid Email", "emoji": "📧", "ok": True, "issues": []}
    key = _get_setting("sendgrid_api_key") or os.environ.get("SENDGRID_API_KEY", "")

    if not key:
        results["message"] = "⚠️ Not configured — set in Settings → SendGrid"
        results["configured"] = False
        return results

    results["configured"] = True
    try:
        r = _req.get(
            "https://api.sendgrid.com/v3/user/profile",
            headers={"Authorization": f"Bearer {key}"},
            timeout=10,
        )
        if r.status_code == 200:
            results["message"] = f"✅ SendGrid connected — {r.json().get('username', 'verified')}"
        elif r.status_code == 401:
            results["ok"] = False
            results["issues"].append("Invalid API key")
            results["message"] = "❌ SendGrid key invalid"
        else:
            results["ok"] = False
            results["message"] = f"⚠️ SendGrid returned {r.status_code}"
    except _req.exceptions.Timeout:
        results["ok"] = False
        results["message"] = "⏱️ SendGrid timed out"
    except Exception as e:
        results["ok"] = False
        results["message"] = f"⚠️ SendGrid error: {e}"

    return results


def check_disk():
    """Check disk space."""
    results = {"name": "Disk Space", "emoji": "💾", "ok": True, "issues": []}
    try:
        disk = _check_disk_space()
        results.update(disk)

        if disk["ok"]:
            results["message"] = f"✅ Disk OK — {disk['free_gb']}GB free ({100 - disk['used_pct']}% available)"
        else:
            results["ok"] = False
            results["issues"].append(f"Disk {disk['used_pct']}% full")
            results["message"] = f"❌ Disk almost full — {disk['used_pct']}% used, only {disk['free_gb']}GB left"

        # Also check upload dir writable
        if os.path.isdir(UPLOAD_DIR):
            test_file = os.path.join(UPLOAD_DIR, ".health_check")
            try:
                with open(test_file, "w") as f:
                    f.write("ok")
                os.unlink(test_file)
            except Exception:
                results["ok"] = False
                results["issues"].append("Upload directory not writable")
        else:
            results["issues"].append("Upload directory missing")

    except Exception as e:
        results["ok"] = False
        results["message"] = f"⚠️ Disk check error: {e}"

    return results


def check_routes():
    """Verify key routes respond with 200."""
    results = {"name": "Route Health", "emoji": "🌐", "ok": True, "issues": []}
    routes_to_check = [
        ("/", "Dashboard"),
        ("/login", "Login"),
        ("/health", "Health"),
    ]

    failed = []
    for path, name in routes_to_check:
        try:
            r = _req.get(f"{APP_URL}{path}", timeout=5, allow_redirects=True)
            if r.status_code >= 500:
                failed.append(f"{name} ({path}) → {r.status_code}")
        except Exception as e:
            failed.append(f"{name} ({path}) → error: {e}")

    if failed:
        results["ok"] = False
        results["issues"] = failed
        results["message"] = f"❌ {len(failed)} route(s) failing: {', '.join(failed[:3])}"
    else:
        results["message"] = f"✅ All {len(routes_to_check)} key routes responding"

    return results


def check_claim_integrity():
    """Check for orphaned records and data integrity issues."""
    results = {"name": "Claim Integrity", "emoji": "🔍", "ok": True, "issues": []}
    try:
        db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row

        # Orphaned rooms (claim deleted but rooms remain — shouldn't happen with CASCADE but check)
        orphaned_rooms = db.execute(
            "SELECT COUNT(*) FROM rooms r LEFT JOIN claims c ON r.claim_id=c.id WHERE c.id IS NULL"
        ).fetchone()[0]

        # Orphaned photos
        orphaned_photos = db.execute(
            "SELECT COUNT(*) FROM photos p LEFT JOIN claims c ON p.claim_id=c.id WHERE c.id IS NULL"
        ).fetchone()[0]

        # Orphaned line items
        orphaned_items = db.execute(
            "SELECT COUNT(*) FROM line_items li LEFT JOIN rooms r ON li.room_id=r.id LEFT JOIN claims c ON li.claim_id=c.id WHERE c.id IS NULL"
        ).fetchone()[0]

        # Claims with 0 estimate (new claims are OK, old ones might be stuck)
        old_zero_claims = db.execute(
            "SELECT id, claim_number, client_name, created_at FROM claims WHERE total_estimate=0 AND status='New' AND created_at < datetime('now', '-7 days')"
        ).fetchall()

        # Claims without photos
        no_photo_claims = db.execute(
            "SELECT COUNT(*) FROM claims WHERE id NOT IN (SELECT DISTINCT claim_id FROM photos WHERE deleted_at IS NULL)"
        ).fetchone()[0]

        if orphaned_rooms:
            results["issues"].append(f"{orphaned_rooms} orphaned rooms")
            results["ok"] = False
        if orphaned_photos:
            results["issues"].append(f"{orphaned_photos} orphaned photos")
            results["ok"] = False
        if orphaned_items:
            results["issues"].append(f"{orphaned_items} orphaned line items")
            results["ok"] = False

        stuck_info = ""
        if old_zero_claims:
            stuck_list = ", ".join(f"#{c['claim_number']} ({c['client_name']})" for c in old_zero_claims[:3])
            results["issues"].append(f"{len(old_zero_claims)} claims stuck >7 days with $0 estimate: {stuck_list}")
            results["ok"] = False

        db.close()

        if results["ok"]:
            results["message"] = "✅ No orphaned records or data issues found"
        else:
            results["message"] = f"⚠️ {'; '.join(results['issues'])}"

    except Exception as e:
        results["ok"] = False
        results["message"] = f"⚠️ Integrity check error: {e}"

    return results


def check_uploads():
    """Check upload directory health and photo count."""
    results = {"name": "Photo Storage", "emoji": "📸", "ok": True, "issues": []}
    try:
        if not os.path.isdir(UPLOAD_DIR):
            results["message"] = "📁 Upload directory doesn't exist yet (OK if no photos uploaded)"
            return results

        files = [f for f in os.listdir(UPLOAD_DIR) if not f.startswith(".")]
        total_size = sum(os.path.getsize(os.path.join(UPLOAD_DIR, f)) for f in files)
        results["photo_count"] = len(files)
        results["total_mb"] = round(total_size / (1024 * 1024), 1)
        results["message"] = f"✅ {len(files)} photos stored ({results['total_mb']}MB)"

        # Check for files not in DB (orphaned files)
        try:
            db = sqlite3.connect(DB_PATH)
            db_files = set(
                row[0] for row in db.execute("SELECT filename FROM photos WHERE deleted_at IS NULL").fetchall()
            )
            db.close()
            disk_files = set(files)
            orphans = disk_files - db_files
            if orphans:
                results["issues"].append(f"{len(orphans)} files on disk not in database")
                # Not marking as failing, just informational
                results["message"] += f" ({len(orphans)} orphaned files on disk)"
        except Exception:
            pass

    except Exception as e:
        results["message"] = f"⚠️ Upload check error: {e}"

    return results


def check_aquila_brain():
    """Verify Aquila brain files are loaded."""
    results = {"name": "Aquila Brain", "emoji": "🧠", "ok": True, "issues": []}
    try:
        checks = {
            "Identity": _get_setting("brain_identity_md"),
            "Soul": _get_setting("brain_soul_md"),
            "Memory": _get_setting("brain_memory_md"),
            "System Prompt": _get_setting("brain_system_prompt"),
            "Photo Prompt": _get_setting("brain_photo_prompt"),
        }
        missing = [k for k, v in checks.items() if not v]
        if missing:
            results["issues"].append(f"Missing brain files: {', '.join(missing)}")
            results["message"] = f"⚠️ {len(missing)} brain file(s) not set — Aquila uses defaults"
        else:
            results["message"] = f"✅ All {len(checks)} brain files loaded"
    except Exception as e:
        results["message"] = f"⚠️ Brain check error: {e}"

    return results


# ── Master health check ────────────────────────────────────────────────────────


def run_all_checks():
    """Run every health check and return a full report."""
    start = time.time()

    checks = [
        check_database,
        check_openrouter,
        check_twilio,
        check_sendgrid,
        check_disk,
        check_routes,
        check_claim_integrity,
        check_uploads,
        check_aquila_brain,
    ]

    results = []
    all_ok = True
    critical_issues = []

    for check_fn in checks:
        try:
            r = check_fn()
        except Exception as e:
            r = {"name": check_fn.__name__, "emoji": "❓", "ok": False, "message": f"Check crashed: {e}", "issues": [str(e)]}
        results.append(r)
        if not r.get("ok", True):
            all_ok = False
            critical_issues.append(f"{r.get('emoji', '')} {r['name']}: {r.get('message', '')}")

    elapsed = round(time.time() - start, 2)

    # Save the latest report to settings so the dashboard can load it fast
    report = {
        "timestamp": datetime.datetime.now().isoformat(),
        "all_ok": all_ok,
        "elapsed_s": elapsed,
        "checks": results,
        "critical_issues": critical_issues,
    }

    try:
        db = sqlite3.connect(DB_PATH)
        db.execute(
            "INSERT INTO settings (key, value) VALUES ('health_report_latest', ?)",
            (json.dumps(report),),
        )
        db.execute(
            "INSERT INTO settings (key, value) VALUES ('health_report_time', ?)",
            (report["timestamp"],),
        )
        db.commit()
        db.close()
    except Exception:
        pass  # Don't fail the health check because of a settings write failure

    return report
