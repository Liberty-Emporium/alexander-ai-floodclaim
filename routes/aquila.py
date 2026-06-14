"""
Aquila 3D Integration Blueprint for FloodClaims Pro.

Connects FloodClaims Pro photo workflow to Aquila's AI damage analysis
and 3D model generation pipeline.

Endpoints:
  POST /claims/<id>/aquila/analyze     → Analyze all claim photos with Aquila
  POST /claims/<id>/aquila/generate-3d  → Generate 3D model from claim photos
  GET  /claims/<id>/aquila/status       → Check analysis/generation status
  GET  /claims/<id>/aquila/report       → Get full damage report
  GET  /claims/<id>/aquila/model/<mid>  → Download generated 3D model
"""

import os
import json
import secrets
import datetime
import threading
import pathlib

from flask import (
    Blueprint, render_template, request, redirect, url_for,
    session, flash, jsonify, send_from_directory, current_app
)
from models.database import get_db, get_setting
from utils.auth_decorators import login_required

bp = Blueprint("aquila", __name__)

# ── Aquila service URL (configurable via settings) ──────────────────────────
AQUILA_BASE_URL = os.environ.get("AQUILA_API_URL", "http://localhost:8000")

# ── Background job storage (in production, use Redis or DB table) ────────────
_aquila_jobs = {}
_jobs_lock = threading.Lock()


def _get_aquila_url(path):
    """Build Aquila API URL."""
    return f"{AQUILA_BASE_URL}{path}"


def _submit_aquila_job(job_type, claim_id, photo_paths, params=None, upload_dir=None):
    """Submit a job to Aquila and track it."""
    job_id = f"aq-{secrets.token_hex(8)}"
    job = {
        "id": job_id,
        "type": job_type,
        "claim_id": claim_id,
        "status": "pending",
        "progress": 0,
        "progress_msg": "Submitting to Aquila...",
        "result": None,
        "error": None,
        "created_at": datetime.datetime.now().isoformat(),
    }
    with _jobs_lock:
        _aquila_jobs[job_id] = job

    # Launch background thread. upload_dir is captured here in the request
    # context and passed in — the thread has no app context to read config from.
    t = threading.Thread(
        target=_run_aquila_job,
        args=(job_id, job_type, photo_paths, params or {}, upload_dir or "/data/uploads"),
        daemon=True,
    )
    t.start()
    return job_id


def _run_aquila_job(job_id, job_type, photo_paths, params, upload_dir="/data/uploads"):
    """Background thread: call Aquila API and update job status."""
    import requests as _req

    with _jobs_lock:
        job = _aquila_jobs.get(job_id)
    if not job:
        return

    try:
        if job_type == "analyze":
            job["progress_msg"] = "Analyzing damage in photos..."
            job["progress"] = 20
            files = []
            for p in photo_paths:
                if os.path.exists(p):
                    files.append(("files", open(p, "rb")))
            resp = _req.post(
                _get_aquila_url("/api/v1/analyze"),
                files=files,
                data={"claim_id": str(job["claim_id"])},
                timeout=120,
            )
            for _, f in files:
                f.close()
            if resp.status_code == 200:
                job["result"] = resp.json()
                job["status"] = "done"
                job["progress"] = 100
                job["progress_msg"] = "Analysis complete"
            else:
                job["status"] = "error"
                job["error"] = f"Aquila returned {resp.status_code}: {resp.text[:200]}"

        elif job_type == "generate_3d":
            job["progress_msg"] = "Generating 3D model..."
            job["progress"] = 10
            files = []
            for p in photo_paths:
                if os.path.exists(p):
                    files.append(("files", open(p, "rb")))
            resp = _req.post(
                _get_aquila_url("/api/v1/generate-3d"),
                files=files,
                data={
                    "claim_id": str(job["claim_id"]),
                    "method": params.get("method", "auto"),
                },
                timeout=300,
            )
            for _, f in files:
                f.close()
            if resp.status_code == 200:
                # Save the GLB file
                model_filename = f"aquila_3d_{job_id}.glb"
                model_path = os.path.join(upload_dir, model_filename)
                with open(model_path, "wb") as f:
                    f.write(resp.content)
                job["result"] = {
                    "model_id": job_id,
                    "filename": model_filename,
                    "file_path": model_path,
                    "file_size": os.path.getsize(model_path),
                    "format": "glb",
                }
                job["status"] = "done"
                job["progress"] = 100
                job["progress_msg"] = "3D model generated"
            else:
                job["status"] = "error"
                job["error"] = f"Aquila returned {resp.status_code}: {resp.text[:200]}"

    except Exception as e:
        job["status"] = "error"
        msg = str(e)
        # Connection failures mean the separate Aquila 3D service isn't reachable.
        # Give a clear, non-technical message instead of a raw stack trace.
        if "Connection refused" in msg or "Max retries" in msg or "Failed to establish" in msg or "NewConnectionError" in msg:
            job["error"] = ("The Aquila 3D analysis service isn't available right now. "
                            "This feature requires the Aquila service to be running and "
                            "configured (AQUILA_API_URL). Contact Jay to enable it.")
        else:
            job["error"] = msg

    with _jobs_lock:
        _aquila_jobs[job_id] = job


# ── Routes ──────────────────────────────────────────────────────────────────


@bp.route("/claims/<int:claim_id>/aquila/analyze", methods=["POST"])
@login_required
def aquila_analyze(claim_id):
    """Analyze all photos in a claim using Aquila AI."""
    db = get_db()
    claim = db.execute("SELECT * FROM claims WHERE id=?", (claim_id,)).fetchone()
    if not claim:
        return jsonify({"ok": False, "error": "Claim not found"}), 404

    # Get all photo paths for this claim
    photos = db.execute(
        "SELECT * FROM photos WHERE claim_id=? AND deleted_at IS NULL ORDER BY id",
        (claim_id,),
    ).fetchall()

    if not photos:
        return jsonify({"ok": False, "error": "No photos uploaded for this claim"}), 400

    upload_dir = current_app.config.get("UPLOAD_DIR", "/data/uploads")
    photo_paths = []
    for p in photos:
        full_path = os.path.join(upload_dir, p["filename"])
        if os.path.exists(full_path):
            photo_paths.append(full_path)

    if not photo_paths:
        return jsonify({"ok": False, "error": "Photo files not found on disk"}), 400

    # Submit to Aquila
    job_id = _submit_aquila_job("analyze", claim_id, photo_paths, upload_dir=upload_dir)

    return jsonify({
        "ok": True,
        "job_id": job_id,
        "status": "pending",
        "photo_count": len(photo_paths),
        "poll_url": f"/claims/{claim_id}/aquila/status?job_id={job_id}",
    })


@bp.route("/claims/<int:claim_id>/aquila/generate-3d", methods=["POST"])
@login_required
def aquila_generate_3d(claim_id):
    """Generate a 3D model from claim photos."""
    db = get_db()
    claim = db.execute("SELECT * FROM claims WHERE id=?", (claim_id,)).fetchone()
    if not claim:
        return jsonify({"ok": False, "error": "Claim not found"}), 404

    photos = db.execute(
        "SELECT * FROM photos WHERE claim_id=? AND deleted_at IS NULL ORDER BY id",
        (claim_id,),
    ).fetchall()

    if not photos:
        return jsonify({"ok": False, "error": "No photos uploaded"}), 400

    upload_dir = current_app.config.get("UPLOAD_DIR", "/data/uploads")
    photo_paths = []
    for p in photos:
        full_path = os.path.join(upload_dir, p["filename"])
        if os.path.exists(full_path):
            photo_paths.append(full_path)

    if not photo_paths:
        return jsonify({"ok": False, "error": "Photo files not found"}), 400

    data = request.get_json(silent=True) or {}
    method = data.get("method", "auto")

    job_id = _submit_aquila_job("generate_3d", claim_id, photo_paths, {"method": method}, upload_dir=upload_dir)

    return jsonify({
        "ok": True,
        "job_id": job_id,
        "status": "pending",
        "photo_count": len(photo_paths),
        "poll_url": f"/claims/{claim_id}/aquila/status?job_id={job_id}",
    })


@bp.route("/claims/<int:claim_id>/aquila/status", methods=["GET"])
@login_required
def aquila_status(claim_id):
    """Check status of an Aquila job."""
    job_id = request.args.get("job_id")
    if not job_id:
        return jsonify({"ok": False, "error": "job_id required"}), 400

    with _jobs_lock:
        job = _aquila_jobs.get(job_id)

    if not job:
        return jsonify({"ok": False, "error": "Job not found"}), 404

    return jsonify({
        "ok": True,
        "job_id": job_id,
        "type": job["type"],
        "status": job["status"],
        "progress": job["progress"],
        "progress_msg": job["progress_msg"],
        "result": job["result"],
        "error": job["error"],
    })


@bp.route("/claims/<int:claim_id>/aquila/report", methods=["GET"])
@login_required
def aquila_report(claim_id):
    """Get the latest Aquila damage report for a claim."""
    job_id = request.args.get("job_id")
    if not job_id:
        return jsonify({"ok": False, "error": "job_id required"}), 400

    with _jobs_lock:
        job = _aquila_jobs.get(job_id)

    if not job:
        return jsonify({"ok": False, "error": "Job not found"}), 404

    if job["status"] != "done":
        return jsonify({"ok": False, "error": f"Job status: {job['status']}"}), 202

    return jsonify({
        "ok": True,
        "report": job["result"],
    })


@bp.route("/claims/<int:claim_id>/aquila/model/<job_id>", methods=["GET"])
@login_required
def aquila_download_model(claim_id, job_id):
    """Download a generated 3D model file."""
    with _jobs_lock:
        job = _aquila_jobs.get(job_id)

    if not job or job["status"] != "done":
        return jsonify({"ok": False, "error": "Model not ready"}), 404

    result = job.get("result", {})
    file_path = result.get("file_path")
    if not file_path or not os.path.exists(file_path):
        return jsonify({"ok": False, "error": "Model file not found"}), 404

    upload_dir = current_app.config.get("UPLOAD_DIR", "/data/uploads")
    filename = result.get("filename", f"{job_id}.glb")

    return send_from_directory(
        upload_dir,
        filename,
        as_attachment=True,
        download_name=f"aquila_3d_model_{claim_id}.glb",
    )


@bp.route("/claims/<int:claim_id>/aquila/panel")
@login_required
def aquila_panel(claim_id):
    """Render the Aquila 3D analysis panel for a claim."""
    db = get_db()
    claim = db.execute("SELECT * FROM claims WHERE id=?", (claim_id,)).fetchone()
    if not claim:
        flash("Claim not found.", "error")
        return redirect(url_for("auth.dashboard"))

    photos = db.execute(
        "SELECT * FROM photos WHERE claim_id=? AND deleted_at IS NULL ORDER BY id",
        (claim_id,),
    ).fetchall()

    return render_template(
        "aquila_panel.html",
        claim=claim,
        photos=photos,
        aquila_url=AQUILA_BASE_URL,
    )
