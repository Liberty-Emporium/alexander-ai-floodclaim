"""
Enhanced Claim Routes — NFIP Forms, Supplements, AI Analysis, Offline Mode.

These routes extend the base claims functionality with:
  1. NFIP Forms generation & download
  2. Supplement creation & comparison
  3. Enhanced AI photo analysis (water category/class)
  4. Offline data caching
"""
import datetime
import json

from flask import Blueprint, render_template, request, jsonify, redirect, url_for, session, flash, make_response
from models.database import get_db, get_setting
from utils.auth_decorators import login_required
from services.nfip_forms import generate_all_nfip_forms
from services.supplement import generate_supplement_data, generate_comparison_report, suggest_supplement_items
from services.flood_analysis import analyze_flood_photo, batch_analyze_photos
from services.offline import get_offline_cache_routes, get_offline_page_data

bp = Blueprint("enhanced", __name__)


# ═══════════════════════════════════════════════════════════════════════════════
# NFIP FORMS
# ═══════════════════════════════════════════════════════════════════════════════

@bp.route("/claims/<int:claim_id>/nfip-forms")
@login_required
def nfip_forms_page(claim_id):
    """Display all NFIP forms for a claim."""
    db = get_db()
    claim = _get_claim_or_404(db, claim_id)
    room_data = _get_room_data(db, claim_id)
    forms = generate_all_nfip_forms(dict(claim), _room_data_to_list(room_data))
    return render_template("nfip_forms.html", claim=claim, forms=forms)


@bp.route("/claims/<int:claim_id>/nfip-forms/<form_type>/download")
@login_required
def download_nfip_form(claim_id, form_type):
    """Download a specific NFIP form as text file."""
    db = get_db()
    claim = _get_claim_or_404(db, claim_id)
    room_data = _get_room_data(db, claim_id)
    forms = generate_all_nfip_forms(dict(claim), _room_data_to_list(room_data))

    form_type_map = {
        'proof_of_loss': ('NFIP_Proof_of_Loss', 'Proof of Loss'),
        'building_worksheet': ('NFIP_Building_Worksheet', 'Building Property Worksheet'),
        'contents_worksheet': ('NFIP_Contents_Worksheet', 'Contents Property Worksheet'),
        'prior_loss': ('NFIP_Prior_Loss', 'Prior Loss History'),
        'cause_of_loss': ('NFIP_Cause_of_Loss', 'Cause of Loss Report'),
    }

    if form_type not in form_type_map:
        flash(f"Unknown form type: {form_type}", "error")
        return redirect(url_for("enhanced.nfip_forms_page", claim_id=claim_id))

    filename_base, display_name = form_type_map[form_type]
    content = forms.get(form_type, 'Form not available.')
    claim_num = claim['claim_number'] if claim else 'unknown'

    resp = make_response(content)
    resp.headers['Content-Type'] = 'text/plain; charset=utf-8'
    resp.headers['Content-Disposition'] = f'attachment; filename="{filename_base}_{claim_num}.txt"'
    return resp


@bp.route("/claims/<int:claim_id>/nfip-forms/download-all")
@login_required
def download_all_nfip_forms(claim_id):
    """Download all NFIP forms as a combined text file."""
    db = get_db()
    claim = _get_claim_or_404(db, claim_id)
    room_data = _get_room_data(db, claim_id)
    forms = generate_all_nfip_forms(dict(claim), _room_data_to_list(room_data))

    combined = "\n\n" + "=" * 80 + "\n\n".join(
        [f"--- {name.upper()} ---\n\n{content}" for name, content in forms.items()]
    )

    claim_num = claim['claim_number'] if claim else 'unknown'
    resp = make_response(combined)
    resp.headers['Content-Type'] = 'text/plain; charset=utf-8'
    resp.headers['Content-Disposition'] = f'attachment; filename="NFIP_All_Forms_{claim_num}.txt"'
    return resp


# ═══════════════════════════════════════════════════════════════════════════════
# SUPPLEMENTS
# ═══════════════════════════════════════════════════════════════════════════════

@bp.route("/claims/<int:claim_id>/supplement", methods=["GET"])
@login_required
def supplement_page(claim_id):
    """Display supplement builder page."""
    db = get_db()
    claim = _get_claim_or_404(db, claim_id)
    room_data = _get_room_data(db, claim_id)

    # Get AI suggestions for supplement items
    photos = db.execute(
        'SELECT id, ai_description FROM photos WHERE claim_id=? AND deleted_at IS NULL',
        (claim_id,)
    ).fetchall()
    ai_descriptions = {str(p['id']): p['ai_description'] for p in photos if p['ai_description']}
    suggestions = suggest_supplement_items(dict(claim), _room_data_to_list(room_data), ai_descriptions)

    return render_template(
        "supplement.html",
        claim=claim,
        room_data=room_data,
        suggestions=suggestions,
    )


@bp.route("/claims/<int:claim_id>/supplement/generate", methods=["POST"])
@login_required
def generate_supplement(claim_id):
    """Generate a supplement from posted data."""
    db = get_db()
    claim = _get_claim_or_404(db, claim_id)
    room_data = _get_room_data(db, claim_id)

    data = request.get_json(silent=True) or request.form
    items = data.get("items", [])
    reason = data.get("reason", "additional_damage")

    if isinstance(items, str):
        items = json.loads(items)

    result = generate_supplement_data(
        claim_id, dict(claim), _room_data_to_list(room_data), items, reason
    )
    return jsonify(result)


@bp.route("/claims/<int:claim_id>/supplement/suggestions")
@login_required
def supplement_suggestions(claim_id):
    """Get AI-powered supplement suggestions for a claim."""
    db = get_db()
    claim = _get_claim_or_404(db, claim_id)
    room_data = _get_room_data(db, claim_id)

    photos = db.execute(
        'SELECT id, ai_description FROM photos WHERE claim_id=? AND deleted_at IS NULL',
        (claim_id,)
    ).fetchall()
    ai_descriptions = {str(p['id']): p['ai_description'] for p in photos if p['ai_description']}
    suggestions = suggest_supplement_items(dict(claim), _room_data_to_list(room_data), ai_descriptions)

    return jsonify({"suggestions": suggestions})


@bp.route("/claims/<int:claim_id>/supplement/download", methods=["POST"])
@login_required
def download_supplement(claim_id):
    """Download supplement as text file."""
    db = get_db()
    claim = _get_claim_or_404(db, claim_id)
    room_data = _get_room_data(db, claim_id)

    data = request.get_json(silent=True) or {}
    items = data.get("items", [])
    reason = data.get("reason", "additional_damage")

    result = generate_supplement_data(
        claim_id, dict(claim), _room_data_to_list(room_data), items, reason
    )

    claim_num = claim['claim_number'] if claim else 'unknown'
    resp = make_response(result['supplement_text'])
    resp.headers['Content-Type'] = 'text/plain; charset=utf-8'
    resp.headers['Content-Disposition'] = f'attachment; filename="Supplement_{claim_num}.txt"'
    return resp


# ═══════════════════════════════════════════════════════════════════════════════
# ENHANCED AI ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════

@bp.route("/claims/<int:claim_id>/ai-analyze/<int:photo_id>", methods=["POST"])
@login_required
def ai_analyze_photo(claim_id, photo_id):
    """Run enhanced flood analysis on a single photo (water category/class)."""
    db = get_db()
    claim = _get_claim_or_404(db, claim_id)
    photo = db.execute(
        'SELECT * FROM photos WHERE id=? AND claim_id=? AND deleted_at IS NULL',
        (photo_id, claim_id)
    ).fetchone()
    if not photo:
        return jsonify({"error": "photo_not_found"}), 404

    upload_dir = get_setting('upload_dir', '/data/uploads')
    image_path = os.path.join(upload_dir, str(claim_id), photo['filename'])
    if not os.path.exists(image_path):
        image_path = os.path.join(upload_dir, photo['filename'])

    claim_context = {
        'flood_source': claim.get('flood_source', ''),
        'water_category': claim.get('water_category', ''),
        'water_class': claim.get('water_class', ''),
        'property_type': claim.get('property_type', ''),
    }

    result = analyze_flood_photo(image_path, claim_context)

    # Update photo record with AI results
    db.execute(
        'UPDATE photos SET ai_description=?, water_category=?, water_class=?, '
        'ai_damage_severity=?, ai_room_type=?, ai_water_evidence=?, '
        'ai_mold_detected=?, ai_structural_damage=?, ai_flooring_type=?, '
        'ai_flooring_damage=?, ai_wall_damage=?, ai_suggested_items=?, '
        'ai_analysis_json=?, analysis_status="complete" WHERE id=?',
        (
            result['description'],
            str(result['water_category']),
            str(result['water_class']),
            result['damage_severity'],
            result['room_type'],
            json.dumps(result.get('water_evidence', {})),
            1 if result.get('mold_detected') else 0,
            1 if result.get('structural_damage') else 0,
            result.get('flooring_type', ''),
            result.get('flooring_damage', ''),
            result.get('wall_damage', ''),
            json.dumps(result.get('suggested_items', [])),
            json.dumps(result),
            photo_id,
        )
    )

    # Update claim water category/class if AI found worse
    updates = []
    params = []
    current_cat = claim.get('water_category', 5)  # Default high
    current_class = claim.get('water_class', 5)
    try:
        current_cat = int(current_cat) if current_cat else 5
    except (ValueError, TypeError):
        current_cat = 5
    try:
        current_class = int(current_class) if current_class else 5
    except (ValueError, TypeError):
        current_class = 5

    if result['water_category'] < current_cat:
        updates.append('water_category=?')
        params.append(result['water_category'])
    if result['water_class'] < current_class:
        updates.append('water_class=?')
        params.append(result['water_class'])

    if updates:
        params.append(claim_id)
        db.execute(f'UPDATE claims SET {", ".join(updates)} WHERE id=?', params)

    db.commit()
    return jsonify(result)


@bp.route("/claims/<int:claim_id>/ai-analyze-batch", methods=["POST"])
@login_required
def ai_analyze_batch(claim_id):
    """Run enhanced flood analysis on ALL unanalyzed photos for a claim."""
    db = get_db()
    claim = _get_claim_or_404(db, claim_id)

    photos = db.execute(
        'SELECT * FROM photos WHERE claim_id=? AND deleted_at IS NULL '
        'AND (ai_analysis_json IS NULL OR ai_analysis_json="")',
        (claim_id,)
    ).fetchall()

    if not photos:
        return jsonify({"message": "No unanalyzed photos", "analyzed": 0})

    upload_dir = get_setting('upload_dir', '/data/uploads')
    photo_paths = []
    for p in photos:
        path = os.path.join(upload_dir, str(claim_id), p['filename'])
        if not os.path.exists(path):
            path = os.path.join(upload_dir, p['filename'])
        if os.path.exists(path):
            photo_paths.append((p['id'], path))

    claim_context = {
        'flood_source': claim.get('flood_source', ''),
        'water_category': claim.get('water_category', ''),
        'water_class': claim.get('water_class', ''),
        'property_type': claim.get('property_type', ''),
    }

    results = []
    for photo_id, path in photo_paths:
        result = analyze_flood_photo(path, claim_context)
        result['photo_id'] = photo_id
        results.append(result)

        db.execute(
            'UPDATE photos SET ai_description=?, water_category=?, water_class=?, '
            'ai_damage_severity=?, ai_room_type=?, ai_water_evidence=?, '
            'ai_mold_detected=?, ai_structural_damage=?, ai_flooring_type=?, '
            'ai_flooring_damage=?, ai_wall_damage=?, ai_suggested_items=?, '
            'ai_analysis_json=?, analysis_status="complete" WHERE id=?',
            (
                result['description'],
                str(result['water_category']),
                str(result['water_class']),
                result['damage_severity'],
                result['room_type'],
                json.dumps(result.get('water_evidence', {})),
                1 if result.get('mold_detected') else 0,
                1 if result.get('structural_damage') else 0,
                result.get('flooring_type', ''),
                result.get('flooring_damage', ''),
                result.get('wall_damage', ''),
                json.dumps(result.get('suggested_items', [])),
                json.dumps(result),
                photo_id,
            )
        )

    db.commit()

    # Aggregate results
    categories = [r['water_category'] for r in results if r.get('water_category')]
    classes = [r['water_class'] for r in results if r.get('water_class')]
    mold_photos = [r['photo_id'] for r in results if r.get('mold_detected')]

    return jsonify({
        "analyzed": len(results),
        "aggregate_water_category": max(categories) if categories else 3,
        "aggregate_water_class": max(classes) if classes else 2,
        "mold_detected_in": mold_photos,
        "results": [{"photo_id": r['photo_id'], "description": r['description'],
                      "water_category": r['water_category'], "water_class": r['water_class'],
                      "damage_severity": r['damage_severity'], "room_type": r['room_type']}
                     for r in results],
    })


# ═══════════════════════════════════════════════════════════════════════════════
# OFFLINE MODE
# ═══════════════════════════════════════════════════════════════════════════════

@bp.route("/claims/<int:claim_id>/offline-data")
@login_required
def offline_data(claim_id):
    """Return claim data as JSON for offline caching."""
    db = get_db()
    claim = _get_claim_or_404(db, claim_id)
    room_data = _get_room_data(db, claim_id)
    data = get_offline_page_data(claim_id, dict(claim), _room_data_to_list(room_data))
    return jsonify(data)


@bp.route("/offline.js")
def offline_js():
    """Serve the offline cache JavaScript."""
    js = get_offline_cache_routes()
    resp = make_response(js)
    resp.headers['Content-Type'] = 'application/javascript'
    return resp


# ═══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

import os

def _get_claim_or_404(db, claim_id):
    claim = db.execute('SELECT * FROM claims WHERE id=?', (claim_id,)).fetchone()
    if not claim:
        from flask import abort
        abort(404)
    return claim


def _get_room_data(db, claim_id):
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
    return room_data


def _room_data_to_list(room_data):
    """Convert room_data to list format for NFIP/supplement services."""
    return [
        {
            'room': dict(rd['room']) if hasattr(rd['room'], 'keys') else rd['room'],
            'line_items': [dict(i) if hasattr(i, 'keys') else i for i in rd['line_items']],
        }
        for rd in room_data
    ]
