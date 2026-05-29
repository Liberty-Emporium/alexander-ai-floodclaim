# FLOODCLAIMS PRO — AI BATCH PHOTO RECOGNITION PLAN
## Feature: "Photo-to-Claim" — Turn Batch Photos into Auto-Populated Damage Lists
## Date: May 30, 2026
## Requested by: Jay Alexander & Billy (flood restoration adjuster)

---

## 1. THE PROBLEM (Billy's Current Workflow)

Billy walks through a flood-damaged property and takes 400+ photos. He then manually reviews every single photo, identifies each damaged item, and types it into the claim. This takes hours per property.

**Pain points:**
- Manually reviewing 400+ photos one by one
- Manually typing each damaged item into the claim line items
- Missing high-value items that need special documentation
- Customers won't submit photos through current upload flow (email links don't work)

---

## 2. THE SOLUTION — "Photo-to-Claim" AI Workflow

### 2.1 Mass Photo Upload
- Adjuster taps "Scan Room" button in the FloodClaims app
- Takes multiple photos in quick succession (or selects from gallery)
- Photos are uploaded as a batch, tagged to a specific room
- **No individual upload prompts** — just rapid-fire camera or multi-select

### 2.2 AI Batch Recognition (NEW)
- AI receives ALL photos from a room at once (up to 20 photos per batch)
- Uses a vision model (Claude Sonnet 4.5 or GPT-4o with vision) to analyze the full set
- Returns a **structured damage inventory** — items with quantities, condition, estimated value

**AI Prompt Output Format:**
```json
{
  "room": "Master Bedroom",
  "items": [
    {
      "description": "Water-damaged carpet",
      "quantity": 1,
      "unit": "sq_ft",
      "estimated_area": "200",
      "condition": "saturated, needs full replacement",
      "category": "flooring",
      "priority": "standard"
    },
    {
      "description": "Water-stained drywall (peeling paint)",
      "quantity": 2,
      "unit": "walls",
      "condition": "full replacement needed, 4ft water line visible",
      "category": "walls",
      "priority": "standard"
    },
    {
      "description": "Designer handbag (Prada, black leather)",
      "quantity": 1,
      "unit": "each",
      "condition": "water damage, needs close-up photo for appraisal",
      "category": "personal_property_high_value",
      "priority": "HIGH_VALUE_FLAG"
    }
  ],
  "summary": "Master bedroom with 4ft water damage line. Carpet saturated, drywall peeling on 2 walls. One high-value personal item flagged for close-up documentation.",
  "water_category": 3,
  "water_class": 2,
  "confidence": "high",
  "needs_closeup": ["Designer handbag (black leather, suspected Prada)"]
}
```

### 2.3 Auto-Populate Claim (NEW)
- AI output is automatically converted to claim line items
- Adjuster sees a pre-populated list and reviews/edits/approves each line
- **One-tap approve all** or edit individual items
- Quantities, descriptions, and categories are pre-filled from AI analysis

### 2.4 High-Value Item Flagging (NEW)
- AI specifically looks for high-value items: jewelry, designer bags, electronics, art, antiques
- Flags items as "NEEDS CLOSE-UP" with a special UI badge
- Adds a reminder for the adjuster to take extreme close-up photos for insurance documentation

### 2.5 Simple Customer Photo Submission (NEW)
- **Current flow (BROKEN):** Email link → customer uploads one by one → most don't finish
- **New flow:**
  1. Adjuster texts customer: "Text [phone] to submit photos for your claim"
  2. Customer taps link → opens mobile web app
  3. App shows: "Take photos of damaged areas. Tap the camera to begin."
  4. Customer snaps photos directly in the browser (uses device camera API)
  5. Photos auto-upload in batch — no individual file picker needed
  6. When done, customer taps "Submit" → photos appear in Billy's claim dashboard
- **Key:** Works on ANY phone, no app download, no account creation

---

## 3. TECHNICAL ARCHITECTURE

### 3.1 Backend Changes (`app.py`)

**New Route: `POST /claim/<id>/room/<room_id>/batch-analyze`**
- Accepts multipart form data with multiple image files
- Stores each photo in the `photos` table with `room_id` reference
- Sends batch to AI vision model
- Parses JSON response into `line_items` table rows
- Returns structured JSON of detected items

**New Route: `POST /claim/<id>/ai-populate`**
- Triggers batch analysis on ALL unanalyzed photos for a claim
- Returns full auto-populated item list
- Adjuster reviews via existing claim edit UI

**New Route: `POST /customer/upload/:token` (Public, No Auth)**
- Simple upload page accessible via texted link
- Token-based access (expires after 7 days)
- Uses browser `getUserMedia` API for direct camera capture
- Multi-file drag/drop or camera capture
- Shows upload progress
- Confirmation screen after submit

**Updated: `Photo` table schema**
```sql
ALTER TABLE photos ADD COLUMN batch_id TEXT;
ALTER TABLE photos ADD COLUMN room_id INTEGER;
ALTER TABLE photos ADD COLUMN ai_raw_json TEXT;          -- Full AI response JSON
ALTER TABLE photos ADD COLUMN detected_items JSON;        -- Array of detected items
ALTER TABLE photos ADD COLUMN is_high_value INTEGER DEFAULT 0;
ALTER TABLE photos ADD COLUMN needs_closeup INTEGER DEFAULT 0;
ALTER TABLE photos ADD COLUMN customer_submitted INTEGER DEFAULT 0;
```

### 3.2 AI Model Selection

Current: `openrouter/owl-alpha` (text only — NO vision)
**New: Must use a vision-capable model**

| Model | Vision? | Cost | Quality | Notes |
|-------|---------|------|---------|-------|
| `openai/gpt-4o` | ✅ | ~$2.50/1K img | Excellent | Best object recognition |
| `anthropic/claude-sonnet-4-20250514` | ✅ | ~$3.00/1K img | Excellent | Best reasoning about damage |
| `google/gemini-2.0-flash` | ✅ | ~$0.10/1K img | Good | Cheapest, fast |
| `openrouter/owl-alpha` | ❌ | N/A | N/A | Current model — NO vision |

**Recommendation:** Use `anthropic/claude-sonnet-4` as primary (best reasoning about damage severity and repair needs), with `openai/gpt-4o` as fallback.

### 3.3 Photo Processing Pipeline

```
Photos uploaded (batch)
    ↓
Stored in /data/uploads/ with batch_id + room_id
    ↓
AI receives up to 20 images at once (base64 encoded)
    ↓
AI returns structured JSON (items, summary, flags)
    ↓
JSON parsed → line_items rows created (status: "ai_proposed")
    ↓
UI shows AI-proposed items with approve/edit/reject buttons
    ↓
Adjuster reviews, edits, approves
    ↓
Approved items become "confirmed" line items
    ↓
High-value flagged items show special UI with close-up reminder
```

### 3.4 Frontend Changes

**New: Batch Upload UI (`templates/claim_detail.html`)**
- "Scan Room" button opens camera/multi-select
- Photo preview carousel before upload
- Upload progress bar
- "AI is analyzing..." spinner after upload
- Results appear as editable line items below

**New: Customer Upload Page (`templates/customer_upload.html`)**
- No login required (token-based)
- Large camera button: "Take Photo"
- Photo counter: "5 photos taken"
- Gallery: thumbnail grid with delete per photo
- "Submit Photos" button
- Confirmation: "Your photos have been sent to your adjuster."

**Updated: Line Items Display**
- AI-proposed items shown with orange "AI" badge
- Approve (✓) / Edit (✗) / Reject (🗑) buttons per item
- High-value items show 💎 "HIGH VALUE — Take Close-Up" banner
- "Approve All AI Items" bulk action button

---

## 4. DEVELOPMENT PLAN

### Phase 1: Backend Batch Analysis (OWL leads)
| Task | File | Est. Time |
|------|------|-----------|
| Add `room_id`, `batch_id`, `ai_raw_json` columns to photos table | `app.py` (migration) | 30 min |
| New route: `POST /claim/<id>/room/<room_id>/batch-analyze` | `app.py` | 2 hours |
| New route: `POST /claim/<id>/ai-populate` | `app.py` | 1.5 hours |
| AI prompt engineering for batch photo analysis | `app.py` | 1 hour |
| JSON parsing → line_items creation logic | `app.py` | 1.5 hours |
| High-value item detection logic | `app.py` | 1 hour |
| **Phase 1 Total** | | **~7 hours** |

### Phase 2: Frontend Batch Upload UI (Self leads)
| Task | File | Est. Time |
|------|------|-----------|
| "Scan Room" button + photo capture UI | `claim_detail.html` | 2 hours |
| Multi-file upload with preview carousel | `claim_detail.html` + `base.html` JS | 2 hours |
| AI analysis progress indicator | `claim_detail.html` | 30 min |
| AI-proposed items display (approve/edit/reject) | `claim_detail.html` | 2 hours |
| High-value item flagging UI (💎 badge) | `claim_detail.html` | 1 hour |
| "Approve All" bulk action | `claim_detail.html` | 30 min |
| **Phase 2 Total** | | **~8 hours** |

### Phase 3: Customer Photo Submission (OWL leads)
| Task | File | Est. Time |
|------|------|-----------|
| Token generation + SMS link sending | `app.py` (new route) | 1 hour |
| Customer upload page (no auth) | `customer_upload.html` | 2 hours |
| Browser camera API integration | `customer_upload.html` JS | 1.5 hours |
| Batch upload from customer → adjuster claim | `app.py` | 1 hour |
| Customer-submitted photos appear in adjuster dashboard | `claim_detail.html` | 1 hour |
| **Phase 3 Total** | | **~6.5 hours** |

### Phase 4: Testing & Deployment
| Task | Est. Time |
|------|-----------|
| Test batch analysis with real flood photos | 2 hours |
| Test customer upload flow on mobile | 1 hour |
| Edge cases: poor lighting, blurry photos, many items | 1 hour |
| Railway deploy + verify | 30 min |
| **Phase 4 Total** | **~4.5 hours** |

**TOTAL ESTIMATED TIME: ~26 hours**

---

## 5. AI PROMPT (Batch Analysis)

```
You are an expert flood damage insurance adjuster. You will receive multiple photos of 
a single room in a flood-damaged property. Analyze ALL photos together and return a 
JSON response with the following structure:

{
  "room": "<room name or best guess>",
  "items": [
    {
      "description": "<specific damaged item, e.g., 'water-damaged carpet'>",
      "quantity": <number>,
      "unit": "<unit of measure, e.g., 'sq_ft', 'each', 'linear_ft'>",
      "estimated_area": "<estimated size/area if applicable, e.g., '200 sq ft'>",
      "condition": "<damage description: water line height, saturation, peeling, mold, etc.>",
      "category": "<one of: flooring, walls, ceiling, electrical, plumbing, furniture, personal_property, structural, insulation, other>",
      "priority": "<standard or high_value>"
    }
  ],
  "summary": "<2-3 sentence overall assessment of the room damage>",
  "water_category": "<1, 2, or 3 based on water source: 1=clean, 2=gray, 3=black>",
  "water_class": "<1, 2, 3, or 4 based on extent: 1=small area, 2=large area, 3=whole room, 4=saturated>",
  "confidence": "<low, medium, or high>",
  "needs_closeup": ["<list of items that need extreme close-up photos for proper documentation>"]
}

RULES:
- Be specific. "Water-damaged carpet, approximately 200 sq ft, fully saturated, needs full replacement" 
  is better than "carpet damaged."
- Look for high-value items: jewelry, designer bags/shoes, art, antiques, electronics. 
  Flag any detected high-value item in the needs_closeup array with brand name if visible.
- Estimate water category (1=clean, 2=gray, 3=black) and class (1-4) based on visible water lines and damage extent.
- Use the quantity and unit fields to help with insurance cost estimation.
- Items should be detailed enough to create insurance claim line items directly from this list.
- If you cannot determine something, use "unknown" rather than guessing.
```

---

## 6. DEPENDENCIES & RISKS

**Dependencies:**
- Current `OPENROUTER_API_KEY` supports vision models (verify before starting)
- Railway instance has enough storage for batch photo uploads
- Front-end JS must handle large file uploads (increase MAX_CONTENT_LENGTH to 50MB)

**Risks:**
- AI hallucination on items → mitigated by adjuster review step (AI proposes, human approves)
- Large batch uploads may timeout → implement async processing with polling
- Mobile camera quality varies → AI prompt should handle low-quality images gracefully
- Cost of vision API calls → ~$0.01-0.03 per image analysis; batch of 20 photos ≈ $0.20-0.60 per room

---

## 7. SUCCESS METRICS

- Billy can process a 400-photo property in under 30 minutes (vs. hours currently)
- AI auto-populates 80%+ of line items correctly on first pass
- Customer photo submission rate increases from ~20% to ~80%+ with new SMS link flow
- High-value items are never missed (100% flagging rate)

---

## APPENDIX: What Exists Today

Current `ai_describe_photo()` function (line 1336 in app.py):
- Takes ONE photo at a time
- Returns 2-3 sentence text description
- Stored in `photos.ai_description` text field
- No structured data (no quantities, categories, or flags)
- Uses OWL-alpha (text-only model — CANNOT process images)

**The fix:** Replace single-photo text descriptions with batch-photo structured JSON analysis using a vision-capable AI model. The adjuster reviews AI-proposed items instead of starting from scratch.
