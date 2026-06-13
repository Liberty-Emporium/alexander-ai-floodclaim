"""
Extreme Testing Routes
========================
Aquila runs comprehensive tests on demand.
Results saved to DB, visible in admin panel.
"""
import json
from flask import Blueprint, render_template, jsonify, request
from models.database import get_db
from utils.auth_decorators import login_required, admin_required

bp = Blueprint("extreme_test", __name__, url_prefix="/admin/extreme-test")


@bp.route("/")
@login_required
@admin_required
def test_dashboard():
    """Show test results dashboard."""
    db = get_db()

    # Get latest test run
    latest = db.execute(
        "SELECT * FROM aquila_test_runs ORDER BY started_at DESC LIMIT 1"
    ).fetchone()

    # Get results for latest run
    results = []
    if latest:
        results = db.execute(
            "SELECT * FROM aquila_test_results WHERE test_run_id=? ORDER BY id",
            (latest["id"],)
        ).fetchall()

    # Get all runs for history
    runs = db.execute(
        "SELECT * FROM aquila_test_runs ORDER BY started_at DESC LIMIT 20"
    ).fetchall()

    return render_template("extreme_test_dashboard.html",
                           latest=latest, results=results, runs=runs)


@bp.route("/run", methods=["POST"])
@login_required
@admin_required
def run_tests():
    """Trigger extreme test run."""
    try:
        from flask import current_app
        from services.extreme_testing import run_extreme_tests
        stats = run_extreme_tests(current_app._get_current_object())
        return jsonify({"ok": True, "stats": stats})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@bp.route("/api/results/<run_id>")
@login_required
@admin_required
def api_results(run_id):
    """Get test results for a specific run."""
    db = get_db()
    results = db.execute(
        "SELECT * FROM aquila_test_results WHERE test_run_id=? ORDER BY id",
        (run_id,)
    ).fetchall()
    return jsonify({"results": [dict(r) for r in results]})
