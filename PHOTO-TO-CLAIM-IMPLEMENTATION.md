# 📸 PHOTO-TO-CLAIM — Implementation Guide for Mingo
**Prepared by:** Django (OWL)  
**Date:** 2026-06-04 23:35 EDT  
**Based on:** FloodClaims Pro refactor/modular branch (commit 131838f)  
**Prerequisite:** Merge refactor/modular → main first, THEN build this on top

---

## 🎯 WHAT WE'RE BUILDING

Billy takes 400+ photos per property. AI analyzes them in batches, returns structured damage data, and auto-populates claim line items. Billy reviews and approves — goes from hours to minutes.

**Full spec:** `/home/lol/Desktop/openclaw/alexander-ai-floodclaim/photo-to-claim-plan.md`

---

## 🏗️ REFACTORED CODEBASE MAP

The refactor split the 7,356-line monolith into clean modules. Here's where Photo-to-Claim touches:

```
routes/photos.py     ← ADD batch-analyze route here (or create new blueprint)
routes/claims.py     ← ADD ai-populate route here
routes/rooms.py      ← Already has room CRUD, no changes needed
services/ai.py       ← ADD batch vision analysis function here
models/database.py   ← ADD migration for new photo columns
templates/           ← ADD batch upload UI to claim_detail.html
                     ← CREATE customer_upload.html
```

### Key Files (refactor branch)

| File | Purpose | What to Add |
|------|---------|-------------|
| `routes/photos.py` | Photo upload/view | `POST /claim/<id>/room/<room_id>/batch-analyze` |
| `routes/claims.py` | Claim CRUD + AI estimate | `POST /claim/<id>/ai-populate` |
| `services/ai.py` | AI helper functions | `batch_analyze_photos(images, room_name)` |
| `models/database.py` | DB layer | Migration: add `batch_id`, `ai_raw_json`, `detected_items`, `is_high_value`, `needs_closeup`, `customer_submitted` to photos table |
| `routes/customer.py` | Customer-facing routes | `GET/POST /customer/upload/<token>` (public, no auth) |

---

## 📋 PHASE 1: Backend Batch Analysis (~7 hours)

### Step 1: DB Migration (30 min)

In `models/database.py`, add a migration function:

```python
def migrate_photos_batch_columns():
    """Add Photo-to-Claim columns to photos table."""
    db = get_db()
    # Check if columns exist first (idempotent)
    cols = [row[1] for row in db.execute("PRAGMA table_info(photos)").fetchall()]
    if 'batch_id' not in cols:
        db.execute('ALTER TABLE photos ADD COLUMN batch_id TEXT')
    if 'ai_raw_json' not in cols:
        db.execute('ALTER TABLE photos ADD COLUMN ai_raw_json TEXT')
    if 'detected_items' not in cols:
        db.execute('ALTER TABLE photos ADD COLUMN detected_items TEXT')  # JSON array
    if 'is_high_value' not in cols:
        db.execute('ALTER TABLE photos ADD COLUMN is_high_value INTEGER DEFAULT 0')
    if 'needs_closeup' not in cols:
        db.execute('ALTER TABLE photos ADD COLUMN needs_closeup INTEGER DEFAULT 0')
    if 'customer_submitted' not in cols:
        db.execute('ALTER TABLE photos ADD COLUMN customer_submitted INTEGER DEFAULT 0')
    db.commit()
```

Call this from the app factory or a `/admin/migrate` endpoint.

### Step 2: AI Batch Analysis Service (2 hours)

In `services/ai.py`, add:

```python
import base64
import json

def batch_analyze_photos(image_paths, room_name="Unknown Room"):
    """
    Send multiple photos to a vision-capable AI model.
    Returns structured damage inventory JSON.
    
    IMPORTANT: Must use a VISION-capable model.
    Current model 'openrouter/owl-alpha' is TEXT-ONLY and CANNOT process images.
    Recommended: 'anthropic/claude-sonnet-4-20250514' or 'openai/gpt-4o'
    """
    key = get_setting('openrouter_api_key') or OPENROUTER_KEY
    if not key:
        return None, "OpenRouter API key not configured"
    
    # Use vision-capable model
    model = get_setting('ai_vision_model', 'anthropic/claude-sonnet-4-20250514')
    
    # Encode images as base64
    content = []
    for path in image_paths[:20]:  # Max 20 images per batch
        with open(path, 'rb') as f:
            b64 = base64.b64encode(f.read()).decode()
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}"}
        })
    
    # Add text prompt
    content.append({"type": "text", "text": BATCH_ANALYSIS_PROMPT.replace("{room_name}", room_name)})
    
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "max_tokens": 4000
    }
    
    try:
        resp = requests.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json=payload,
            timeout=120
        )
        resp.raise_for_status()
        result = resp.json()
        text = result['choices'][0]['message']['content']
        # Parse JSON from response
        data = json.loads(text)
        return data, None
    except Exception as e:
        return None, str(e)

BATCH_ANALYSIS_PROMPT = """You are an expert flood damage insurance adjuster. You will receive multiple photos of {room_name} in a flood-damaged property. Analyze ALL photos together and return ONLY valid JSON (no markdown, no code fences):

{
  "room": "<room name>",
  "items": [
    {
      "description": "<specific damaged item>",
      "quantity": <number>,
      "unit": "<sq_ft|each|linear_ft|walls>",
      "estimated_area": "<size if applicable>",
      "condition": "<damage description>",
      "category": "<flooring|walls|ceiling|electrical|plumbing|furniture|personal_property|structural|insulation|other>",
      "priority": "<standard|high_value>"
    }
  ],
  "summary": "<2-3 sentence overall damage assessment>",
  "water_category": <1|2|3>,
  "water_class": <1|2|3|4>,
  "confidence": "<low|medium|high>",
  "needs_closeup": ["<items needing close-up photos>"]
}

RULES: Be specific. Flag high-value items (jewelry, designer bags, art, antiques, electronics). If unsure, use "unknown"."""
```

### Step 3: Batch Analyze Route (2 hours)

In `routes/photos.py`, add:

```python
@bp.route('/claim/<int:claim_id>/room/<int:room_id>/batch-analyze', methods=['POST'])
@login_required
@csrf_required
def batch_analyze(claim_id, room_id):
    """Upload multiple photos and run AI batch analysis."""
    db = get_db()
    
    # Verify claim + room exist
    claim = db.execute('SELECT * FROM claims WHERE id=?', (claim_id,)).fetchone()
    room = db.execute('SELECT * FROM rooms WHERE id=? AND claim_id=?', (room_id, claim_id)).fetchone()
    if not claim or not room:
        return jsonify({'ok': False, 'error': 'Claim or room not found'}), 404
    
    files = request.files.getlist('photos')
    if not files:
        return jsonify({'ok': False, 'error': 'No photos uploaded'}), 400
    
    batch_id = secrets.token_hex(8)
    upload_paths = []
    
    # Save all photos
    for file in files[:20]:  # Max 20
        if not allowed_file(file.filename):
            continue
        ext = file.filename.rsplit('.', 1)[1].lower()
        filename = f'{secrets.token_hex(12)}.{ext}'
        save_path = os.path.join(UPLOAD_DIR, filename)
        
        # Compress (reuse existing logic)
        try:
            from PIL import Image as _PILImage
            img = _PILImage.open(file)
            max_dim = max(img.size)
            if max_dim > 2048:
                scale = 2048 / max_dim
                img = img.resize((int(img.size[0]*scale), int(img.size[1]*scale)), _PILImage.LANCZOS)
            img.save(save_path, optimize=True, quality=80)
        except Exception:
            file.save(save_path)
        
        upload_paths.append(save_path)
        
        db.execute(
            'INSERT INTO photos (claim_id, room_id, filename, batch_id, customer_submitted) VALUES (?,?,?,?,?)',
            (claim_id, room_id, filename, batch_id, 0)
        )
    
    db.commit()
    
    # Run AI batch analysis
    ai_result, error = batch_analyze_photos(upload_paths, room['name'])
    
    if error:
        return jsonify({'ok': False, 'error': f'AI analysis failed: {error}'}), 500
    
    # Parse AI results → line_items
    items_created = 0
    for item in ai_result.get('items', []):
        total = item.get('quantity', 1) * 0  # unit_cost unknown from AI
        db.execute(
            'INSERT INTO line_items (claim_id, room_id, description, quantity, unit, unit_cost, total, status, source) '
            'VALUES (?,?,?,?,?,?,?,?,?)',
            (claim_id, room_id, item['description'], item.get('quantity', 1),
             item.get('unit', 'each'), 0, total, 'ai_proposed', 'batch_ai')
        )
        items_created += 1
    
    # Store raw AI response on first photo in batch
    if upload_paths:
        first_filename = os.path.basename(upload_paths[0])
        db.execute('UPDATE photos SET ai_raw_json=?, detected_items=? WHERE filename=?',
                    (json.dumps(ai_result), json.dumps(ai_result.get('items', [])), first_filename))
    
    # Flag high-value items
    for item in ai_result.get('items', []):
        if item.get('priority') == 'high_value':
            db.execute('UPDATE photos SET is_high_value=1 WHERE batch_id=?', (batch_id,))
            break
    
    db.commit()
    recalc_claim(claim_id)
    _log_activity(claim_id, f'Batch AI analysis: {items_created} items detected in {room["name"]}')
    
    return jsonify({
        'ok': True,
        'batch_id': batch_id,
        'items': ai_result.get('items', []),
        'summary': ai_result.get('summary', ''),
        'water_category': ai_result.get('water_category'),
        'water_class': ai_result.get('water_class'),
        'needs_closeup': ai_result.get('needs_closeup', []),
        'items_created': items_created
    })
```

### Step 4: AI Populate Route (1.5 hours)

In `routes/claims.py`, add:

```python
@bp.route('/claim/<int:claim_id>/ai-populate', methods=['POST'])
@login_required
def ai_populate(claim_id):
    """Trigger batch analysis on ALL unanalyzed photos for a claim."""
    db = get_db()
    
    # Get all rooms for this claim
    rooms = db.execute('SELECT * FROM rooms WHERE claim_id=? AND deleted_at IS NULL', (claim_id,)).fetchall()
    
    total_items = 0
    for room in rooms:
        # Get unanalyzed photos for this room
        photos = db.execute(
            'SELECT * FROM photos WHERE claim_id=? AND room_id=? AND batch_id IS NULL AND deleted_at IS NULL',
            (claim_id, room['id'])
        ).fetchall()
        
        if not photos:
            continue
        
        paths = [os.path.join(UPLOAD_DIR, p['filename']) for p in photos if os.path.exists(os.path.join(UPLOAD_DIR, p['filename']))]
        if not paths:
            continue
        
        batch_id = secrets.token_hex(8)
        ai_result, error = batch_analyze_photos(paths, room['name'])
        
        if error:
            continue
        
        for item in ai_result.get('items', []):
            db.execute(
                'INSERT INTO line_items (claim_id, room_id, description, quantity, unit, unit_cost, total, status, source) '
                'VALUES (?,?,?,?,?,?,?,?,?)',
                (claim_id, room['id'], item['description'], item.get('quantity', 1),
                 item.get('unit', 'each'), 0, 0, 'ai_proposed', 'batch_ai')
            )
            total_items += 1
        
        # Mark photos as analyzed
        for p in photos:
            db.execute('UPDATE photos SET batch_id=?, ai_raw_json=? WHERE id=?',
                        (batch_id, json.dumps(ai_result) if p == photos[0] else None, p['id']))
    
    db.commit()
    recalc_claim(claim_id)
    _log_activity(claim_id, f'AI populate: {total_items} items auto-generated')
    
    return jsonify({'ok': True, 'items_created': total_items})
```

### Step 5: High-Value Detection (1 hour)

Already handled in the batch analysis — items with `"priority": "high_value"` get flagged. The `is_high_value` column on photos enables the UI badge.

---

## 📋 PHASE 2: Frontend Batch Upload UI (~8 hours)

### Changes to `templates/claim_detail.html`:

1. **"Scan Room" button** — Opens camera/multi-select
2. **Multi-file upload** — `<input type="file" multiple accept="image/*" capture="environment">`
3. **Preview carousel** — Show thumbnails before upload
4. **Upload progress** — XMLHttpRequest with progress event
5. **AI analysis spinner** — "Analyzing 12 photos..." while waiting
6. **AI-proposed items display** — Orange "AI" badge, approve/edit/reject per item
7. **High-value flagging** — 💎 "HIGH VALUE — Take Close-Up" banner
8. **"Approve All" bulk action** — One-tap approve all AI items

### Key JavaScript pattern:

```javascript
// Batch upload + analyze
async function scanRoom(claimId, roomId) {
    const input = document.createElement('input');
    input.type = 'file';
    input.multiple = true;
    input.accept = 'image/*';
    input.capture = 'environment';
    input.onchange = async (e) => {
        const files = Array.from(e.target.files);
        // Show preview carousel
        showPreview(files);
        // Upload
        const formData = new FormData();
        files.forEach(f => formData.append('photos', f));
        showSpinner(`Analyzing ${files.length} photos...`);
        const resp = await fetch(`/claim/${claimId}/room/${roomId}/batch-analyze`, {
            method: 'POST',
            body: formData
        });
        const data = await resp.json();
        if (data.ok) {
            showAIResults(data.items, data.needs_closeup);
        } else {
            showError(data.error);
        }
    };
    input.click();
}
```

---

## 📋 PHASE 3: Customer Photo Submission (~6.5 hours)

### New file: `templates/customer_upload.html`

- No login required (token-based access)
- Large camera button
- Photo counter
- Thumbnail grid with delete
- "Submit Photos" button
- Confirmation screen

### New route in `routes/customer.py`:

```python
@bp.route('/customer/upload/<token>', methods=['GET', 'POST'])
def customer_upload(token):
    """Public upload page — no auth required. Token expires after 7 days."""
    db = get_db()
    # Validate token
    upload_token = db.execute(
        'SELECT * FROM upload_tokens WHERE token=? AND expires_at > datetime("now") AND used=0',
        (token,)
    ).fetchone()
    
    if not upload_token:
        return render_template('customer_upload.html', error='This link has expired or is invalid.'), 404
    
    if request.method == 'POST':
        files = request.files.getlist('photos')
        claim_id = upload_token['claim_id']
        batch_id = f'customer_{secrets.token_hex(8)}'
        
        for file in files[:20]:
            if not allowed_file(file.filename):
                continue
            ext = file.filename.rsplit('.', 1)[1].lower()
            filename = f'{secrets.token_hex(12)}.{ext}'
            save_path = os.path.join(UPLOAD_DIR, filename)
            file.save(save_path)
            db.execute(
                'INSERT INTO photos (claim_id, filename, batch_id, customer_submitted) VALUES (?,?,?,1)',
                (claim_id, filename, batch_id)
            )
        
        db.execute('UPDATE upload_tokens SET used=1 WHERE token=?', (token,))
        db.commit()
        return render_template('customer_upload.html', success=True)
    
    return render_template('customer_upload.html', token=token)
```

### Also need: Token generation route (in `routes/claims.py`):

```python
@bp.route('/claim/<int:claim_id>/generate-upload-link', methods=['POST'])
@login_required
def generate_upload_link(claim_id):
    """Generate a customer upload token and return the link."""
    token = secrets.token_hex(32)
    db = get_db()
    db.execute(
        'INSERT INTO upload_tokens (claim_id, token, expires_at) VALUES (?, ?, datetime("now", "+7 days"))',
        (claim_id, token)
    )
    db.commit()
    link = f"{request.host_url}customer/upload/{token}"
    return jsonify({'ok': True, 'link': link})
```

### DB addition for upload_tokens:

```python
# In models/database.py migration
def migrate_upload_tokens():
    db = get_db()
    db.execute('''CREATE TABLE IF NOT EXISTS upload_tokens (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        claim_id INTEGER NOT NULL,
        token TEXT UNIQUE NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        expires_at DATETIME NOT NULL,
        used INTEGER DEFAULT 0,
        FOREIGN KEY (claim_id) REFERENCES claims(id)
    )''')
    db.commit()
```

---

## ⚠️ CRITICAL: Model Change Required

**The current AI model `openrouter/owl-alpha` is TEXT-ONLY and CANNOT process images.**

You MUST configure a vision-capable model. Add to Settings or environment:

```
ai_vision_model = anthropic/claude-sonnet-4-20250514
```

Or as fallback: `openai/gpt-4o`

Cost: ~$0.01-0.03 per image. Batch of 20 photos ≈ $0.20-0.60 per room.

---

## 📋 PHASE 4: Testing & Deployment (~4.5 hours)

1. Test batch analysis with real flood photos
2. Test customer upload flow on mobile
3. Edge cases: poor lighting, blurry photos, many items
4. Railway deploy + verify

---

## 🔑 ENVIRONMENT VARIABLES NEEDED

| Variable | Purpose | Required |
|----------|---------|----------|
| `OPENROUTER_API_KEY` | AI API access | ✅ Yes |
| `ai_vision_model` | Vision model selection | ✅ Yes (must be vision-capable) |
| `MAX_CONTENT_LENGTH` | Increase to 50MB for batch uploads | ✅ Yes |

---

## 📊 SUCCESS METRICS

- Billy processes 400-photo property in < 30 min (vs hours)
- AI auto-populates 80%+ of line items correctly
- Customer photo submission rate: 20% → 80%+
- High-value items: 100% flagging rate

---

*This guide is mapped to the refactor/modular branch structure. Merge that branch first, then build on top.*
