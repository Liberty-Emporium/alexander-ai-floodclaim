"""AI service — OpenRouter calls and photo analysis.

Extracted from app.py Phase 2 (lines 797-1556).
"""
import os
import base64
import threading
import secrets

import requests as _req

# These are imported from the parent app context at runtime
# (app.py sets them as module-level vars before services are used)
OPENROUTER_KEY = None
UPLOAD_DIR = '/tmp/uploads'
DB_PATH = None
_app_ctx = None  # set by app.py after Flask app init


def _get_setting(key, default=''):
    """Read a setting from the DB, falling back to default."""
    import sqlite3
    try:
        db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
        row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        db.close()
        return row['value'] if row else default
    except Exception:
        return default


def call_openrouter(messages, model, key, max_tokens=4000):
    """Call OpenRouter chat completions API with automatic fallback. Returns response text or error string."""
    fallback_model = _get_setting('ai_fallback_model', 'anthropic/claude-sonnet-4-5')
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


def _build_pricing_kb():
    """2026 Flood Restoration Pricing Reference — used by AI estimate jobs."""
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
    """Build the AI estimate prompt from claim data and pricing reference."""
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
        import time as _time
        estimate = call_openrouter([{'role': 'user', 'content': prompt}], model, key, max_tokens=4000)
        _update(90, 'Processing and formatting estimate results...')
        try:
            import re as _re
            total_matches = [_re.search(r'GRAND TOTAL[:\\s]*\\$?([\\d,]+\\.?\\d*)', estimate, _re.IGNORECASE),
                           _re.search(r'(?:Total|Grand Total|Claim Amount)[:\\s]*\\$?([\\d,]+\\.?\\d*)', estimate, _re.IGNORECASE)]
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


def ai_describe_photo(image_path):
    """Describe flood damage in a photo using vision AI. Returns description or empty string."""
    key = OPENROUTER_KEY
    if not key:
        return ''
    try:
        with open(image_path, 'rb') as f:
            img_b64 = base64.b64encode(f.read()).decode()
        ext  = image_path.rsplit('.', 1)[-1].lower()
        mime = f'image/{ext}' if ext != 'jpg' else 'image/jpeg'
        model = _get_setting('ai_vision_model') or _get_setting('ai_model', 'openrouter/auto')
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
        if result.startswith('Error:'):
            return ''
        if result.startswith('[Used fallback:'):
            result = result.split(']\n\n', 1)[-1] if ']\n\n' in result else result
        return result
    except Exception:
        return ''


def ai_describe_photo_detailed(image_path, key, model):
    """Run vision AI on a photo with a detailed damage-focused prompt, customized by brain training."""
    try:
        custom_prompt = _get_setting('brain_photo_prompt', '')
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
