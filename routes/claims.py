"""Routes for claims blueprint."""

from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify, send_from_directory
from models.database import get_db, get_setting, set_setting
from utils.auth_decorators import login_required, admin_required, manager_required
from utils.security import allowed_file, csrf_required
from services.ai import call_openrouter, ai_describe_photo, ai_describe_photo_detailed
from services.email import send_email, notify_client_status_change
from services.fema import lookup_fema_flood_zone
from services.claims import gen_claim_number, recalc_claim
import json
import datetime
import os
import pathlib

bp = Blueprint("claims", __name__)

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




@bp.route('/claims/<int:claim_id>/ai-estimate', methods=['POST'])
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




@bp.route('/claims/<int:claim_id>/ai-estimate/<int:job_id>', methods=['GET'])
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




@bp.route('/claims/<int:claim_id>/update-estimate', methods=['POST'])
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


@bp.route('/claims/new', methods=['GET', 'POST'])
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



@bp.route('/claims/<int:claim_id>/delete', methods=['POST'])
@login_required
@csrf_required
def delete_claim(claim_id):
    """Delete a claim and all its rooms, line items, and photos."""
    db = get_db()
    claim = db.execute('SELECT id, client_name, claim_number FROM claims WHERE id=?', (claim_id,)).fetchone()
    if not claim:
        flash('Claim not found.', 'error')
        return redirect(url_for('auth.dashboard'))
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
    return redirect(url_for('auth.dashboard'))




@bp.route('/claims/<int:claim_id>/nfip-fill', methods=['POST'])
@login_required
@csrf_required
def nfip_quick_fill(claim_id):
    """Quick-fill all NFIP compliance fields in one shot."""
    db = get_db()
    claim = db.execute('SELECT id FROM claims WHERE id=?', (claim_id,)).fetchone()
    if not claim:
        flash('Claim not found.', 'error')
        return redirect(url_for('auth.dashboard'))
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




@bp.route('/claims/<int:claim_id>/notes', methods=['POST'])
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




@bp.route('/claims/<int:claim_id>')
@login_required
def claim_detail(claim_id):
    try:
        db = get_db()
        claim = db.execute('''SELECT c.*, u.name as adjuster_name
            FROM claims c LEFT JOIN users u ON c.adjuster_id=u.id WHERE c.id=?''',
            (claim_id,)).fetchone()
        if not claim:
            flash('Claim not found.', 'error')
            return redirect(url_for('auth.dashboard'))
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
            return redirect(url_for('auth.dashboard'))
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
        return redirect(url_for('auth.dashboard'))



@bp.route('/claims/<int:claim_id>/mobile')
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
            return redirect(url_for('auth.dashboard'))
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
        return redirect(url_for('auth.dashboard'))



@bp.route('/claims/<int:claim_id>/status', methods=['POST'])
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



@bp.route('/claims/<int:claim_id>/duplicate', methods=['POST'])
@login_required
@csrf_required
def duplicate_claim(claim_id):
    db    = get_db()
    src   = db.execute('SELECT * FROM claims WHERE id=?', (claim_id,)).fetchone()
    if not src:
        flash('Claim not found.', 'error')
        return redirect(url_for('auth.dashboard'))
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



@bp.route('/claims/<int:claim_id>/activity')
@login_required
def claim_activity(claim_id):
    db    = get_db()
    claim = db.execute('SELECT * FROM claims WHERE id=?', (claim_id,)).fetchone()
    if not claim:
        flash('Claim not found.', 'error')
        return redirect(url_for('auth.dashboard'))
    logs = db.execute(
        'SELECT * FROM activity_log WHERE claim_id=? ORDER BY created_at DESC LIMIT 100',
        (claim_id,)
    ).fetchall()
    return render_template('activity.html', claim=claim, logs=logs)


# ─────────────────────────────────────────────────────────────────────────────
# SMS VIA TWILIO
# ─────────────────────────────────────────────────────────────────────────────



@bp.route('/claims/<int:claim_id>/sms', methods=['POST'])
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



@bp.route('/claims/<int:claim_id>/mobile-upload')
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




@bp.route('/claims/<int:claim_id>/mobile-upload', methods=['POST'])
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




@bp.route('/claims/<int:claim_id>/qr')
@login_required
def claim_qr(claim_id):
    """Show a QR code for mobile photo upload — generates/reuses the portal token."""
    db    = get_db()
    claim = db.execute('SELECT * FROM claims WHERE id=?', (claim_id,)).fetchone()
    if not claim:
        flash('Claim not found.', 'error')
        return redirect(url_for('auth.dashboard'))
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



@bp.route('/claims/bulk', methods=['POST'])
@login_required
@csrf_required
def bulk_action():
    action   = request.form.get('bulk_action', '')
    ids_raw  = request.form.getlist('claim_ids')
    ids      = [int(i) for i in ids_raw if i.isdigit()]
    if not ids:
        flash('No claims selected.', 'error')
        return redirect(url_for('auth.dashboard'))
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
    return redirect(url_for('auth.dashboard'))


# ─────────────────────────────────────────────────────────────────────────────
# WEEKLY SUMMARY REPORT (email)
# ─────────────────────────────────────────────────────────────────────────────



@bp.route('/claims/<int:claim_id>/compliance')
@login_required
def compliance(claim_id):
    db    = get_db()
    claim = db.execute('SELECT * FROM claims WHERE id=?', (claim_id,)).fetchone()
    if not claim:
        flash('Claim not found.', 'error')
        return redirect(url_for('auth.dashboard'))
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



@bp.route('/claims/<int:claim_id>/submit')
@login_required
def submit_package_page(claim_id):
    """Show the Submit Package page for a claim."""
    db    = get_db()
    claim = db.execute('''SELECT c.*, u.name as adjuster_name, u.email as adjuster_email
        FROM claims c LEFT JOIN users u ON c.adjuster_id=u.id WHERE c.id=?''', (claim_id,)).fetchone()
    if not claim:
        flash('Claim not found.', 'error')
        return redirect(url_for('auth.dashboard'))
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




@bp.route('/claims/<int:claim_id>/submit/download', methods=['POST'])
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
        return redirect(url_for('auth.dashboard'))
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




@bp.route('/claims', methods=['GET'])
def claims_list():
    """Claims list — redirects to dashboard which shows claims."""
    return redirect(url_for('auth.dashboard'))

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





@bp.route('/claim/<int:claim_id>/room/<int:room_id>/batch-analyze', methods=['POST'])
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




@bp.route('/claim/<int:claim_id>/ai-populate', methods=['POST'])
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



