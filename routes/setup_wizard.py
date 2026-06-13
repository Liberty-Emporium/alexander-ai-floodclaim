"""
AI-Guided Setup Wizard Routes
==============================
Conversational setup flow for all integrations.
Aquila asks questions, tests connections, and saves config.
"""
import json
from flask import (
    Blueprint, render_template, request, session, flash,
    jsonify, redirect, url_for
)
from models.database import get_db, get_setting, set_setting
from utils.auth_decorators import login_required, admin_required
from services.setup_wizard import (
    SETUP_SERVICES, run_test, get_setup_status, get_completion_pct
)

bp = Blueprint("setup_wizard", __name__, url_prefix="/setup")


@bp.route("/")
@login_required
@admin_required
def setup_wizard_page():
    """Render the setup wizard UI."""
    status = get_setup_status()
    pct = get_completion_pct()
    return render_template(
        "setup_wizard.html",
        services=SETUP_SERVICES,
        status=status,
        completion_pct=pct,
    )


@bp.route("/api/status")
@login_required
@admin_required
def api_status():
    """Get setup completion status for all services."""
    return jsonify({
        "status": get_setup_status(),
        "completion_pct": get_completion_pct(),
    })


@bp.route("/api/test", methods=["POST"])
@login_required
@admin_required
def api_test_connection():
    """Test a service connection. Expects JSON: {service_id, values: {key: value}}."""
    data = request.get_json(silent=True) or {}
    service_id = data.get("service_id", "")
    values = data.get("values", {})

    if not service_id:
        return jsonify({"ok": False, "message": "service_id required"}), 400

    result = run_test(service_id, values)
    return jsonify(result)


@bp.route("/api/save", methods=["POST"])
@login_required
@admin_required
def api_save_config():
    """Save configuration values for a service."""
    data = request.get_json(silent=True) or {}
    service_id = data.get("service_id", "")
    values = data.get("values", {})

    if not service_id:
        return jsonify({"ok": False, "message": "service_id required"}), 400

    svc = SETUP_SERVICES.get(service_id)
    if not svc:
        return jsonify({"ok": False, "message": f"Unknown service: {service_id}"}), 400

    # Save all provided values
    saved = []
    for field in svc["fields"]:
        key = field["key"]
        val = values.get(key, "").strip()
        if val:
            set_setting(key, val)
            saved.append(key)

    return jsonify({
        "ok": True,
        "message": f"Saved {len(saved)} value(s) for {svc['label']}",
        "saved": saved,
    })


@bp.route("/api/save-and-test", methods=["POST"])
@login_required
@admin_required
def api_save_and_test():
    """Save values then test the connection."""
    data = request.get_json(silent=True) or {}
    service_id = data.get("service_id", "")
    values = data.get("values", {})

    if not service_id:
        return jsonify({"ok": False, "message": "service_id required"}), 400

    svc = SETUP_SERVICES.get(service_id)
    if not svc:
        return jsonify({"ok": False, "message": f"Unknown service: {service_id}"}), 400

    # Save all provided values first
    saved = []
    for field in svc["fields"]:
        key = field["key"]
        val = values.get(key, "").strip()
        if val:
            set_setting(key, val)
            saved.append(key)

    # Run the test
    test_result = run_test(service_id, values)
    test_result["saved"] = saved
    return jsonify(test_result)
