# 🔧 FloodClaims Pro — Modularization Plan
**Created:** 2026-06-04 15:39 EDT by Django  
**Status:** ANALYSIS COMPLETE — Ready for Mingo review  
**Risk Level:** HIGH — Billy's paying app. No pushes without Mingo + Jay approval.

---

## 📊 Current State

| Metric | Value |
|--------|-------|
| Total lines | 7,355 |
| Total functions | 214 |
| Total routes | 130 |
| Classes | 0 (all plain functions) |
| File count | 1 (monolith) |
| Health | ✅ Currently UP (200) |

---

## 🎯 Target Structure

```
alexander-ai-floodclaim/
├── app.py                  # Flask app setup ONLY (< 100 lines)
├── config.py               # Config class (env vars, secrets)
├── models/
│   ├── __init__.py         # DB init, migrations
│   └── database.py         # get_db, close_db, init_db, migrations
├── routes/
│   ├── __init__.py         # Blueprint registration
│   ├── auth.py             # login, logout, dashboard
│   ├── claims.py           # CRUD, detail, mobile, status, notes
│   ├── rooms.py            # add/delete rooms
│   ├── items.py            # add/delete line items
│   ├── photos.py           # upload, delete, analyze, edit
│   ├── reports.py          # PDF, Xactimate export
│   ├── billing.py          # Stripe checkout, portal, success
│   ├── admin.py            # settings, team, feedback, training
│   ├── willie.py           # Willie AI chat + API
│   ├── feedback.py         # client feedback portal
│   ├── training.py         # adjuster training, exams, certificates
│   ├── pipeline.py         # pipeline view + move
│   ├── schedule.py         # scheduling
│   ├── analytics.py        # analytics dashboard
│   ├── customer.py         # customer upload portal
│   └── api.py              # /health, /ready, /api/status
├── services/
│   ├── __init__.py
│   ├── ai.py               # OpenRouter calls, photo analysis, estimates
│   ├── email.py            # SendGrid email + notifications
│   ├── fema.py             # FEMA flood zone lookup
│   ├── claims.py           # claim number gen, recalc, NFIP fill
│   └── willie.py           # Willie token auth, estimate jobs
├── utils/
│   ├── __init__.py
│   ├── security.py         # CSRF, bot blocking, security headers
│   ├── auth_decorators.py  # login_required, admin_required, manager_required
│   ├── helpers.py          # allowed_file, rate limiting, secrets
│   └── settings.py         # get_setting, set_setting
├── templates/              # (unchanged)
├── tests/                  # (expand)
├── requirements.txt        # (unchanged)
├── Procfile               # (unchanged)
└── railway.json           # (unchanged)
```

---

## 📦 Module Breakdown — Function Mapping

### `app.py` (target: < 100 lines)
Keep ONLY:
- `import` statements
- `app = Flask(__name__)`
- Config loading
- Blueprint registration
- `app.teardown_appcontext(close_db)`
- `app.after_request(security_headers)`
- `if __name__ == '__main__': app.run()`

### `config.py` (~50 lines)
Extract from lines 27-55:
- SECRET_KEY logic
- UPLOAD_FOLDER config
- STRIPE/SENDGRID/WEASYPRINT feature flags
- _PET_VET_AI_URL constant

### `models/database.py` (~450 lines)
Extract functions at lines 195-647:
- `_ensure_db_initialized()` (195)
- `get_db()` (215)
- `close_db()` (226)
- `init_db()` (244)
- `hash_pw()` (448)
- `check_pw()` (454)
- `migrate_claims_columns()` (473)
- `migrate_new_features()` (509)
- `migrate_photos_columns()` (577)
- `migrate_new_features_v2()` (596)
- `get_setting()` (757)
- `set_setting()` (769)

### `utils/security.py` (~130 lines)
Extract functions at lines 113-194:
- `_get_csrf_token()` (113)
- `_validate_csrf()` (119)
- `_block_bot_paths()` (141)
- `_csrf_protect()` (149)
- `csrf_required` decorator (163)
- `allowed_file()` (189)
- `security_headers()` (231)

### `utils/auth_decorators.py` (~30 lines)
Extract from lines 710-738:
- `login_required` (710)
- `admin_required` (719)
- `manager_required` (728)

### `utils/helpers.py` (~60 lines)
Extract from lines 56-112, 1563-1573:
- `_get_secret_key()` (56)
- `is_rate_limited()` (1563)

### `utils/settings.py` (~35 lines)
Extract from lines 757-778:
- `get_setting()` (757)
- `set_setting()` (769)

### `services/ai.py` (~300 lines)
Extract from lines 797-1562:
- `_run_estimate_job()` (797)
- `_build_pricing_kb()` (843)
- `_build_estimate_prompt()` (899)
- `call_openrouter()` (1476)
- `ai_describe_photo()` (1517)
- `ai_describe_photo_detailed()` (5451)

### `services/email.py` (~50 lines)
Extract from lines 676-710:
- `send_email()` (676)
- `notify_client_status_change()` (692)

### `services/fema.py` (~30 lines)
Extract from lines 648-675:
- `lookup_fema_flood_zone()` (648)

### `services/claims.py` (~50 lines)
Extract from lines 739-756:
- `gen_claim_number()` (739)
- `recalc_claim()` (744)

### `services/willie.py` (~30 lines)
Extract from lines 779-796:
- `get_willie_token()` (779)
- `willie_auth()` (787)

### `routes/auth.py` (~100 lines)
Extract routes at lines 1574-1678:
- `/` → `index()` (1574)
- `/login` → `login()` (1580)
- `/logout` → `logout()` (1614)
- `/dashboard` → `dashboard()` (1619)

### `routes/claims.py` (~250 lines)
Extract routes at lines 1679-1900:
- `/claims/new` → `new_claim()` (1679)
- `/claims/<id>/delete` → `delete_claim()` (1732)
- `/claims/<id>/nfip-fill` → `nfip_quick_fill()` (1758)
- `/claims/<id>/notes` → `update_claim_notes()` (1796)
- `/claims/<id>` → `claim_detail()` (1813)
- `/claims/<id>/mobile` → `claim_detail_mobile()` (1853)
- `/claims/<id>/status` → `update_status()` (1886)
- `/claims/<id>/duplicate` (5503)
- `/claims/<id>/activity` (5572)
- `/claims/<id>/sms` (5630)
- `/claims/<id>/mobile-upload` (5655)
- `/claims/<id>/qr` (5703)
- `/claims/bulk` (5730)
- `/claims/<id>/submit` (6439)
- `/claims/<id>/submit/download` (6502)
- `/claims` (6693)
- `/claim/<id>/room/<id>/batch-analyze` (6831)
- `/claim/<id>/ai-populate` (6930)

### `routes/rooms.py` (~40 lines)
Extract routes at lines 1901-1974:
- `/claims/<id>/room/add` → `add_room()` (1901)
- `/rooms/<id>/delete` → `delete_room()` (1917)
- `/rooms/<id>/item/add` → `add_item()` (1935)
- `/items/<id>/delete` → `delete_item()` (1957)

### `routes/photos.py` (~130 lines)
Extract routes at lines 2051-2183:
- `/claims/<id>/photo/upload` → `upload_photo()` (2051)
- `/uploads/<filename>` → `uploaded_file()` (2113)
- `/photos/<id>/delete` → `delete_photo()` (2118)
- `/photos/<id>/ai-description` → `edit_ai_description()` (2138)
- `/photos/<id>/analyze` → `analyze_photo_route()` (2150)
- `/photos/<id>/edit` → `edit_photo()` (2167)

### `routes/reports.py` (~80 lines)
Extract routes at lines 1214-1289, 2184-2214:
- `/claims/<id>/report/pdf` → `report_pdf()` (1214)
- `/claims/<id>/export/xactimate` → `export_xactimate()` (1243)
- `/claims/<id>/report` → `report()` (2184)
- `/claims/<id>/fema-lookup` → `fema_lookup()` (1290)
- `/claims/<id>/portal/generate` → `generate_portal_link()` (1308)
- `/portal/<token>` → `client_portal()` (1330)
- `/claims/<id>/sign` → `sign_claim()` (1350)
- `/claims/<id>/signature` → `get_signature()` (1365)

### `routes/billing.py` (~100 lines)
Extract routes at lines 1388-1475:
- `/billing` → `billing()` (1388)
- `/billing/checkout` → `billing_checkout()` (1396)
- `/billing/success` → `billing_success()` (1427)
- `/billing/portal` → `billing_portal()` (1452)

### `routes/admin.py` (~600 lines)
Extract routes at lines 2215-2862, 3328-3521, 4478-4572, 4922-5070, 5788-5840, 6093-6438, 6662-6670:
- `/admin/settings` → `admin_settings()` (2215)
- `/admin/api/free-models` → `admin_api_free_models()` (2256)
- `/admin/api/init-brain` → `admin_api_init_brain()` (2313)
- `/admin/api/test-photo-analysis` (2681)
- `/admin/team` → `admin_team()` (2744)
- `/admin/team/add` → `admin_team_add()` (2760)
- `/admin/team/<id>/edit` (2788)
- `/admin/team/<id>/delete` (2826)
- `/admin/team/<id>/deactivate` (2849)
- `/admin/team/<id>/reactivate` (2862)
- `/admin/recruit` → `admin_recruit()` (3328)
- `/admin/recruit/adjuster/<id>/approve` (3376)
- `/admin/recruit/contractor/<id>` (3406)
- `/admin/recruit/send-invite` (3450)
- `/admin/willie/brain` (4478)
- `/admin/willie/brain/update` (4492)
- `/admin/settings/data` (4511)
- `/admin/settings/save` (4521)
- `/admin/settings/chat-bubble` (4537)
- `/admin/feedback` (4922)
- `/admin/feedback/conversations/list` (4934)
- `/admin/feedback/conversations` (4943)
- `/admin/feedback/conversations/<id>` (4956)
- `/admin/feedback/conversations/<id>` DELETE (4969)
- `/admin/feedback/conversations/<id>/meta` (4978)
- `/admin/feedback/conversations/<id>/messages` (5001)
- `/admin/feedback/chat` (5021)
- `/admin/feedback/report/<id>` (5071)
- `/admin/weekly-report` (5788)
- `/claims/<id>/compliance` (6093)
- `/sales` (6662)

### `routes/willie.py` (~500 lines)
Extract routes at lines 3522-4477, 4573-4842, 5152-5348:
- `/willie` → `willie()` (3522)
- `/willie/conversations` POST (3531)
- `/willie/conversations/<id>` GET (3539)
- `/willie/conversations/<id>` DELETE (3551)
- `/willie/conversations/<id>/messages` (3560)
- `/willie/chat` (3585)
- `/api/analyze-photo` (3831)
- `/willie/token` (3869)
- `/willie/api/claims` GET (3879)
- `/willie/api/claims/lookup` (3893)
- `/willie/api/claims/<id>/estimate` (3921)
- `/willie/api/claims` POST (4132)
- `/willie/api/claims/<id>` GET (4170)
- `/willie/api/claims/by-number/<num>` DELETE (4188)
- `/willie/api/claims/<id>` DELETE (4207)
- `/willie/api/claims/<id>/status` (4221)
- `/willie/api/claims/<id>/rooms` POST (4239)
- `/willie/api/claims/<id>/rooms/<id>/items` (4256)
- `/willie/api/team` GET (4276)
- `/willie/api/team` POST (4288)
- `/willie/api/dashboard` (4326)
- `/willie/api/claims/<id>/rooms` GET (4346)
- `/willie/api/claims/<id>/rooms/<id>` DELETE (4360)
- `/willie/api/line-items/<id>` DELETE (4371)
- `/willie/api/team/<id>` PUT/PATCH (4382)
- `/willie/api/team/<id>` DELETE (4423)
- `/willie/api/claims/<id>/report` (4434)
- `/willie/api/settings` GET (4451)
- `/willie/api/settings` POST (4459)
- `/willie/api/actions/sync` (4573)
- `/willie/api/claims/<id>/update` POST (4840)
- `/willie/api/claims/<id>` PATCH (4841)
- `/willie/api/claims/<id>/schedule` (5152)
- `/willie/api/claims/<id>/compliance` (5178)
- `/willie/api/claims/<id>/fema-lookup` (5225)
- `/willie/api/claims/<id>/notify` (5247)
- `/willie/api/claims/<id>/move-pipeline` (5278)
- `/willie/api/analytics` (5300)
- `/willie/api/schedule` (5330)
- `/willie/api/claims/<id>/analyze` (5348)

### `routes/feedback.py` (~60 lines)
Extract routes at lines 1975-2050:
- `/api/health/feedback-tables` (1975)
- `/api/dashboard/feedback` (1983)
- `/admin/feedback/conversations/<id>/read` (2007)
- `/admin/feedback/clients` (2018)
- `/feedback/<token>` (2037)

### `routes/training.py` (~80 lines)
Extract routes at lines 3172-3327, 7112-7328:
- `/become-an-agent` (3172)
- `/training/<slug>` (3182)
- `/practice-exam` (3195)
- `/practice-exam/<token>` (3217)
- `/apply-adjuster` (3296)
- `/training` (7112)
- `/training/<id>/enroll` (7131)
- `/training/<id>/learn` (7156)
- `/training/<id>/lesson/<id>/complete` (7179)
- `/training/<id>/exam` (7202)
- `/training/<id>/exam/submit` (7225)
- `/training/certificate/<id>` (7257)
- `/admin/training` (7277)
- `/admin/training/new` (7288)
- `/admin/training/<id>/lessons` (7307)
- `/admin/training/<id>/lesson/<id>/delete` (7328)

### `routes/pipeline.py` (~50 lines)
Extract routes at lines 5841-5895:
- `/pipeline` (5841)
- `/pipeline/move` (5863)

### `routes/schedule.py` (~60 lines)
Extract routes at lines 5896-6008:
- `/schedule` (5896)
- `/schedule/add` (5935)
- `/schedule/<id>/status` (5971)
- `/schedule/<id>/delete` (5981)
- `/notifications` (6009)
- `/notifications/send` (6031)

### `routes/analytics.py` (~300 lines)
Extract routes at lines 6134-6438:
- `/analytics` (6134)

### `routes/customer.py` (~80 lines)
Extract routes at lines 6981-7111:
- `/customer/upload/<token>` GET (6981)
- `/customer/upload/<token>` POST (6996)
- `/claim/<id>/generate-upload-link` (7046)

### `routes/api.py` (~30 lines)
Extract routes at lines 6668-6692:
- `/health` (6668)
- `/ready` (6672)
- `/api/status` (6683)

---

## 🚀 Execution Plan

### Phase 1: Extract utilities (SAFE — no route changes)
1. Create `utils/` package
2. Move security, auth decorators, helpers
3. Update imports in app.py
4. Test: verify all routes still work

### Phase 2: Extract services (SAFE — no route changes)
1. Create `services/` package
2. Move AI, email, FEMA, claims services
3. Update imports
4. Test: verify AI features work

### Phase 3: Extract models (RISKY — DB layer)
1. Create `models/` package
2. Move DB init, migrations, password hashing
3. Update imports
4. Test: verify DB operations

### Phase 4: Extract routes (RISKY — URL changes)
1. Create `routes/` package with blueprints
2. Move route groups one at a time
3. Register blueprints in app.py
4. Test after EACH route group

### Phase 5: Cleanup
1. Remove dead code from app.py
2. Verify app.py < 100 lines
3. Full regression test
4. Push to GitHub (after Mingo review)

---

## ⚠️ Risk Mitigation

1. **DO NOT push without Mingo review** — Jay's rule
2. **Test after EVERY phase** — don't batch changes
3. **Keep the monolith working** — if refactor breaks anything, revert
4. **Billy's app is priority** — if refactor causes downtime, stop immediately
5. **Git branch** — do all work on `refactor/modular` branch, never `main`

---

## 📋 Pre-Flight Checklist

- [ ] Create `refactor/modular` branch
- [ ] Verify current app is healthy (200 on /health)
- [ ] Backup current app.py: `cp app.py app.py.bak`
- [ ] Set up test script to verify all 130 routes
- [ ] Get Mingo's approval before starting
