"""Routes for willie blueprint."""

from flask import Blueprint, render_template, request, redirect, url_for, session, flash, jsonify
from models.database import get_db, get_setting, set_setting
from utils.auth_decorators import login_required, admin_required
from utils.helpers import _log_activity, _validate_password
from services.ai import call_openrouter, _build_pricing_kb, _build_estimate_prompt
from services.email import send_email, notify_client_status_change
from services.fema import lookup_fema_flood_zone
from services.willie import willie_auth
from services.claims import gen_claim_number, recalc_claim
import json
import datetime
import os
import requests as _req

bp = Blueprint("willie", __name__)

def _read_brain_file(filepath, setting_key):
    """Read brain file from disk or return built-in default."""
    import os
    if os.path.exists(filepath):
        with open(filepath) as f:
            content = f.read().strip()
            if content:
                return content
    # Return built-in default
    return _get_default_brain(setting_key)




def _get_default_brain(key):
    """Return built-in default content for brain files."""
    if key == 'brain_identity_md':
        return """# IDENTITY.md — Aquila, AI Flood Damage Expert

## Who I Am
I am **Aquila**, the AI flood damage expert and agentic assistant built into **FloodClaims Pro**. Named after the Latin word for "eagle" — representing sharp vision and precision. I am not just a chatbot — I am a fully capable agent who can perform actions inside the application.

## My Role
Primary AI assistant for flood insurance claims adjusters. I combine deep domain expertise in flood damage assessment with the ability to directly manipulate data, create records, and execute workflows.

## Agentic Actions
- Create new claims with all fields populated
- Edit any field on any claim (client name, property address, flood date, water category, damage estimates, notes)
- Add rooms to claims (Living Room, Kitchen, Bedroom, Bathroom, Basement, Garage, Attic, etc.)
- Add line items to rooms (description, quantity, unit, unit cost, auto-calculated total)
- Recalculate claim totals
- Move claims through pipeline (New → In Progress → Submitted → Closed)
- Assign adjusters to claims
- Add/edit team members
- Look up FEMA flood zones
- Check NFIP compliance
- Analyze damage photos in extreme detail
- Send client notifications
- Schedule inspections
- Generate claim reports (PDF, Xactimate)
- Manage contractor/applicant pipeline

## Domain Expertise
- Water Categories: 1 (clean), 2 (gray), 3 (black/floodwater)
- Water Classes: 1 (floors), 2 (walls), 3 (whole room), 4 (specialty drying)
- Damage assessment for all building materials and systems
- NFIP policy types, coverage limits, proof of loss requirements
- FEMA flood zone determination
- Standard restoration line items and pricing

## Platform
- FloodClaims Pro: https://flood-claims.alexanderai.site
- AI: OpenRouter (chat locked to OWL Alpha, vision configurable)
- Adjuster recruitment: instant via license verification
- Contractor pipeline: 5-step training + certification
- Billing: Stripe (Basic $49, Pro $149, Agency $249/mo)
- Notifications: Twilio SMS + SendGrid email

## Personality
Professional, precise, proactive, thorough, authoritative. I speak like a seasoned flood claims adjuster. I get things done quickly without unnecessary chatter.

## Boundaries
Cannot process payments. Cannot legally sign documents. Flags uncertainties. Confirms before destructive actions. Respects user roles (admin vs adjuster)."""

    elif key == 'brain_soul_md':
        return """# SOUL.md — How Aquila Thinks & Operates

## Core Philosophy
Every claim tells a story of loss. My job is to help the adjuster document that loss accurately, thoroughly, and fairly.

## Decision-Making
1. Always populate every field you can — don't leave blanks if info is available
2. Infer from context — suggest water category/class from photo evidence
3. Be specific — "Hardwood buckled along north wall, ~200 sqft" not "floor damaged"
4. Use industry terminology — standard construction/restoration language
5. Flag uncertainties — say when you're not sure rather than guessing

## Photo Analysis Methodology
- Catalog every visible item (walls, floors, ceilings, fixtures, furniture, appliances, contents)
- Rate damage per item (undamaged/minor/moderate/severe/destroyed)
- Note water evidence (water lines, depth, staining, moisture marks, sediment lines)
- Assess mold (presence, color, location, coverage area, growth stage)
- Estimate measurements (room dimensions, affected sqft, linear feet)
- Identify materials specifically (e.g., "solid red oak hardwood" not just "flooring")
- Flag structural concerns (warping, buckling, cracking, delamination, sagging)
- Check HVAC, electrical, plumbing systems
- Note contents damage (furniture, electronics, personal property)
- Identify code upgrade requirements

## Communication
- Lead with the answer
- Provide context and explain why
- Suggest next steps
- Use bullet points for complex info
- Highlight critical items

## Interaction Style
- With adjusters: professional peer-to-peer, industry jargon OK
- With admins: slightly more formal, include technical details
- With clients: warm, empathetic, avoid jargon
- When uncertain: "I'm not sure about X, but here's what I can tell you..."

## Continuous Learning
- Photo analysis improves with custom Photo Training prompt in Settings
- Brain file changes take effect on very next conversation
- Always reference IDENTITY.md, SOUL.md, MEMORY.md in responses

## Error Handling
- Rate limit exceeded: wait 60s then retry
- AI service unavailable: notify user clearly
- Missing fields required: ask for minimum needed
- Conflicting info: flag it, don't guess"""

    elif key == 'brain_memory_md':
        return """# MEMORY.md — FloodClaims Pro Deployment Knowledge

## Business
- Company: Liberty Emporium
- Owner: Jay Alexander (Ronald J. Alexander Jr.)
- Address: 125 W Swannanoa Av, Liberty NC 27298
- Email: leprograms@protonmail.com
- Phone: 743-337-9506
- Website: https://alexanderai.site
- GitHub: https://github.com/Liberty-Emporium

## Deployment
- Primary: https://flood-claims.alexanderai.site (Railway)
- Database: SQLite on Railway volume (/data/floodclaim.db)
- AI: OpenRouter (OPENROUTER_API_KEY env var)
- Session: 30-day cookie, server-side

## Related Apps (Railway)
- FloodClaims Pro: billy-floods.up.railway.app
- Sweet Spot Cakes: sweet-spot-cakes.up.railway.app
- Pet Vet AI: ai-vet-tech.alexanderai.site
- AI Agent Widget: ai-agent-widget-production.up.railway.app
- EcDash: jay-portfolio-production.up.railway.app (alexanderai.site)
- Liberty Oil: liberty-oil-propane.up.railway.app
- KYS: ai-api-tracker-production.up.railway.app
- Agents: agents.alexanderai.site
- LE Thrift: liberty-emporium-thrift.alexanderai.site
- Gym Forge: gymforge.ai.alexanderai.site
- Liberty Oil (main): libertyoilandpropane.com (NOT on Railway, Jay manages manually)

## Integrations
- Stripe: payments (Basic $49, Pro $149, Agency $249/mo)
- SendGrid: email delivery
- Twilio: SMS notifications
- FEMA NFHL API: flood zone lookup
- Census Geocoding: address geocoding
- Xactimate: export format support

## Agent System
- Willie Agent ID: F5J8yYT6a6GrppjviN6p8w
- Multi-agent: OWL (Kali) + Bull (KiloClaw)
- Chat model: locked to openrouter/OWL Alpha
- Vision model: configurable in Settings → Vision Model

## Water Classification
- Category 1: Clean Water (sanitary — broken supply line, sink/tub overflow)
- Category 2: Gray Water (significant contamination — sump backup, washing machine overflow)
- Category 3: Black Water (grossly contaminated — sewage, floodwater, river water)
- Class 1: Affects only part of room, minimal absorption
- Class 2: Affects entire room, carpet and padding, wicking up walls 24-48"
- Class 3: Fastest evaporation rate, ceilings and walls saturated
- Class 4: Specialty drying — hardwood, concrete, plaster

## NFIP Policy Limits
- Residential: Building $250,000, Contents $100,000
- Commercial: Building $500,000, Contents $500,000
- Deductibles: $1,000-$10,000 depending on zone and elevation
- Proof of Loss: Required within 60 days of loss date (unless extended by FEMA)

## Standard Line Items (Xactimate-style)
- Demo/Remove (per room, per sqft)
- Drywall removal & reinstall (sqft)
- Insulation removal & reinstall (sqft)
- Interior painting (sqft wall area)
- Flooring removal & install (sqft — hardwood, tile, carpet)
- Baseboard removal & reinstall (linear ft)
- Electrical outlet/switch replacement (per unit)
- HVAC duct cleaning (per room)
- Dehumidification (per day)
- Air movers (per day, per unit)
- Content manipulation (per room)
- Anti-microbial treatment (sqft)
- Ozone treatment (per day)

## Database Tables
users, claims, rooms, line_items, photos, willie_conversations, willie_messages, settings, client_portal_tokens, signatures, stripe_customers, estimate_jobs, inspection_slots, notifications_log, activity_log, adjuster_applications, contractor_applications

## Roles
- Admin: full access, settings, team, recruit, analytics, billing
- Adjuster: assigned claims only, create/edit own claims, view own inspections

## Routes
/ (dashboard), /new_claim, /claims/<id>, /pipeline, /schedule, /notifications, /analytics, /billing, /admin/settings, /admin/team, /admin/recruit, /willie, /portal/<token>, /login, /logout, /health

## Contractor Recruitment Pipeline
1. Apply (contractor application form)
2. Review (admin reviews application)
3. Training (5 certification courses)
4. Certification Test (pass/fail)
5. Activate (approved for job assignments)

## Adjuster Recruitment
- Instant: enter NC license # → verify → auto-approve
- Email notification sent to adjuster
- First login requires password setup"""

    elif key == 'brain_photo_prompt':
        return """You are an expert flood damage assessor analyzing a photo for an insurance claim. Examine this photo with extreme precision and report ALL findings.

Structure your analysis as follows:

## ROOM & CONTEXT
- Identify room type if visible
- Ceiling height estimate
- Approximate room dimensions if determinable

## WATER EVIDENCE
- Water line height (inches from floor)
- Water staining (location, extent, color)
- Sediment or debris lines
- Active moisture visibility

## DAMAGE ASSESSMENT (item by item)

### Ceiling
- Material, condition, damage level (none/minor/moderate/severe)
- Staining, sagging, peeling, holes

### Walls
- Material (drywall, plaster, wood paneling)
- Damage: wicking height, staining, peeling paint, bubbling
- Affected linear feet and height from floor

### Flooring
- Material (hardwood, tile, carpet, vinyl, laminate, concrete)
- Damage type (buckling, warping, delamination, staining, saturation)
- Affected area in square feet
- Padding condition

### Baseboards & Trim
- Affected linear feet
- Material and condition

### Doors & Windows
- Frame damage, warping
- Hardware condition

### Kitchen
- Cabinet damage (base and upper)
- Countertop condition
- Appliance damage (dishwasher, fridge, range, microwave)

### Bathroom
- Vanity, toilet, tub/shower damage
- Tile/grout condition

### Contents & Furniture
- Any visible furniture/contents
- Damage level and material type

### HVAC/Mechanical
- Visible ductwork, vents, HVAC equipment damage

### Electrical
- Outlet/switch plate water lines
- Panel damage if visible

## MOLD ASSESSMENT
- Present: Y/N
- If present: location, approximate coverage area, color, growth stage

## STRUCTURAL CONCERNS
- Warped framing, buckled walls, sagging ceiling
- Any visible foundation or structural damage

## WATER CATEGORY ASSESSMENT
- Category 1 (Clean), 2 (Gray), or 3 (Black)
- Reasoning for classification

## WATER CLASS ASSESSMENT
- Class 1 through 4 with reasoning

## REPAIR RECOMMENDATIONS
List specific restoration actions needed:
- Demo/removal items
- Drying requirements
- Replacement items
- Specialty treatments (anti-microbial, ozone)

## SUMMARY
Total affected square feet, estimated severity, priority items.

Be thorough. If something is NOT damaged, say so. If you can't see it, say "not visible." Never fabricate details."""

    elif key == 'brain_system_prompt':
        return """You are Aquila, the AI assistant for FloodClaims Pro — a flood insurance claims management platform built by Liberty Emporium.

## Your Role
You help homeowners, insurance adjusters, and contractors with flood damage assessment, claims processing, and insurance guidance. You are knowledgeable, empathetic, and action-oriented.

## How to Analyze Damage Photos
When a user uploads a flood damage photo:
1. Identify the type of damage (water staining, structural crack, mold, debris, etc.)
2. Rate severity: Minor / Moderate / Major / Severe
3. List affected materials (drywall, flooring, insulation, electrical, HVAC, foundation)
4. Estimate the remediation urgency: Immediate / Within 48 hours / Can wait
5. Provide 2-3 recommended next steps
6. Format with clear headings and bullet points

## Response Guidelines
- Be specific and actionable — tell users exactly what to do next
- When you see damage in a photo, ALWAYS mention: "I recommend having a licensed adjuster verify this in person"
- Use plain language — avoid insurance jargon unless asked
- When uncertain about dollar amounts, give ranges and recommend professional estimates
- For FEMA/NFIP questions, reference the specific policy section when possible
- Always end responses with a clear next step or question to advance the conversation

## Tone
Professional, calm, empathetic. People filing flood claims are often overwhelmed. Be the steady hand that guides them through the process.

## Critical Rules
- Never fabricate policy details — if unsure, say so and direct to FEMA or their agent
- Never guarantee claim approval or specific payout amounts
- Always recommend professional inspection for structural damage or mold
- Do not provide legal advice — direct to licensed attorneys for legal questions"""

    return ''




@bp.route('/willie')
@login_required
def willie():
    db    = get_db()
    convs = db.execute(
        'SELECT * FROM willie_conversations WHERE user_id=? ORDER BY updated DESC LIMIT 100',
        (session['user_id'],)).fetchall()
    return render_template('willie.html', conversations=convs)



@bp.route('/willie/conversations', methods=['POST'])
@login_required
def willie_new_conversation():
    db  = get_db()
    cur = db.execute('INSERT INTO willie_conversations (user_id) VALUES (?)', (session['user_id'],))
    db.commit()
    return jsonify({'id': cur.lastrowid, 'title': 'New Conversation'})



@bp.route('/willie/conversations/<int:conv_id>')
@login_required
def willie_get_conversation(conv_id):
    db   = get_db()
    conv = db.execute('SELECT * FROM willie_conversations WHERE id=? AND user_id=?',
                      (conv_id, session['user_id'])).fetchone()
    if not conv:
        return jsonify({'error': 'not found'}), 404
    msgs = db.execute('SELECT role,content,created FROM willie_messages WHERE conversation_id=? ORDER BY id',
                      (conv_id,)).fetchall()
    return jsonify({'conversation': dict(conv), 'messages': [dict(m) for m in msgs]})



@bp.route('/willie/conversations/<int:conv_id>', methods=['DELETE'])
@login_required
def willie_delete_conversation(conv_id):
    db = get_db()
    db.execute('DELETE FROM willie_messages WHERE conversation_id=?', (conv_id,))
    db.execute('DELETE FROM willie_conversations WHERE id=? AND user_id=?', (conv_id, session['user_id']))
    db.commit()
    return jsonify({'ok': True})



@bp.route('/willie/conversations/<int:conv_id>/messages', methods=['POST'])
@login_required
def willie_save_message(conv_id):
    db      = get_db()
    conv    = db.execute('SELECT * FROM willie_conversations WHERE id=? AND user_id=?',
                         (conv_id, session['user_id'])).fetchone()
    if not conv:
        return jsonify({'error': 'not found'}), 404
    data    = request.get_json(silent=True) or {}
    role    = data.get('role', 'user')
    content = data.get('content', '').strip()
    if not content:
        return jsonify({'error': 'content required'}), 400
    db.execute('INSERT INTO willie_messages (conversation_id, role, content) VALUES (?,?,?)',
               (conv_id, role, content))
    # Auto-title from first user message
    if role == 'user' and conv['title'] == 'New Conversation':
        title = content[:60] + ('...' if len(content) > 60 else '')
        db.execute('UPDATE willie_conversations SET title=?, updated=CURRENT_TIMESTAMP WHERE id=?',
                   (title, conv_id))
    else:
        db.execute('UPDATE willie_conversations SET updated=CURRENT_TIMESTAMP WHERE id=?', (conv_id,))
    db.commit()
    return jsonify({'ok': True})



@bp.route('/willie/chat', methods=['POST'])
@login_required
def willie_chat():
    """Smart Aquila chat proxy — injects live app context into every message."""
    data       = request.get_json(silent=True) or {}
    message    = data.get('message', '').strip()
    history    = data.get('history', [])
    conv_id    = data.get('conversation_id')
    session_id = data.get('session_id', '')
    claim_id   = data.get('claim_id')       # passed when chatting from a claim page
    context_hint = data.get('context', '') # page context hint
    if not message:
        return jsonify({'error': 'message required'}), 400

    FLOOD_BASE = request.host_url.rstrip('/')

    # ── Build rich live context ─────────────────────────────────────────
    db = get_db()
    now_str = datetime.datetime.now().strftime('%A %B %d, %Y %I:%M %p')

    # Dashboard stats
    if session['role'] == 'admin':
        all_claims = db.execute('SELECT id, claim_number, client_name, property_address, status, total_estimate, flood_date, adjuster_id FROM claims ORDER BY created_at DESC').fetchall()
    else:
        all_claims = db.execute('SELECT id, claim_number, client_name, property_address, status, total_estimate, flood_date, adjuster_id FROM claims WHERE adjuster_id=? ORDER BY created_at DESC', (session['user_id'],)).fetchall()

    stats = {
        'total': len(all_claims),
        'new': sum(1 for c in all_claims if c['status'] == 'New'),
        'in_progress': sum(1 for c in all_claims if c['status'] == 'In Progress'),
        'submitted': sum(1 for c in all_claims if c['status'] == 'Submitted'),
        'closed': sum(1 for c in all_claims if c['status'] == 'Closed'),
        'pipeline': sum(c['total_estimate'] for c in all_claims if c['status'] != 'Closed'),
    }

    # Recent claims summary (last 10)
    recent_summary = '\n'.join(
        f'  - [{c["id"]}] {c["claim_number"]} | {c["client_name"]} | {c["property_address"][:40]} | {c["status"]} | ${c["total_estimate"]:,.0f}'
        for c in all_claims[:10]
    ) or '  (no claims yet)'

    # Current claim context (if on a claim page)
    claim_context = ''
    if claim_id:
        claim = db.execute('''
            SELECT c.*, u.name as adjuster_name
            FROM claims c LEFT JOIN users u ON c.adjuster_id=u.id
            WHERE c.id=?
        ''', (claim_id,)).fetchone()
        if claim:
            rooms = db.execute('SELECT * FROM rooms WHERE claim_id=? AND deleted_at IS NULL ORDER BY id', (claim_id,)).fetchall()
            room_lines = []
            for room in rooms:
                items = db.execute('SELECT * FROM line_items WHERE room_id=? AND deleted_at IS NULL ORDER BY id', (room['id'],)).fetchall()
                item_text = ', '.join(f'{i["description"]} ({i["quantity"]} {i["unit"]} @ ${i["unit_cost"]})' for i in items) or 'no items yet'
                room_lines.append(f'    Room [{room["id"]}] {room["name"]} (${room["subtotal"]:,.2f}): {item_text}')
            photos = db.execute('SELECT COUNT(*) as c FROM photos WHERE claim_id=? AND deleted_at IS NULL', (claim_id,)).fetchone()

            _nl = chr(10)
            rooms_text = _nl.join(room_lines) if room_lines else '    (no rooms added yet)'
            claim_context = (
                _nl + '┌─ CURRENT CLAIM (you are viewing this right now) ─────────────────────────' + _nl
                + f'| Claim ID: {claim["id"]} | Claim #: {claim["claim_number"]}' + _nl
                + f'| Client: {claim["client_name"]} | Phone: {claim["client_phone"] or "N/A"}' + _nl
                + f'| Property: {claim["property_address"]}' + _nl
                + f'| Status: {claim["status"]} | Priority: {claim["priority"] or "Normal"}' + _nl
                + f'| Flood Date: {claim["flood_date"]} | Source: {claim["flood_source"] or "N/A"}' + _nl
                + f'| Water: Category {claim["water_category"] or "?"} / Class {claim["water_class"] or "?"} / {claim["water_depth_in"] or "?"} inches' + _nl
                + f'| Insurance: {claim["insurance_company"] or "N/A"} | Policy: {claim["policy_number"] or "N/A"}' + _nl
                + f'| Coverage: Bldg ${claim["coverage_building"]:,.0f} | Contents ${claim["coverage_contents"]:,.0f} | Deductible ${claim["deductible"]:,.0f}' + _nl
                + f'| FEMA Zone: {claim["flood_zone"] or "NOT LOOKED UP"} | Map: {claim["fema_map_number"] or "N/A"}' + _nl
                + f'| Total Estimate: ${claim["total_estimate"]:,.2f} | Photos: {photos["c"]}' + _nl
                + f'| Adjuster: {claim["adjuster_name"] or "Unassigned"}' + _nl
                + f'| Rooms ({len(rooms)}):' + _nl
                + rooms_text + _nl
                + '└' + '─' * 65
            )

    # Today's inspections
    today_str = datetime.date.today().isoformat()
    todays_slots = db.execute('''
        SELECT s.*, c.claim_number, c.client_name, c.property_address
        FROM inspection_slots s JOIN claims c ON s.claim_id=c.id
        WHERE s.slot_date=? AND s.status != 'cancelled'
        ORDER BY s.slot_time
    ''', (today_str,)).fetchall()
    todays_insp = '\n'.join(
        f'  {s["slot_time"]} — {s["claim_number"]} ({s["client_name"]}) at {s["property_address"][:50]}'
        for s in todays_slots
    ) or '  None scheduled today'

    # Build the enriched system context injected as first message
    system_context = f"""You are Aquila, an expert AI flood damage adjuster assistant embedded inside FloodClaims Pro.
You have FULL CONTROL of the app and can take actions directly on behalf of the adjuster.
Always be helpful, concise, and professional. Use your knowledge of NFIP rules and flood claim procedures.

CURRENT USER: {session.get('name') or session.get('email')} (role: {session.get('role', 'adjuster')})
CURRENT TIME: {now_str}
PAGE CONTEXT: {context_hint or 'Dashboard'}

APP SNAPSHOT:
- Total claims: {stats['total']} | New: {stats['new']} | In Progress: {stats['in_progress']} | Submitted: {stats['submitted']} | Closed: {stats['closed']}
- Open pipeline value: ${stats['pipeline']:,.2f}
- Today's inspections: 
{todays_insp}

RECENT CLAIMS (use claim IDs below for API actions):
{recent_summary}
{claim_context}
YOUR CAPABILITIES (you can do all of these right now):
1. CREATE claims — just ask for client name, address, flood date
2. UPDATE any claim field — status, priority, water category, coverage, notes, etc.
3. ADD rooms and line items to any claim with exact pricing
4. LOOK UP claims by name, number, or address
5. RUN AI estimates on any claim
6. ANALYZE photos on any claim and suggest rooms/items
7. SCHEDULE inspections with date, time, adjuster
8. MOVE claims between pipeline stages
9. CHECK NFIP compliance score for any claim
10. LOOK UP FEMA flood zones by address
11. SEND client notifications
12. VIEW analytics and team data
13. ADD team members
14. ANSWER any question about NFIP rules, flood damage, water categories, pricing, procedures

NFIP EXPERTISE:
- Water Categories: Cat 1=clean, Cat 2=gray water, Cat 3=black/flood water (always Cat 3 for rising floodwater)
- Water Classes: 1=floors only, 2=walls up 24”, 3=ceiling wet, 4=hardwood/brick specialty drying
- Proof of Loss must be filed within 60 days of flood date or claim is denied
- 2026 national avg flood claim: $10,234-$11,605 | Full restoration: $5,000-$16,000
- Mitigation rates: Cat3 extraction $0.75-$1.50/sf, air movers $38-$55/day, dehumidifier $83-$110/day
- Drywall Cat3 tear-out: $1.79/sf | LVP/vinyl: $1.25-$2.00/sf | Hardwood: $5.82/sf
- Reconstruction: drywall $3.99-$5.50/sf | paint $1.50-$2.50/sf | baseboard R&R $5.51/lf
- FEMA flood zones: A/AE/AO/AH=high risk, B/X=moderate, C/X=minimal risk

When the user asks you to DO something (create a claim, add a room, update status, etc.):
1. Confirm what you are about to do
2. Use your API actions to execute it immediately
3. Report back what was done with specifics
4. Suggest the logical next step

When asked for advice, give specific, actionable NFIP-compliant guidance.
When referencing a claim, always use its claim number AND name so it's clear."""

    # Build payload with context prepended as a system message in history
    enriched_history = [
        {'role': 'system', 'content': system_context}
    ] + history[-8:]

    # ── Build brain-augmented system prompt from local DB ─────────────────
    brain_identity  = get_setting('brain_identity_md', '')
    brain_soul      = get_setting('brain_soul_md', '')
    brain_memory    = get_setting('brain_memory_md', '')
    brain_system    = get_setting('brain_system_prompt', '')

    # Default brain if nothing saved yet
    default_system = brain_system or """You are Aquila, an expert AI flood damage adjuster assistant embedded inside FloodClaims Pro.
You have FULL CONTROL of the app and can take actions directly on behalf of the adjuster.
Always be helpful, concise, and professional. Use your knowledge of NFIP rules and flood claim procedures."""

    # Prepend brain files to system context
    brain_prefix = ''
    if brain_identity:
        brain_prefix += f"\n\n## Your Identity\n{brain_identity}"
    if brain_soul:
        brain_prefix += f"\n\n## Your Soul\n{brain_soul}"
    if brain_memory:
        brain_prefix += f"\n\n## Your Memory\n{brain_memory}"

    final_system = default_system + brain_prefix

    # Replace the old system_context with the brain-augmented one
    # but keep all the live app context (claims, dashboard, etc.)
    live_context = system_context.replace(
        'You are Aquila, an expert AI flood damage adjuster assistant embedded inside FloodClaims Pro.\nYou have FULL CONTROL of the app and can take actions directly on behalf of the adjuster.\nAlways be helpful, concise, and professional. Use your knowledge of NFIP rules and flood claim procedures.',
        final_system
    )

    # Build messages for OpenRouter
    messages = [{'role': 'system', 'content': live_context}] + history[-8:]

    try:
        selected_model   = get_setting('ai_chat_model') or get_setting('ai_model', 'openrouter/owl-alpha')
        fallback_model   = get_setting('ai_fallback_model', 'anthropic/claude-sonnet-4-5')
        openrouter_key   = OPENROUTER_KEY
        if not openrouter_key:
            return jsonify({'reply': '⚠️ OpenRouter API key not configured. Go to Settings → AI Integration to set it up.'})

        reply = None
        models_to_try = [selected_model]
        if fallback_model and fallback_model != selected_model:
            models_to_try.append(fallback_model)

        for model in models_to_try:
            try:
                r = _req.post(
                    'https://openrouter.ai/api/v1/chat/completions',
                    headers={'Authorization': f'Bearer {openrouter_key}', 'Content-Type': 'application/json'},
                    json={'model': model, 'messages': messages, 'max_tokens': 2000},
                    timeout=45
                )
                d = r.json()
                if 'error' in d:
                    err = d['error']
                    if isinstance(err, dict):
                        err = err.get('message', str(err))
                    if model == models_to_try[-1]:
                        reply = f'⚠️ AI error: {err}'
                    continue
                reply = d['choices'][0]['message']['content']
                if model != selected_model:
                    reply = f'[Used fallback: {model}]\n\n{reply}'
                break
            except Exception as inner_e:
                if model == models_to_try[-1]:
                    reply = f'Aquila is unavailable right now. ({str(inner_e)[:80]})'

        reply = reply or 'Aquila is unavailable right now.'
    except Exception as e:
        reply = f'Aquila is unavailable right now. ({str(e)[:80]})'

    # Save to conversation history
    if conv_id:
        conv = db.execute('SELECT * FROM willie_conversations WHERE id=? AND user_id=?',
                          (conv_id, session['user_id'])).fetchone()
        if conv:
            db.execute('INSERT INTO willie_messages (conversation_id,role,content) VALUES (?,?,?)',
                       (conv_id, 'user', message))
            db.execute('INSERT INTO willie_messages (conversation_id,role,content) VALUES (?,?,?)',
                       (conv_id, 'assistant', reply))
            if conv['title'] == 'New Conversation':
                title = message[:60] + ('...' if len(message) > 60 else '')
                db.execute('UPDATE willie_conversations SET title=?,updated=CURRENT_TIMESTAMP WHERE id=?',
                           (title, conv_id))
            else:
                db.execute('UPDATE willie_conversations SET updated=CURRENT_TIMESTAMP WHERE id=?', (conv_id))
            db.commit()

    return jsonify({'reply': reply})

# ── Aquila External API ──────────────────────────────────────────────────────────────
# All routes accept: Authorization: Bearer <willie_token>
# Get token from: GET /willie/token (admin session required)

# ── Instant AI photo analysis (used by new claim form before submit) ────────────────



@bp.route('/api/analyze-photo', methods=['POST'])
@login_required
def api_analyze_photo():
    data     = request.get_json(silent=True) or {}
    img_b64  = data.get('image', '')
    mime     = data.get('mime', 'image/jpeg')
    if not img_b64:
        return jsonify({'error': 'no image'}), 400
    key = get_setting('openrouter_api_key') or OPENROUTER_KEY
    if not key:
        return jsonify({'description': ''})
    try:
        selected_model = get_setting('ai_vision_model') or get_setting('ai_model', 'openrouter/auto')
        # Ensure vision-capable
        _text_only = {'openrouter/owl-alpha', 'openrouter/owl', 'openai/o3-mini', 'deepseek/deepseek-r1'}
        if selected_model in _text_only:
            selected_model = 'openrouter/auto'
        r = _req.post(
            'https://openrouter.ai/api/v1/chat/completions',
            headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
            json={
                'model': selected_model,
                'messages': [{'role': 'user', 'content': [
                    {'type': 'text', 'text': (
                        'You are a professional flood damage adjuster. Analyze this photo and provide '
                        'a concise 2-3 sentence assessment covering: (1) what is damaged, '
                        '(2) severity and water category if visible, '
                        '(3) immediate repair needs. Be specific and professional.'
                    )},
                    {'type': 'image_url', 'image_url': {'url': f'data:{mime};base64,{img_b64}'}}
                ]}],
                'max_tokens': 200
            }, timeout=30)
        desc = r.json()['choices'][0]['message']['content']
        return jsonify({'description': desc})
    except Exception:
        return jsonify({'description': ''})



@bp.route('/willie/token')
@login_required
def willie_token():
    """Show the Willie API token to admin users."""
    if session.get('role') != 'admin':
        return jsonify({'error': 'admin required'}), 403
    return jsonify({'token': get_willie_token(),
                    'base_url': request.host_url.rstrip('/'),
                    'note': 'Use this as Authorization: Bearer <token> in Willie actions'})



@bp.route('/willie/api/claims', methods=['GET'])
def willie_list_claims():
    if not willie_auth():
        return jsonify({'error': 'unauthorized'}), 401
    db = get_db()
    claims = db.execute('''
        SELECT c.id, c.claim_number, c.client_name, c.property_address,
               c.flood_date, c.status, c.total_estimate,
               u.name as adjuster_name
        FROM claims c LEFT JOIN users u ON c.adjuster_id=u.id
        ORDER BY c.created_at DESC LIMIT 50
    ''').fetchall()
    return jsonify({'ok': True, 'claims': [dict(c) for c in claims], 'count': len(claims)})



@bp.route('/willie/api/claims/lookup', methods=['GET'])
def willie_lookup_claim():
    """Look up a claim by claim_number (e.g. FC-202604-FBA7C7) or partial client name."""
    if not willie_auth():
        return jsonify({'error': 'unauthorized'}), 401
    claim_number = request.args.get('claim_number', '').strip()
    client_name  = request.args.get('client_name', '').strip()
    db = get_db()
    if claim_number:
        claim = db.execute(
            'SELECT c.*, u.name as adjuster_name FROM claims c LEFT JOIN users u ON c.adjuster_id=u.id WHERE c.claim_number=?',
            (claim_number,)).fetchone()
        if not claim:
            return jsonify({'ok': False, 'error': f'No claim found with number {claim_number}'}), 404
        rooms = db.execute('SELECT * FROM rooms WHERE claim_id=? AND deleted_at IS NULL ORDER BY id', (claim['id'],)).fetchall()
        room_data = []
        for r in rooms:
            items = db.execute('SELECT * FROM line_items WHERE room_id=? AND deleted_at IS NULL ORDER BY id', (r['id'],)).fetchall()
            room_data.append({'room': dict(r), 'line_items': [dict(i) for i in items]})
        return jsonify({'ok': True, 'claim': dict(claim), 'rooms': room_data})
    elif client_name:
        claims = db.execute(
            'SELECT id, claim_number, client_name, status, total_estimate FROM claims WHERE client_name LIKE ? ORDER BY created_at DESC',
            (f'%{client_name}%',)).fetchall()
        return jsonify({'ok': True, 'claims': [dict(c) for c in claims], 'count': len(claims)})
    return jsonify({'error': 'Provide claim_number or client_name as query param'}), 400




@bp.route('/willie/api/claims/<int:claim_id>/estimate', methods=['POST'])
def willie_generate_estimate(claim_id):
    """Use AI (with photo vision) to generate a full adjuster-style estimate."""
    if not willie_auth():
        return jsonify({'error': 'unauthorized'}), 401
    db = get_db()
    claim = db.execute('SELECT * FROM claims WHERE id=?', (claim_id,)).fetchone()
    if not claim:
        return jsonify({'error': 'Claim not found'}), 404
    claim = dict(claim)  # convert sqlite3.Row → dict so .get() works

    key = get_setting('openrouter_api_key') or OPENROUTER_KEY
    model = get_setting('ai_chat_model') or get_setting('ai_model', 'openrouter/owl-alpha')
    if not key:
        return jsonify({'error': 'OpenRouter API key not configured. Add it in Settings.'}), 400

    # Gather rooms + items
    rooms = db.execute('SELECT * FROM rooms WHERE claim_id=? AND deleted_at IS NULL ORDER BY id', (claim_id,)).fetchall()
    room_summary = []
    for r in rooms:
        items = db.execute('SELECT * FROM line_items WHERE room_id=? AND deleted_at IS NULL ORDER BY id', (r['id'],)).fetchall()
        item_list = '; '.join([f"{i['description']} x{i['quantity']} {i['unit']} @${i['unit_cost']:.2f}" for i in items]) or 'No line items yet'
        room_summary.append(f"  Room: {r['name']}\n  Items: {item_list}")

    # Analyze all photos with vision AI
    photos = [dict(p) for p in db.execute('SELECT * FROM photos WHERE claim_id=? AND deleted_at IS NULL ORDER BY id', (claim_id,)).fetchall()]
    photo_analyses = []
    for photo in photos[:8]:  # limit to 8 photos to avoid token overflow
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
            photo_analyses.append(f"Photo ({photo['caption'] or photo['filename']}): {desc}")

    photo_section = '\n'.join(photo_analyses) if photo_analyses else 'No photos uploaded yet.'
    room_section  = '\n'.join(room_summary) if room_summary else 'No rooms documented yet.'

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
Analyze this flood damage claim and produce a complete professional estimate like you would submit to an insurance company.

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

Current Total: ${claim['total_estimate']:.2f}

=== PHOTO ANALYSIS ===
{photo_section}

=== YOUR TASK ===
Based on the photos, claim details, and current line items, provide:

1. **PHOTO FINDINGS** — What damage did you observe in each photo? Be specific (water staining, mold, structural damage, flooring damage, etc.). Note water category/class implied by the damage.

2. **COMPLETE LINE-ITEM ESTIMATE** — Using the pricing reference above, list EVERY repair needed — both mitigation phase and reconstruction phase:
   - Description
   - Quantity + Unit (sq ft, ln ft, ea, hr)
   - Unit Cost (from the pricing reference above)
   - Line Total
   Mark items already documented with ✅, missing items with ➕
   Do NOT omit drying equipment, antimicrobial treatment, or debris removal.

3. **ESTIMATE SUMMARY**
   - Subtotal by room
   - Contractor O&P (20%)
   - Sales tax on materials (~8%)
   - 10% contingency
   - GRAND TOTAL (recommended claim amount)

4. **ADJUSTER NOTES** — Red flags, documentation gaps, items insurance will scrutinize, and whether the current estimate of ${claim['total_estimate']:.2f} is adequate.

Be thorough — this goes directly to the insurance company. Low estimates hurt the homeowner."""

    estimate = call_openrouter([{'role': 'user', 'content': prompt}], model, key)
    return jsonify({
        'ok': True,
        'claim_number': claim['claim_number'],
        'client': claim['client_name'],
        'property': claim['property_address'],
        'current_total': claim['total_estimate'],
        'photos_analyzed': len(photo_analyses),
        'estimate': estimate
    })




@bp.route('/willie/api/claims', methods=['POST'])
def willie_create_claim():
    if not willie_auth():
        return jsonify({'error': 'unauthorized'}), 401
    data = request.get_json(silent=True) or {}

    # Fill in demo data for any missing fields
    client_name      = data.get('client_name', 'Demo Client').strip() or 'Demo Client'
    client_phone     = data.get('client_phone', '(555) 000-0000')
    client_email     = data.get('client_email', '')
    property_address = data.get('property_address', '123 Flood St, Liberty, NC 27298').strip() or '123 Flood St, Liberty, NC 27298'
    flood_date       = data.get('flood_date', datetime.datetime.now().strftime('%Y-%m-%d'))
    insurance_company= data.get('insurance_company', '')
    policy_number    = data.get('policy_number', '')
    notes            = data.get('notes', '')

    db = get_db()
    # Get first admin user as default adjuster
    adjuster = db.execute("SELECT id FROM users WHERE role='admin' ORDER BY id LIMIT 1").fetchone()
    adjuster_id = adjuster['id'] if adjuster else 1

    claim_num = gen_claim_number()
    db.execute('''INSERT INTO claims
        (claim_number, adjuster_id, client_name, client_phone, client_email,
         property_address, flood_date, insurance_company, policy_number, notes)
        VALUES (?,?,?,?,?,?,?,?,?,?)''',
        (claim_num, adjuster_id, client_name, client_phone, client_email,
         property_address, flood_date, insurance_company, policy_number, notes))
    db.commit()
    claim = db.execute('SELECT * FROM claims WHERE claim_number=?', (claim_num,)).fetchone()
    return jsonify({
        'ok': True,
        'claim_id': claim['id'],
        'claim_number': claim_num,
        'message': f'Claim {claim_num} created for {client_name} at {property_address}',
        'url': f'https://flood-claims.alexanderai.site/claims/{claim["id"]}'
    }), 201



@bp.route('/willie/api/claims/<int:claim_id>', methods=['GET'])
def willie_get_claim(claim_id):
    if not willie_auth():
        return jsonify({'error': 'unauthorized'}), 401
    db = get_db()
    claim = db.execute('''
        SELECT c.*, u.name as adjuster_name FROM claims c
        LEFT JOIN users u ON c.adjuster_id=u.id WHERE c.id=?
    ''', (claim_id,)).fetchone()
    if not claim:
        return jsonify({'error': 'Claim not found'}), 404
    rooms = db.execute('SELECT * FROM rooms WHERE claim_id=? AND deleted_at IS NULL ORDER BY id', (claim_id,)).fetchall()
    room_data = []
    for r in rooms:
        items = db.execute('SELECT * FROM line_items WHERE room_id=? AND deleted_at IS NULL ORDER BY id', (r['id'],)).fetchall()
        room_data.append({'room': dict(r), 'line_items': [dict(i) for i in items]})
    return jsonify({'ok': True, 'claim': dict(claim), 'rooms': room_data})



@bp.route('/willie/api/claims/by-number/<claim_number>', methods=['DELETE'])
def willie_delete_claim_by_number(claim_number):
    """Delete a claim by claim number (e.g. FC-202604-AF52D2) in one step."""
    if not willie_auth():
        return jsonify({'error': 'unauthorized'}), 401
    db = get_db()
    claim = db.execute('SELECT id, client_name, claim_number FROM claims WHERE claim_number=?', (claim_number,)).fetchone()
    if not claim:
        return jsonify({'ok': False, 'error': f'No claim found with number {claim_number}'}), 404
    db.execute('DELETE FROM claims WHERE id=?', (claim['id'],))
    db.commit()
    # Verify it's gone
    check = db.execute('SELECT id FROM claims WHERE id=?', (claim['id'],)).fetchone()
    if check:
        return jsonify({'ok': False, 'error': 'Delete failed — claim still exists'}), 500
    remaining = db.execute('SELECT COUNT(*) as c FROM claims').fetchone()['c']
    return jsonify({'ok': True, 'message': f'Claim {claim_number} ({claim["client_name"]}) permanently deleted. {remaining} claims remaining.', 'deleted_id': claim['id'], 'deleted_client': claim['client_name'], 'remaining_claims': remaining})




@bp.route('/willie/api/claims/<int:claim_id>', methods=['DELETE'])
def willie_delete_claim(claim_id):
    if not willie_auth():
        return jsonify({'error': 'unauthorized'}), 401
    db = get_db()
    claim = db.execute('SELECT id, client_name FROM claims WHERE id=?', (claim_id,)).fetchone()
    if not claim:
        return jsonify({'error': 'Claim not found'}), 404
    # CASCADE deletes rooms, line_items, photos automatically
    db.execute('DELETE FROM claims WHERE id=?', (claim_id,))
    db.commit()
    return jsonify({'ok': True, 'message': f'Claim {claim_id} ({claim["client_name"]}) and all records deleted.'})




@bp.route('/willie/api/claims/<int:claim_id>/status', methods=['POST'])
def willie_update_status(claim_id):
    if not willie_auth():
        return jsonify({'error': 'unauthorized'}), 401
    data   = request.get_json(silent=True) or {}
    status = data.get('status', '').strip()
    valid  = ['New', 'In Progress', 'Submitted', 'Closed']
    if status not in valid:
        return jsonify({'error': f'status must be one of: {valid}'}), 400
    db = get_db()
    claim = db.execute('SELECT * FROM claims WHERE id=?', (claim_id,)).fetchone()
    db.execute('UPDATE claims SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?', (status, claim_id))
    db.commit()
    if claim:
        notify_client_status_change(claim, status)
    return jsonify({'ok': True, 'claim_id': claim_id, 'status': status,
                    'message': f'Claim {claim_id} status updated to {status}'})



@bp.route('/willie/api/claims/<int:claim_id>/rooms', methods=['POST'])
def willie_add_room(claim_id):
    if not willie_auth():
        return jsonify({'error': 'unauthorized'}), 401
    data = request.get_json(silent=True) or {}
    name = data.get('room_name', data.get('name', '')).strip()
    if not name:
        return jsonify({'error': 'room_name required'}), 400
    db    = get_db()
    claim = db.execute('SELECT id FROM claims WHERE id=?', (claim_id,)).fetchone()
    if not claim:
        return jsonify({'error': 'Claim not found'}), 404
    cur = db.execute('INSERT INTO rooms (claim_id, name) VALUES (?,?)', (claim_id, name))
    db.commit()
    return jsonify({'ok': True, 'room_id': cur.lastrowid, 'room_name': name,
                    'message': f'Room "{name}" added to claim {claim_id}'})



@bp.route('/willie/api/claims/<int:claim_id>/rooms/<int:room_id>/items', methods=['POST'])
def willie_add_item(claim_id, room_id):
    if not willie_auth():
        return jsonify({'error': 'unauthorized'}), 401
    data      = request.get_json(silent=True) or {}
    desc      = data.get('description', '').strip()
    qty       = float(data.get('quantity', 1) or 1)
    unit      = data.get('unit', 'ea')
    unit_cost = float(data.get('unit_cost', 0) or 0)
    total     = qty * unit_cost
    if not desc:
        return jsonify({'error': 'description required'}), 400
    db = get_db()
    db.execute('INSERT INTO line_items (room_id,description,quantity,unit,unit_cost,total) VALUES (?,?,?,?,?,?)',
               (room_id, desc, qty, unit, unit_cost, total))
    db.commit()
    recalc_claim(claim_id, get_db)
    return jsonify({'ok': True, 'description': desc, 'total': total,
                    'message': f'Added "{desc}" — {qty} {unit} @ ${unit_cost} = ${total:.2f}'})



@bp.route('/willie/api/team', methods=['GET'])
def willie_list_team():
    if not willie_auth():
        return jsonify({'error': 'unauthorized'}), 401
    db    = get_db()
    users = db.execute(
        'SELECT id, name, email, role, created_at, '
        '(SELECT COUNT(*) FROM claims WHERE adjuster_id=users.id) as claim_count '
        'FROM users ORDER BY name'
    ).fetchall()
    return jsonify({'ok': True, 'team': [dict(u) for u in users], 'count': len(users)})



@bp.route('/willie/api/team', methods=['POST'])
def willie_add_team_member():
    if not willie_auth():
        return jsonify({'error': 'unauthorized'}), 401
    data  = request.get_json(silent=True) or {}
    name  = data.get('name', '').strip()
    email = data.get('email', '').strip().lower()
    pw    = data.get('password', '').strip()
    role  = data.get('role', 'adjuster').strip().lower()
    if role not in ('admin', 'adjuster'):
        role = 'adjuster'
    if not name:
        return jsonify({'error': 'name is required'}), 400
    if not email:
        return jsonify({'error': 'email is required'}), 400
    if not pw:
        pw = secrets.token_urlsafe(10)
    ok, err = _validate_password(pw)
    if not ok:
        return jsonify({'error': err}), 400
    db = get_db()
    try:
        db.execute('INSERT INTO users (name, email, password, role) VALUES (?,?,?,?)',
                   (name, email, hash_pw(pw), role))
        db.commit()
        user = db.execute('SELECT id, name, email, role FROM users WHERE email=?', (email,)).fetchone()
        return jsonify({
            'ok': True,
            'user_id':  user['id'],
            'name':     user['name'],
            'email':    user['email'],
            'role':     user['role'],
            'password': pw,
            'message':  f'Team member {name} added as {role}. Login: {email} / {pw}'
        }), 201
    except sqlite3.IntegrityError:
        return jsonify({'error': f'Email {email} already exists'}), 409



@bp.route('/willie/api/dashboard', methods=['GET'])
def willie_dashboard():
    if not willie_auth():
        return jsonify({'error': 'unauthorized'}), 401
    db = get_db()
    claims = db.execute('SELECT status, total_estimate FROM claims').fetchall()
    stats = {
        'total':       len(claims),
        'new':         sum(1 for c in claims if c['status'] == 'New'),
        'in_progress': sum(1 for c in claims if c['status'] == 'In Progress'),
        'submitted':   sum(1 for c in claims if c['status'] == 'Submitted'),
        'closed':      sum(1 for c in claims if c['status'] == 'Closed'),
        'pipeline_value': sum(c['total_estimate'] for c in claims if c['status'] != 'Closed'),
    }
    recent = db.execute('''
        SELECT c.id, c.claim_number, c.client_name, c.status, c.total_estimate
        FROM claims c ORDER BY c.created_at DESC LIMIT 5
    ''').fetchall()
    return jsonify({'ok': True, 'stats': stats, 'recent_claims': [dict(r) for r in recent]})



@bp.route('/willie/api/claims/<int:claim_id>/rooms', methods=['GET'])
def willie_list_rooms(claim_id):
    if not willie_auth(): return jsonify({'error': 'unauthorized'}), 401
    db = get_db()
    claim = db.execute('SELECT id FROM claims WHERE id=?', (claim_id,)).fetchone()
    if not claim: return jsonify({'error': 'Claim not found'}), 404
    rooms = db.execute('SELECT id, name, subtotal FROM rooms WHERE claim_id=? ORDER BY id', (claim_id,)).fetchall()
    rooms_out = []
    for r in rooms:
        items = db.execute('SELECT id, room_id, description, quantity, unit, unit_cost, total FROM line_items WHERE room_id=? ORDER BY id', (r['id'],)).fetchall()
        rooms_out.append({'id': r['id'], 'name': r['name'], 'subtotal': r['subtotal'], 'line_items': [dict(i) for i in items]})
    return jsonify({'ok': True, 'claim_id': claim_id, 'rooms': rooms_out, 'count': len(rooms_out)})




@bp.route('/willie/api/claims/<int:claim_id>/rooms/<int:room_id>', methods=['DELETE'])
def willie_delete_room(claim_id, room_id):
    if not willie_auth(): return jsonify({'error': 'unauthorized'}), 401
    db = get_db()
    room = db.execute('SELECT id, name FROM rooms WHERE id=? AND claim_id=?', (room_id, claim_id)).fetchone()
    if not room: return jsonify({'error': 'Room not found'}), 404
    db.execute('DELETE FROM rooms WHERE id=?', (room_id,))
    db.commit()
    return jsonify({'ok': True, 'message': f'Room "{room["name"]}" and all its line items deleted.'})




@bp.route('/willie/api/line-items/<int:item_id>', methods=['DELETE'])
def willie_delete_line_item(item_id):
    if not willie_auth(): return jsonify({'error': 'unauthorized'}), 401
    db = get_db()
    item = db.execute('SELECT id, description FROM line_items WHERE id=?', (item_id,)).fetchone()
    if not item: return jsonify({'error': 'Line item not found'}), 404
    db.execute('DELETE FROM line_items WHERE id=?', (item_id,))
    db.commit()
    return jsonify({'ok': True, 'message': f'Line item "{item["description"]}" deleted.'})




@bp.route('/willie/api/team/<int:user_id>', methods=['PUT', 'PATCH'])
def willie_edit_team_member(user_id):
    if not willie_auth():
        return jsonify({'error': 'unauthorized'}), 401
    data  = request.get_json(silent=True) or {}
    name  = data.get('name', '').strip()
    email = data.get('email', '').strip().lower()
    pw    = data.get('password', '').strip()
    role  = data.get('role', '').strip().lower()
    if not email:
        return jsonify({'error': 'email is required'}), 400
    if role and role not in ('admin', 'adjuster'):
        return jsonify({'error': 'role must be admin or adjuster'}), 400
    if pw:
        ok, err = _validate_password(pw)
        if not ok:
            return jsonify({'error': err}), 400
    db = get_db()
    user = db.execute('SELECT id FROM users WHERE id=?', (user_id,)).fetchone()
    if not user:
        return jsonify({'error': 'Team member not found'}), 404
    try:
        if pw and role:
            db.execute('UPDATE users SET name=?, email=?, password=?, role=? WHERE id=?',
                       (name, email, hash_pw(pw), role, user_id))
        elif pw:
            db.execute('UPDATE users SET name=?, email=?, password=? WHERE id=?',
                       (name, email, hash_pw(pw), user_id))
        elif role:
            db.execute('UPDATE users SET name=?, email=?, role=? WHERE id=?',
                       (name, email, role, user_id))
        else:
            db.execute('UPDATE users SET name=?, email=? WHERE id=?',
                       (name, email, user_id))
        db.commit()
        updated = db.execute('SELECT id, name, email, role FROM users WHERE id=?', (user_id,)).fetchone()
        return jsonify({'ok': True, 'user': dict(updated), 'message': f'Team member {name} updated.'})
    except sqlite3.IntegrityError:
        return jsonify({'error': f'Email {email} already exists'}), 409




@bp.route('/willie/api/team/<int:user_id>', methods=['DELETE'])
def willie_delete_team_member(user_id):
    if not willie_auth(): return jsonify({'error': 'unauthorized'}), 401
    db = get_db()
    user = db.execute('SELECT id, name FROM users WHERE id=?', (user_id,)).fetchone()
    if not user: return jsonify({'error': 'Team member not found'}), 404
    db.execute('DELETE FROM users WHERE id=?', (user_id,))
    db.commit()
    return jsonify({'ok': True, 'message': f'Team member "{user["name"]}" deleted.'})




@bp.route('/willie/api/claims/<int:claim_id>/report', methods=['GET'])
def willie_get_report(claim_id):
    if not willie_auth(): return jsonify({'error': 'unauthorized'}), 401
    db = get_db()
    claim = db.execute('SELECT * FROM claims WHERE id=?', (claim_id,)).fetchone()
    if not claim: return jsonify({'error': 'Claim not found'}), 404
    rooms = db.execute('SELECT * FROM rooms WHERE claim_id=? AND deleted_at IS NULL ORDER BY id', (claim_id,)).fetchall()
    report = dict(claim)
    report['rooms'] = []
    for r in rooms:
        items = db.execute('SELECT * FROM line_items WHERE room_id=? AND deleted_at IS NULL ORDER BY id', (r['id'],)).fetchall()
        room_data = dict(r)
        room_data['line_items'] = [dict(i) for i in items]
        report['rooms'].append(room_data)
    return jsonify({'ok': True, 'report': report})




@bp.route('/willie/api/settings', methods=['GET'])
def willie_get_settings():
    if not willie_auth(): return jsonify({'error': 'unauthorized'}), 401
    db = get_db()
    settings = db.execute('SELECT key, value FROM settings').fetchall()
    return jsonify({'ok': True, 'settings': {s['key']: s['value'] for s in settings}})




@bp.route('/willie/api/settings', methods=['POST'])
def willie_update_settings():
    if not willie_auth(): return jsonify({'error': 'unauthorized'}), 401
    data = request.get_json(silent=True) or {}
    db = get_db()
    # Accept both 'ai_model' and legacy 'openrouter_model' alias
    allowed = {'openrouter_api_key', 'ai_model', 'openrouter_model', 'willie_agent_key', 'willie_agent_id'}
    updated = []
    for key, value in data.items():
        if key in allowed:
            store_key = 'ai_model' if key == 'openrouter_model' else key
            db.execute('INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (store_key, value))
            updated.append(store_key)
    db.commit()
    return jsonify({'ok': True, 'updated': updated})


# ── Admin: Train Aquila (Brain Editor) — Local DB Storage ─────────────────────



@bp.route('/willie/api/actions/sync', methods=['POST'])
def willie_sync_actions():
    """Push all FloodClaims actions to Willie's widget so he can use them correctly.
    Requires willie_agent_key to be set in settings (Willie's own widget API key).
    Auth: Willie token OR admin session."""
    if not session.get('user_id') and not willie_auth():
        return jsonify({'error': 'unauthorized'}), 401

    WIDGET_BASE     = 'https://ai-agent-widget-production.up.railway.app'
    FLOOD_BASE      = 'https://flood-claims.alexanderai.site'
    WILLIE_AGENT_ID = get_setting('willie_agent_id', 'F5J8yYT6a6GrppjviN6p8w')
    willie_key      = get_setting('willie_agent_key', '')
    flood_token     = get_willie_token()

    if not willie_key:
        return jsonify({'ok': False,
                        'error': 'aquila_agent_key not set. Go to Settings and paste Aquila\'s widget API key.'}), 400

    # Full correct action definitions — {param} placeholders get substituted by the widget engine
    ACTIONS = [
        {
            'name':        'get_dashboard',
            'description': 'Get FloodClaims Pro dashboard stats: total claims, pipeline value, status breakdown, recent claims.',
            'method':      'GET',
            'url':         f'{FLOOD_BASE}/willie/api/dashboard',
            'headers':     {'Authorization': f'Bearer {flood_token}'},
            'body':        {},
        },
        {
            'name':        'list_claims',
            'description': 'List all flood damage claims. Use this to find a claim ID from a client name or claim number.',
            'method':      'GET',
            'url':         f'{FLOOD_BASE}/willie/api/claims',
            'headers':     {'Authorization': f'Bearer {flood_token}'},
            'body':        {},
        },
        {
            'name':        'lookup_claim',
            'description': 'Look up a specific claim by claim_number (e.g. FC-202604-XXXX) or partial client name. Always do this before adding rooms/items to find the correct claim ID.',
            'method':      'GET',
            'url':         f'{FLOOD_BASE}/willie/api/claims/lookup?claim_number={{claim_number}}',
            'headers':     {'Authorization': f'Bearer {flood_token}'},
            'body':        {},
        },
        {
            'name':        'get_claim',
            'description': 'Get full details of a claim including rooms and line items. Requires numeric claim_id.',
            'method':      'GET',
            'url':         f'{FLOOD_BASE}/willie/api/claims/{{claim_id}}',
            'headers':     {'Authorization': f'Bearer {flood_token}'},
            'body':        {},
        },
        {
            'name':        'create_claim',
            'description': 'Create a new flood damage claim. Requires client_name, property_address, flood_date.',
            'method':      'POST',
            'url':         f'{FLOOD_BASE}/willie/api/claims',
            'headers':     {'Authorization': f'Bearer {flood_token}'},
            'body':        {'client_name': '{client_name}', 'property_address': '{property_address}',
                            'flood_date': '{flood_date}', 'insurance_company': '{insurance_company}'},
        },
        {
            'name':        'update_claim_status',
            'description': 'Update the status of a claim. Status must be one of: New, In Progress, Submitted, Closed.',
            'method':      'POST',
            'url':         f'{FLOOD_BASE}/willie/api/claims/{{claim_id}}/status',
            'headers':     {'Authorization': f'Bearer {flood_token}'},
            'body':        {'status': '{status}'},
        },
        {
            'name':        'add_room',
            'description': 'Add a room to a claim. ALWAYS call lookup_claim first to get the numeric claim_id. Requires claim_id (number) and room_name.',
            'method':      'POST',
            'url':         f'{FLOOD_BASE}/willie/api/claims/{{claim_id}}/rooms',
            'headers':     {'Authorization': f'Bearer {flood_token}'},
            'body':        {'room_name': '{room_name}'},
        },
        {
            'name':        'list_rooms',
            'description': 'List all rooms and line items for a claim. Requires numeric claim_id.',
            'method':      'GET',
            'url':         f'{FLOOD_BASE}/willie/api/claims/{{claim_id}}/rooms',
            'headers':     {'Authorization': f'Bearer {flood_token}'},
            'body':        {},
        },
        {
            'name':        'add_line_item',
            'description': 'Add a line item (damage item) to a room. ALWAYS call list_rooms first to get the numeric room_id. Requires claim_id, room_id, description, quantity, unit, unit_cost.',
            'method':      'POST',
            'url':         f'{FLOOD_BASE}/willie/api/claims/{{claim_id}}/rooms/{{room_id}}/items',
            'headers':     {'Authorization': f'Bearer {flood_token}'},
            'body':        {'description': '{description}', 'quantity': '{quantity}',
                            'unit': '{unit}', 'unit_cost': '{unit_cost}'},
        },
        {
            'name':        'delete_room',
            'description': 'Delete a room and all its line items from a claim.',
            'method':      'DELETE',
            'url':         f'{FLOOD_BASE}/willie/api/claims/{{claim_id}}/rooms/{{room_id}}',
            'headers':     {'Authorization': f'Bearer {flood_token}'},
            'body':        {},
        },
        {
            'name':        'delete_line_item',
            'description': 'Delete a single line item by its numeric item_id.',
            'method':      'DELETE',
            'url':         f'{FLOOD_BASE}/willie/api/line-items/{{item_id}}',
            'headers':     {'Authorization': f'Bearer {flood_token}'},
            'body':        {},
        },
        {
            'name':        'get_report',
            'description': 'Get a full damage report for a claim including all rooms and line items.',
            'method':      'GET',
            'url':         f'{FLOOD_BASE}/willie/api/claims/{{claim_id}}/report',
            'headers':     {'Authorization': f'Bearer {flood_token}'},
            'body':        {},
        },
        {
            'name':        'generate_estimate',
            'description': 'Trigger AI estimate generation for a claim. Returns a job_id to poll for results.',
            'method':      'POST',
            'url':         f'{FLOOD_BASE}/willie/api/claims/{{claim_id}}/estimate',
            'headers':     {'Authorization': f'Bearer {flood_token}'},
            'body':        {},
        },
        {
            'name':        'list_team',
            'description': 'List all adjusters and team members.',
            'method':      'GET',
            'url':         f'{FLOOD_BASE}/willie/api/team',
            'headers':     {'Authorization': f'Bearer {flood_token}'},
            'body':        {},
        },
        {
            'name':        'add_team_member',
            'description': 'Add a new adjuster or team member. Requires name, email, password, role (adjuster or admin).',
            'method':      'POST',
            'url':         f'{FLOOD_BASE}/willie/api/team',
            'headers':     {'Authorization': f'Bearer {flood_token}'},
            'body':        {'name': '{name}', 'email': '{email}',
                            'password': '{password}', 'role': '{role}'},
        },
        {
            'name':        'delete_team_member',
            'description': 'Remove a team member by their numeric user_id.',
            'method':      'DELETE',
            'url':         f'{FLOOD_BASE}/willie/api/team/{{user_id}}',
            'headers':     {'Authorization': f'Bearer {flood_token}'},
            'body':        {},
        },
        {
            'name':        'get_settings',
            'description': 'Get current FloodClaims Pro app settings (AI model, etc.)',
            'method':      'GET',
            'url':         f'{FLOOD_BASE}/willie/api/settings',
            'headers':     {'Authorization': f'Bearer {flood_token}'},
            'body':        {},
        },
        {
            'name':        'schedule_inspection',
            'description': 'Schedule an inspection for a claim. Requires claim_id, date (YYYY-MM-DD), time (HH:MM). Optional: adjuster_id, notes.',
            'method':      'POST',
            'url':         f'{FLOOD_BASE}/willie/api/claims/{{claim_id}}/schedule',
            'headers':     {'Authorization': f'Bearer {flood_token}'},
            'body':        {{'date': '{{date}}', 'time': '{{time}}', 'notes': '{{notes}}'}},
        },
        {
            'name':        'check_compliance',
            'description': 'Check NFIP compliance score for a claim. Returns percent complete, grade, and list of missing items. Use before submitting.',
            'method':      'GET',
            'url':         f'{FLOOD_BASE}/willie/api/claims/{{claim_id}}/compliance',
            'headers':     {'Authorization': f'Bearer {flood_token}'},
            'body':        {{}},
        },
        {
            'name':        'fema_flood_zone_lookup',
            'description': 'Look up the FEMA flood zone for a claim property address. Updates the claim automatically.',
            'method':      'POST',
            'url':         f'{FLOOD_BASE}/willie/api/claims/{{claim_id}}/fema-lookup',
            'headers':     {'Authorization': f'Bearer {flood_token}'},
            'body':        {{}},
        },
        {
            'name':        'notify_client',
            'description': 'Send a notification to the client. Requires claim_id and message. Optional: method (email, sms, both).',
            'method':      'POST',
            'url':         f'{FLOOD_BASE}/willie/api/claims/{{claim_id}}/notify',
            'headers':     {'Authorization': f'Bearer {flood_token}'},
            'body':        {{'message': '{{message}}', 'method': '{{method}}'}},
        },
        {
            'name':        'move_pipeline',
            'description': 'Move a claim to a new pipeline stage. Status must be: New, In Progress, Submitted, or Closed. Also notifies client.',
            'method':      'POST',
            'url':         f'{FLOOD_BASE}/willie/api/claims/{{claim_id}}/move-pipeline',
            'headers':     {'Authorization': f'Bearer {flood_token}'},
            'body':        {{'status': '{{status}}'}},
        },
        {
            'name':        'get_analytics',
            'description': 'Get business analytics: total claims, pipeline value, closed revenue, average cycle time, status breakdown.',
            'method':      'GET',
            'url':         f'{FLOOD_BASE}/willie/api/analytics',
            'headers':     {'Authorization': f'Bearer {flood_token}'},
            'body':        {{}},
        },
        {
            'name':        'get_schedule',
            'description': 'Get all upcoming scheduled inspections from today onward.',
            'method':      'GET',
            'url':         f'{FLOOD_BASE}/willie/api/schedule',
            'headers':     {'Authorization': f'Bearer {flood_token}'},
            'body':        {{}},
        },
        {
            'name':        'analyze_photos',
            'description': 'Run AI vision analysis on all claim photos. Returns water category, damage summary, suggested rooms and line items. Use update_claim to apply the recommendations.',
            'method':      'POST',
            'url':         f'{FLOOD_BASE}/willie/api/claims/{{claim_id}}/analyze',
            'headers':     {'Authorization': f'Bearer {flood_token}'},
            'body':        {{}},
        },
        {
            'name':        'update_claim_fields',
            'description': 'Update multiple fields on a claim at once. Can set water_category, water_class, flood_source, cause_of_loss, notes, priority, coverage_building, coverage_contents, deductible, policy_number, insurance_company, etc.',
            'method':      'POST',
            'url':         f'{FLOOD_BASE}/willie/api/claims/{{claim_id}}/update',
            'headers':     {'Authorization': f'Bearer {flood_token}'},
            'body':        {{'water_category': '{{water_category}}', 'water_class': '{{water_class}}', 'notes': '{{notes}}', 'priority': '{{priority}}'}},
        },
    ]

    pushed = []
    errors = []
    for action in ACTIONS:
        payload = {
            'name':        action['name'],
            'description': action['description'],
            'method':      action['method'],
            'url':         action['url'],
            'headers':     action['headers'],
            'body':        action['body'],
        }
        try:
            r = _req.post(
                f'{WIDGET_BASE}/agent/{WILLIE_AGENT_ID}/actions/api',
                headers={'Authorization': f'Bearer {willie_key}',
                         'Content-Type': 'application/json'},
                json=payload, timeout=15)
            d = r.json()
            if d.get('ok'):
                pushed.append(action['name'])
            else:
                errors.append({'action': action['name'], 'error': d.get('error', str(d))})
        except Exception as e:
            errors.append({'action': action['name'], 'error': str(e)})

    return jsonify({
        'ok':     len(errors) == 0,
        'pushed': pushed,
        'errors': errors,
        'total':  len(ACTIONS),
        'message': f'{len(pushed)}/{len(ACTIONS)} actions synced to Willie'
    })




@bp.route('/willie/api/claims/<int:claim_id>/update', methods=['POST'])
@bp.route('/willie/api/claims/<int:claim_id>', methods=['PATCH'])
def willie_update_claim(claim_id):
    """Update any field(s) on a claim. Accepts a JSON body with any claim columns.
    Willie uses this to fill in form fields after analyzing photos or reviewing the claim."""
    if not willie_auth(): return jsonify({'error': 'unauthorized'}), 401
    db = get_db()
    claim = db.execute('SELECT * FROM claims WHERE id=?', (claim_id,)).fetchone()
    if not claim: return jsonify({'error': 'Claim not found'}), 404

    data = request.get_json(silent=True) or {}

    # Allowed fields Willie can update
    UPDATABLE = {
        'client_name', 'client_phone', 'client_phone_alt', 'client_email',
        'property_address', 'property_type', 'property_sqft', 'year_built',
        'num_floors', 'flood_date', 'flood_source', 'water_category', 'water_class',
        'water_depth_in', 'date_water_removed', 'inspection_date',
        'insurance_company', 'policy_number', 'policy_type',
        'coverage_building', 'coverage_contents', 'deductible',
        'mortgage_company', 'mortgage_loan_number', 'cause_of_loss',
        'priority', 'notes',
    }

    updates = {k: v for k, v in data.items() if k in UPDATABLE}
    if not updates:
        return jsonify({'error': 'No valid fields provided. Updatable fields: ' + ', '.join(sorted(UPDATABLE))}), 400

    set_clause = ', '.join(f'{k}=?' for k in updates)
    values     = list(updates.values()) + [claim_id]
    db.execute(f'UPDATE claims SET {set_clause}, updated_at=CURRENT_TIMESTAMP WHERE id=?', values)
    db.commit()

    updated_claim = dict(db.execute('SELECT * FROM claims WHERE id=?', (claim_id,)).fetchone())
    return jsonify({
        'ok':      True,
        'updated': list(updates.keys()),
        'message': f'Updated {len(updates)} field(s) on claim {claim["claim_number"]}',
        'claim':   {k: updated_claim.get(k) for k in updates},
    })



# ── Client Feedback Studio ────────────────────────────────────────────────────

FEEDBACK_SYSTEM_PROMPT = """You are a Client Feedback Concierge for FloodClaims Pro, a flood insurance claims management platform owned by Liberty Emporium (Jay Alexander).

Your job is to have a natural conversation with a client to understand what they want in their custom app. You are NOT a generic assistant — you are gathering specific, actionable product requirements.

## How to conduct the conversation:

1. START by welcoming them and asking what they'd like to build or improve.
2. LISTEN carefully to everything they say — every detail matters.
3. ASK SMART FOLLOW-UP QUESTIONS based on their responses:
   - If they mention a feature, ask about specifics (who uses it, what data it needs, what the workflow looks like)
   - If they mention a problem, ask about their current process and what would make it better
   - If they're vague, give them 2-3 options to choose from based on what flood claims businesses typically need
   - If they mention integrations, ask which systems they currently use
4. DO NOT ask boring survey questions. Have a real conversation.
5. Every few messages, briefly summarize what you've understood so far so they can correct you.

## Key areas to explore (when relevant):
- **Claims management**: How they want to create, track, and process claims
- **Photo/AI analysis**: What kind of damage assessment they need
- **Reporting**: What reports they need and who sees them
- **User roles**: Who needs access (adjusters, managers, clients, contractors)
- **Payments/Billing**: How they charge and get paid
- **Integrations**: What other tools they use (insurance company APIs, accounting, etc.)
- **Mobile**: Do they need mobile access or specific mobile features
- **Client portal**: How their customers interact with them

## When the conversation winds down:
Summarize everything they've told you into a structured format:
- **WHO** they are (their business type, size)
- **WHAT** features they want
- **WHY** they need each feature (the problem it solves)
- **PRIORITIES** (what's most important vs nice-to-have)
- **CONCERNS** (any worries or constraints mentioned)

Keep responses warm, professional, and conversational. You are representing Jay's company.
"""



@bp.route('/willie/api/claims/<int:claim_id>/schedule', methods=['POST'])
def willie_schedule_inspection(claim_id):
    if not willie_auth(): return jsonify({'error': 'unauthorized'}), 401
    db    = get_db()
    claim = db.execute('SELECT * FROM claims WHERE id=?', (claim_id,)).fetchone()
    if not claim: return jsonify({'error': 'Claim not found'}), 404
    data      = request.get_json(silent=True) or {}
    slot_date = data.get('date', '').strip()
    slot_time = data.get('time', '09:00').strip()
    adj_id    = data.get('adjuster_id') or claim['adjuster_id']
    notes     = data.get('notes', '').strip()
    if not slot_date:
        return jsonify({'error': 'date is required (YYYY-MM-DD)'}), 400
    db.execute(
        'INSERT INTO inspection_slots (claim_id, adjuster_id, slot_date, slot_time, notes) VALUES (?,?,?,?,?)',
        (claim_id, adj_id, slot_date, slot_time, notes)
    )
    db.execute('UPDATE claims SET sched_date=?, sched_time=?, inspection_date=?, updated_at=CURRENT_TIMESTAMP WHERE id=?',
               (slot_date, slot_time, slot_date, claim_id))
    db.commit()
    _log_activity(claim_id, f'Inspection scheduled for {slot_date} at {slot_time} (by Aquila)', 'Aquila')
    return jsonify({'ok': True, 'claim_id': claim_id, 'date': slot_date, 'time': slot_time,
                    'message': f'Inspection scheduled for {claim["claim_number"]} on {slot_date} at {slot_time}'})


# ── Willie: Compliance Score ────────────────────────────────────────────────


@bp.route('/willie/api/claims/<int:claim_id>/compliance', methods=['GET'])
def willie_compliance_check(claim_id):
    if not willie_auth(): return jsonify({'error': 'unauthorized'}), 401
    db    = get_db()
    claim = db.execute('SELECT * FROM claims WHERE id=?', (claim_id,)).fetchone()
    if not claim: return jsonify({'error': 'Claim not found'}), 404
    rooms  = db.execute('SELECT id FROM rooms WHERE claim_id=?', (claim_id,)).fetchall()
    photos = db.execute('SELECT id FROM photos WHERE claim_id=?', (claim_id,)).fetchall()
    checks = {
        'policy_number':     bool(claim['policy_number']),
        'policy_type':       bool(claim['policy_type']),
        'coverage_building': bool(claim['coverage_building']),
        'coverage_contents': bool(claim['coverage_contents']),
        'deductible':        bool(claim['deductible']),
        'flood_date':        bool(claim['flood_date']),
        'flood_source':      bool(claim['flood_source']),
        'water_category':    bool(claim['water_category']),
        'water_class':       bool(claim['water_class']),
        'water_depth':       bool(claim['water_depth_in']),
        'date_water_removed':bool(claim['date_water_removed']),
        'inspection_date':   bool(claim['inspection_date']),
        'flood_zone':        bool(claim['flood_zone'] and claim['flood_zone'] != 'Unknown'),
        'fema_map':          bool(claim['fema_map_number']),
        'photos_3plus':      len(photos) >= 3,
        'rooms_documented':  len(rooms) >= 1,
        'estimate_done':     bool(claim['total_estimate']),
    }
    score   = sum(1 for v in checks.values() if v)
    total   = len(checks)
    pct     = round(score / total * 100)
    missing = [k for k, v in checks.items() if not v]
    grade   = 'Excellent' if pct >= 90 else 'Good' if pct >= 75 else 'Needs Work' if pct >= 50 else 'Incomplete'
    # 60-day deadline
    deadline = None
    if claim['flood_date']:
        try:
            dl = datetime.datetime.strptime(claim['flood_date'], '%Y-%m-%d') + datetime.timedelta(days=60)
            deadline = dl.strftime('%B %d, %Y')
        except Exception:
            pass
    return jsonify({'ok': True, 'claim_id': claim_id, 'claim_number': claim['claim_number'],
                    'score': score, 'total': total, 'percent': pct, 'grade': grade,
                    'missing': missing, 'proof_of_loss_deadline': deadline,
                    'message': f'{claim["claim_number"]} is {pct}% complete ({grade}). Missing: {missing if missing else "nothing"}'})


# ── Willie: FEMA Lookup ────────────────────────────────────────────────────


@bp.route('/willie/api/claims/<int:claim_id>/fema-lookup', methods=['POST'])
def willie_fema_lookup(claim_id):
    if not willie_auth(): return jsonify({'error': 'unauthorized'}), 401
    db    = get_db()
    claim = db.execute('SELECT * FROM claims WHERE id=?', (claim_id,)).fetchone()
    if not claim: return jsonify({'error': 'Claim not found'}), 404
    result = lookup_fema_flood_zone(claim['property_address'])
    if result and result.get('flood_zone'):
        db.execute('''
            UPDATE claims SET flood_zone=?, fema_map_number=?, lat=?, lng=?,
            maps_embed_url=?, updated_at=CURRENT_TIMESTAMP WHERE id=?
        ''', (result['flood_zone'], result.get('fema_map_number',''),
              result.get('lat',0), result.get('lng',0), result.get('maps_embed_url',''), claim_id))
        db.commit()
        _log_activity(claim_id, f'FEMA flood zone looked up: Zone {result["flood_zone"]} (by Aquila)', 'Aquila')
        return jsonify({'ok': True, 'flood_zone': result['flood_zone'],
                        'fema_map_number': result.get('fema_map_number'),
                        'message': f'FEMA lookup complete. Zone: {result["flood_zone"]}, Map Panel: {result.get("fema_map_number","N/A")}'})
    return jsonify({'ok': False, 'error': 'Could not determine flood zone for this address'})


# ── Willie: Send Notification ────────────────────────────────────────────────


@bp.route('/willie/api/claims/<int:claim_id>/notify', methods=['POST'])
def willie_notify_client(claim_id):
    if not willie_auth(): return jsonify({'error': 'unauthorized'}), 401
    db    = get_db()
    claim = db.execute('SELECT * FROM claims WHERE id=?', (claim_id,)).fetchone()
    if not claim: return jsonify({'error': 'Claim not found'}), 404
    data    = request.get_json(silent=True) or {}
    message = data.get('message', '').strip()
    method  = data.get('method', 'email').lower()  # email or sms
    if not message:
        return jsonify({'error': 'message is required'}), 400
    sent_email = sent_sms_flag = False
    if method in ('email', 'both') and claim['client_email']:
        subject = f'Update on your FloodClaims — {claim["claim_number"]}'
        html = f'<div style="font-family:sans-serif"><h2>FloodClaims Pro Update</h2><p>Hello {claim["client_name"]},</p><p>{message}</p><hr><small>Claim: {claim["claim_number"]}</small></div>'
        sent_email = send_email(claim['client_email'], subject, html)
        if sent_email:
            _log_notification(claim_id, 'manual', claim['client_email'], message)
    if method in ('sms', 'both'):
        sent_sms_flag = notify_client_sms(claim, f'FloodClaims Pro | {claim["claim_number"]}: {message}')
    result = []
    if sent_email: result.append('email')
    if sent_sms_flag: result.append('SMS')
    if not result:
        return jsonify({'ok': False, 'message': 'Notification not sent — check SendGrid/Twilio config in Settings'})
    _log_activity(claim_id, f'Notification sent via {" and ".join(result)} (by Aquila)', 'Aquila')
    return jsonify({'ok': True, 'sent_via': result,
                    'message': f'Notification sent to {claim["client_name"]} via {" and ".join(result)}'})


# ── Willie: Move Pipeline Status ────────────────────────────────────────────────


@bp.route('/willie/api/claims/<int:claim_id>/move-pipeline', methods=['POST'])
def willie_move_pipeline(claim_id):
    if not willie_auth(): return jsonify({'error': 'unauthorized'}), 401
    data   = request.get_json(silent=True) or {}
    status = data.get('status', '').strip()
    valid  = ['New', 'In Progress', 'Submitted', 'Closed']
    if status not in valid:
        return jsonify({'error': f'status must be one of {valid}'}), 400
    db    = get_db()
    claim = db.execute('SELECT * FROM claims WHERE id=?', (claim_id,)).fetchone()
    if not claim: return jsonify({'error': 'Claim not found'}), 404
    old_status = claim['status']
    db.execute('UPDATE claims SET status=?, updated_at=CURRENT_TIMESTAMP WHERE id=?', (status, claim_id))
    db.commit()
    if old_status != status:
        notify_client_status_change(claim, status)
        _log_activity(claim_id, f'Moved from {old_status} → {status} (by Aquila)', 'Aquila')
    return jsonify({'ok': True, 'old_status': old_status, 'new_status': status,
                    'message': f'{claim["claim_number"]} moved from {old_status} to {status}'})


# ── Willie: Analytics Summary ────────────────────────────────────────────────


@bp.route('/willie/api/analytics', methods=['GET'])
def willie_analytics():
    if not willie_auth(): return jsonify({'error': 'unauthorized'}), 401
    db     = get_db()
    claims = db.execute('SELECT * FROM claims ORDER BY created_at ASC').fetchall()
    closed = [c for c in claims if c['status'] == 'Closed']
    open_c = [c for c in claims if c['status'] != 'Closed']
    cycle_times = []
    for c in closed:
        try:
            s = datetime.datetime.fromisoformat(c['created_at'])
            e = datetime.datetime.fromisoformat(c['updated_at'])
            diff = (e - s).days
            if diff >= 0: cycle_times.append(diff)
        except Exception:
            pass
    avg_cycle = round(sum(cycle_times)/len(cycle_times), 1) if cycle_times else None
    return jsonify({'ok': True,
                    'total_claims':    len(claims),
                    'open_pipeline':   sum(c['total_estimate'] for c in open_c),
                    'closed_revenue':  sum(c['total_estimate'] for c in closed),
                    'avg_cycle_days':  avg_cycle,
                    'new':             sum(1 for c in claims if c['status'] == 'New'),
                    'in_progress':     sum(1 for c in claims if c['status'] == 'In Progress'),
                    'submitted':       sum(1 for c in claims if c['status'] == 'Submitted'),
                    'closed':          len(closed),
                    'message': f'{len(claims)} total claims. Pipeline: ${sum(c["total_estimate"] for c in open_c):,.2f}. Avg cycle: {avg_cycle} days.'})


# ── Willie: Get Upcoming Schedule ───────────────────────────────────────────────


@bp.route('/willie/api/schedule', methods=['GET'])
def willie_get_schedule():
    if not willie_auth(): return jsonify({'error': 'unauthorized'}), 401
    db    = get_db()
    today = datetime.date.today().isoformat()
    slots = db.execute('''
        SELECT s.*, c.claim_number, c.client_name, c.property_address, u.name as adjuster_name
        FROM inspection_slots s
        JOIN claims c ON s.claim_id=c.id
        LEFT JOIN users u ON s.adjuster_id=u.id
        WHERE s.slot_date >= ? AND s.status != 'cancelled'
        ORDER BY s.slot_date, s.slot_time LIMIT 20
    ''', (today,)).fetchall()
    return jsonify({'ok': True, 'today': today, 'upcoming': [dict(s) for s in slots],
                    'count': len(slots),
                    'message': f'{len(slots)} upcoming inspection(s) from today onward'})




@bp.route('/willie/api/claims/<int:claim_id>/analyze', methods=['POST'])
def willie_analyze_claim(claim_id):
    """Run vision AI on all claim photos and return structured field recommendations.
    Willie uses this to fill in water_category, water_class, flood_source, damage description,
    and suggested rooms/line items based purely on what the photos show."""
    if not willie_auth(): return jsonify({'error': 'unauthorized'}), 401
    db  = get_db()
    claim = db.execute('SELECT * FROM claims WHERE id=?', (claim_id,)).fetchone()
    if not claim: return jsonify({'error': 'Claim not found'}), 404
    claim = dict(claim)

    key   = get_setting('openrouter_api_key') or OPENROUTER_KEY
    model = get_setting('ai_model') or 'openai/gpt-4o-mini'
    if not key:
        return jsonify({'error': 'OpenRouter API key not configured'}), 400

    photos = [dict(p) for p in db.execute(
        'SELECT * FROM photos WHERE claim_id=? ORDER BY id', (claim_id,)).fetchall()]

    if not photos:
        return jsonify({'ok': False, 'error': 'No photos on this claim yet. Upload photos first so I can analyze them.'}), 400

    # Run fresh vision analysis on all photos (up to 8)
    photo_analyses = []
    for photo in photos[:8]:
        photo_path = os.path.join(UPLOAD_DIR, photo['filename'])
        if not os.path.exists(photo_path):
            continue
        # Always re-run for analyze endpoint — we want fresh detailed descriptions
        desc = ai_describe_photo_detailed(photo_path, key, model)
        if desc:
            label = photo.get('caption') or photo['filename']
            photo_analyses.append({'label': label, 'description': desc, 'photo_id': photo['id']})
            db.execute('UPDATE photos SET ai_description=? WHERE id=?', (desc, photo['id']))
    db.commit()

    if not photo_analyses:
        return jsonify({'ok': False, 'error': 'Could not analyze photos. Check your OpenRouter API key in Settings.'}), 400

    # Build structured analysis prompt
    photos_text = '\n'.join(f'Photo [{p["label"]}]: {p["description"]}' for p in photo_analyses)

    analysis_prompt = f"""You are a licensed flood damage adjuster analyzing photos of a flood-damaged property.

Claim: {claim['claim_number']} | Client: {claim['client_name']} | Address: {claim['property_address']}
Flood Date: {claim['flood_date']}

PHOTO ANALYSES:
{photos_text}

Based ONLY on what you can see in these photos, provide a structured JSON response with your assessment.
Return ONLY valid JSON, no other text:

{{
  "water_category": "1, 2, or 3 (3=floodwater/black water, 2=gray water, 1=clean)",
  "water_class": "1, 2, 3, or 4 (4=hardwood/brick/specialty, 3=ceiling saturated, 2=full room walls, 1=floors only)",
  "flood_source": "brief description of flood source visible in photos",
  "water_depth_in": "estimated water depth in inches based on water lines visible, or empty string",
  "cause_of_loss": "what caused the damage (e.g. Storm surge, Pipe burst, Roof leak, Rising floodwater)",
  "property_type": "Single Family, Condo, Commercial, Mobile Home, or empty",
  "damage_summary": "2-3 sentence professional summary of all damage visible across all photos",
  "suggested_rooms": [
    {{
      "name": "room name",
      "damage_notes": "what needs to be done in this room",
      "line_items": [
        {{"description": "work item", "quantity": number, "unit": "sf/lf/ea", "unit_cost": dollar_amount}}
      ]
    }}
  ],
  "recommended_field_updates": {{
    "water_category": "value",
    "water_class": "value",
    "flood_source": "value",
    "water_depth_in": "value or empty",
    "cause_of_loss": "value",
    "notes": "professional damage summary for claim notes"
  }}
}}"""

    raw = call_openrouter([{'role': 'user', 'content': analysis_prompt}], model, key, max_tokens=2000)

    # Parse JSON from response
    import re as _re
    json_match = _re.search(r'\{[\s\S]+\}', raw)
    if not json_match:
        return jsonify({'ok': False, 'error': 'AI returned non-JSON response', 'raw': raw[:300]}), 500

    try:
        analysis = json.loads(json_match.group(0))
    except Exception:
        return jsonify({'ok': False, 'error': 'Could not parse AI response as JSON', 'raw': raw[:300]}), 500

    return jsonify({
        'ok':             True,
        'claim_id':       claim_id,
        'claim_number':   claim['claim_number'],
        'photos_analyzed': len(photo_analyses),
        'analysis':       analysis,
        'message':        f'Analyzed {len(photo_analyses)} photo(s). Use update_claim_fields to apply the recommendations.',
    })



