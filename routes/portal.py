"""
Client Portal — Enhanced customer-facing claim status page.

Features:
  - Real-time claim status
  - Room-by-room damage display with photos
  - Document upload (customer-submitted photos)
  - Digital signature capture
  - Claim timeline / activity log
  - Secure token-based access (no login required)
"""
import datetime
import os
import secrets

from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify, current_app, abort
from models.database import get_db
from utils.auth_decorators import login_required
from werkzeug.utils import secure_filename
from services.email import send_email

bp = Blueprint("portal", __name__, url_prefix="/portal")


@bp.route("/<token>")
def portal_home(token):
    """Main client portal page — no login required, token-based access."""
    db = get_db()
    row = db.execute(
        'SELECT claim_id, created_at FROM client_portal_tokens WHERE token=?', (token,)
    ).fetchone()
    if not row:
        return render_template("portal_invalid.html"), 404

    claim_id = row['claim_id']
    claim = db.execute(
        '''SELECT c.*, u.name as adjuster_name, u.email as adjuster_email
           FROM claims c LEFT JOIN users u ON c.adjuster_id=u.id WHERE c.id=?''',
        (claim_id,)
    ).fetchone()
    if not claim:
        return render_template("portal_invalid.html"), 404

    rooms = db.execute(
        'SELECT * FROM rooms WHERE claim_id=? AND deleted_at IS NULL ORDER BY id', (claim_id,)
    ).fetchall()
    room_data = []
    for room in rooms:
        items = db.execute(
            'SELECT * FROM line_items WHERE room_id=? AND deleted_at IS NULL ORDER BY id', (room['id'],)
        ).fetchall()
        photos = db.execute(
            'SELECT * FROM photos WHERE room_id=? AND deleted_at IS NULL ORDER BY id', (room['id'],)
        ).fetchall()
        room_data.append({'room': room, 'line_items': items, 'room_photos': photos})

    # Activity timeline
    activities = db.execute(
        'SELECT * FROM activity_log WHERE claim_id=? ORDER BY created_at DESC LIMIT 50', (claim_id,)
    ).fetchall()

    # Signature status
    sig = db.execute(
        'SELECT * FROM signatures WHERE claim_id=? ORDER BY id DESC LIMIT 1', (claim_id,)
    ).fetchone()

    # Previous and next status for timeline
    status_order = ['New', 'In Progress', 'Submitted', 'Closed']
    current_status = claim['status']
    current_idx = status_order.index(current_status) if current_status in status_order else 0

    return render_template(
        "portal.html",
        claim=claim,
        room_data=room_data,
        activities=activities,
        signature=sig,
        token=token,
        status_order=status_order,
        current_status_idx=current_idx,
        generated=datetime.datetime.now().strftime('%B %d, %Y'),
    )


@bp.route("/<token>/upload", methods=["POST"])
def portal_upload(token):
    """Customer upload photos from portal."""
    db = get_db()
    row = db.execute(
        'SELECT claim_id FROM client_portal_tokens WHERE token=?', (token,)
    ).fetchone()
    if not row:
        return jsonify({"error": "invalid_token"}), 403

    claim_id = row['claim_id']
    photos = request.files.getlist("photos")

    if not photos:
        return jsonify({"error": "no_photos"}), 400

    upload_dir = os.path.join(current_app.config.get("UPLOAD_FOLDER", "/data/uploads"), str(claim_id))
    os.makedirs(upload_dir, exist_ok=True)

    saved = 0
    for i, photo in enumerate(photos):
        if photo and photo.filename:
            filename = f"customer_{token[:8]}_{int(datetime.datetime.now().timestamp())}_{i}_{secure_filename(photo.filename)}"
            filepath = os.path.join(upload_dir, filename)
            photo.save(filepath)
            db.execute(
                'INSERT INTO photos (claim_id, filename, ai_description, customer_submitted, analysis_status) '
                'VALUES (?, ?, ?, 1, "pending")',
                (claim_id, filename, ""),
            )
            saved += 1

    # Log activity
    db.execute(
        'INSERT INTO activity_log (claim_id, actor, action) VALUES (?,?,?)',
        (claim_id, "Customer", f"Uploaded {saved} photo(s) via portal"),
    )
    db.commit()

    return jsonify({"success": True, "photos_uploaded": saved})


@bp.route("/<token>/sign", methods=["POST"])
def portal_sign(token):
    """Customer e-signature from portal."""
    db = get_db()
    row = db.execute(
        'SELECT claim_id FROM client_portal_tokens WHERE token=?', (token,)
    ).fetchone()
    if not row:
        return jsonify({"error": "invalid_token"}), 403

    claim_id = row['claim_id']
    data = request.get_json(silent=True) or {}
    sig_data = data.get("sig_data", "").strip()
    signer = data.get("signer", "Policyholder").strip()

    if not sig_data:
        return jsonify({"error": "sig_data_required"}), 400

    db.execute("DELETE FROM signatures WHERE claim_id=?", (claim_id,))
    db.execute(
        "INSERT INTO signatures (claim_id, signer, sig_data) VALUES (?,?,?)",
        (claim_id, signer, sig_data),
    )
    db.execute(
        'INSERT INTO activity_log (claim_id, actor, action) VALUES (?,?,?)',
        (claim_id, signer, "Signed Proof of Loss (e-signature via portal)"),
    )
    db.commit()

    return jsonify({"success": True, "message": f"Claim signed by {signer}"})


@bp.route("/<token>/status")
def portal_status(token):
    """API: Get claim status as JSON (for auto-refresh)."""
    db = get_db()
    row = db.execute(
        'SELECT claim_id FROM client_portal_tokens WHERE token=?', (token,)
    ).fetchone()
    if not row:
        return jsonify({"error": "invalid_token"}), 403

    claim = db.execute(
        "SELECT id, claim_number, status, total_estimate, updated_at FROM claims WHERE id=?",
        (row['claim_id'],)
    ).fetchone()
    if not claim:
        return jsonify({"error": "not_found"}), 404

    # Count unanalyzed photos
    pending_photos = db.execute(
        'SELECT COUNT(*) as cnt FROM photos WHERE claim_id=? AND analysis_status="pending"',
        (claim['id'],)
    ).fetchone()['cnt']

    return jsonify({
        "claim_number": claim["claim_number"],
        "status": claim["status"],
        "total_estimate": claim["total_estimate"],
        "updated_at": claim["updated_at"],
        "pending_photo_analysis": pending_photos,
    })


@bp.route("/<token>/activity")
def portal_activity(token):
    """API: Get activity log as JSON."""
    db = get_db()
    row = db.execute(
        'SELECT claim_id FROM client_portal_tokens WHERE token=?', (token,)
    ).fetchone()
    if not row:
        return jsonify({"error": "invalid_token"}), 403

    activities = db.execute(
        'SELECT actor, action, created_at FROM activity_log WHERE claim_id=? ORDER BY created_at DESC LIMIT 50',
        (row['claim_id'],)
    ).fetchall()

    return jsonify({
        "activities": [
            {"actor": a["actor"], "action": a["action"], "created_at": a["created_at"]}
            for a in activities
        ]
    })


# ── Adjender-Facing: Generate & Send Portal Link ──────────────────────────────

@bp.route("/claims/<int:claim_id>/portal/generate", methods=["POST"])
@login_required
def generate_portal_link(claim_id):
    """Generate a client portal link and optionally email it."""
    db = get_db()
    claim = db.execute("SELECT * FROM claims WHERE id=?", (claim_id,)).fetchone()
    if not claim:
        return jsonify({"error": "claim_not_found"}), 404

    token = secrets.token_urlsafe(24)
    db.execute("DELETE FROM client_portal_tokens WHERE claim_id=?", (claim_id,))
    db.execute(
        "INSERT INTO client_portal_tokens (claim_id, token) VALUES (?,?)", (claim_id, token)
    )
    db.commit()

    portal_url = url_for("portal.portal_home", token=token, _external=True)

    # Send email if client has email
    emailed = False
    if claim["client_email"]:
        subject = f"Your Flood Damage Claim — {claim['claim_number']}"
        html = f'''<div style="font-family:sans-serif;max-width:600px;margin:0 auto">
            <h2 style="color:#0a1628">Your Flood Claim Portal</h2>
            <p>Hello {claim["client_name"]},</p>
            <p>Your adjuster has created a portal for your flood damage claim.
            You can view the claim status, see photos and damage details, upload
            your own photos, and sign documents — all from your phone.</p>
            <p><a href="{portal_url}" style="background:#06D6C7;color:#0a1628;padding:14px 28px;border-radius:8px;text-decoration:none;display:inline-block;margin:16px 0;font-weight:700">Open My Claim Portal ↗</a></p>
            <p style="font-size:13px;color:#64748b">Claim: {claim["claim_number"]}<br>FloodClaims Pro</p></div>'''
        try:
            send_email(claim["client_email"], subject, html)
            emailed = True
        except Exception:
            pass

    # Generate SMS-friendly link
    sms_msg = f"FloodClaims Pro: View your claim {claim['claim_number']} — {portal_url}"

    return jsonify({
        "success": True,
        "portal_url": portal_url,
        "token": token,
        "emailed": emailed,
        "sms_message": sms_msg,
    })
