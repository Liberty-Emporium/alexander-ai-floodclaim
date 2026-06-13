"""
Health Monitor Routes
======================
Provides health check dashboard and API endpoints.
"""
import json
from flask import Blueprint, render_template, jsonify
from models.database import get_db, get_setting
from utils.auth_decorators import login_required, admin_required
from services.health_monitor import run_all_checks

bp = Blueprint("health", __name__, url_prefix="/health-dashboard")


@bp.route("/")
@login_required
@admin_required
def dashboard():
    """Render the health monitoring dashboard."""
    # Load the latest cached report for fast page load
    raw = get_setting("health_report_latest", "")
    report = None
    if raw:
        try:
            report = json.loads(raw)
        except Exception:
            pass

    return render_template("health_dashboard.html", report=report)


@bp.route("/api/check", methods=["POST"])
@login_required
@admin_required
def api_run_checks():
    """Run all health checks and return results."""
    report = run_all_checks()
    return jsonify(report)


@bp.route("/api/status")
@login_required
@admin_required
def api_cached_status():
    """Return the latest cached health report (fast, no re-check)."""
    raw = get_setting("health_report_latest", "")
    if raw:
        try:
            return jsonify(json.loads(raw))
        except Exception:
            pass
    return jsonify({"ok": False, "message": "No health report available yet. Run a check first."})
