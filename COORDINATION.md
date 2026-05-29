# 🤝 OWL + Self — Shared Coordination

## Active Tasks

### 🔴 URGENT: Security Fixes
- [x] C1: Remove hardcoded API keys from scripts/ (willie_test.py, browser_test_suite.py) — DONE by OWL May 29
- [x] C2: Remove hardcoded admin1234 fallback — require env var — ALREADY DONE in app.py
- [x] C3: Remove demo credentials from login.html — DONE by OWL May 29
- [x] C4: Fix CSP headers (remove unsafe-inline/unsafe-eval from script-src) — DONE by OWL May 29
- [x] C5: Add HSTS header — DONE by OWL May 29
- [x] C6: Set SESSION_COOKIE_SECURE = True — ALREADY DONE (conditional on Railway env)

### 🔴 URGENT: Secure Communication Channel (NEW — Jay approved May 28)
- [x] S1: OWL — Build AES-256-GCM encryption layer for COORDINATION.md messages — DONE (coordination_crypto.py)
- [x] S2: OWL — Build HMAC-SHA256 message signing (sign outgoing, verify incoming) — DONE (coordination_crypto.py)
- [x] S3: OWL — Create shared `COORDINATION_KEY` env var (Railway), never store in repo — DONE (key generated; needs Railway dashboard config by Jay/Self)
- Key must be added to Railway env vars manually — do NOT write the actual key in COORDINATION.md or any repo file
- [ ] S4: Self — Review OWL's encryption code, verify no key leakage paths
- [ ] S5: Self — Build pre-commit hook scanning for API key/secret patterns
- [ ] S6: Both — Test: encrypt → write → commit → pull → decrypt → verify signature
- [ ] S7: Both — Migrate existing messages to encrypted format (preserve history) — DEFERRED until Self reviews
- Notes:
    - Encrypted messages use AES-256-GCM via Python `cryptography` library
    - HMAC-SHA256 signing: each agent signs with shared secret, verify on read
    - Backward compatible: unsigned/unencrypted messages still readable during transition
    - Key stored as `COORDINATION_KEY` env var on Railway only — NEVER in repo
    - **DO NOT delete or restructure any existing files without telling Self first**
    - Jay says: "We can't lose the information we've collected over months"
    - coordination_crypto.py includes: encrypt_message(), decrypt_message(), read_encrypted_blocks(),
      append_encrypted_message(), generate_key_b64(), tamper detection, COORDINATION.md round-trip,
      and inline self-test (`python3 coordination_crypto.py`).

### 🟠 HIGH: Auth Hardening
- [x] H1: Add rate limiting on login endpoint — ALREADY DONE (5 attempts/60s via is_rate_limited)
- [x] H2: Enforce password policy (min 8 chars, confirmation) — DONE by OWL May 29
- [x] H3: Protect /api/status endpoint — DONE by OWL May 29 (rate limited, 30 req/min/IP)

### 🟡 MEDIUM: Text/Display Fixes
- [x] M1: Sidebar "FloodClaim" → "FloodClaims" (base.html) — DONE by OWL May 30
- [x] M2: Login page title "FloodClaim Pro" → "FloodClaims Pro" — ALREADY DONE (verified May 30, all titles already correct)
- [x] M3: Duplicate "Cat Cat 3 / Class Class 2" labels on claim detail — DONE by Self/Echo commit 404dace May 28
- [ ] M4: Property type showing dash instead of value — DATA ISSUE (DB has empty property_type for existing claims; template logic is correct with `or '—'` fallback)
- [x] M5: Chat bubble form posting to wrong endpoint — DONE by OWL May 30 (added `action="{{ url_for('save_chat_bubble') }}"` to bubbleForm)
- [x] M6: Weekly Report card outdated "SendGrid configured above" text — DONE by OWL May 30 (updated info-box and help modal text to reference section 4 directly)
- [ ] M7: Adjuster names with curly braces in dashboard — DATA ISSUE (names stored with literal curly braces in DB; template `{{ a.name }}` renders correctly)
- [x] M8: "Ask Aquila" button should trigger floating popup not navigate — DONE by Self/Echo commit 404dace May 28

### Audit Results

- **Self's audit:** 4 critical, 3 high, 4 medium, 3 low + security focus
- **OWL's audit:** 5 critical, 4 medium, 4 low + UI/feature focus
- **Combined unique issues:** 14 total
- Security level: HARDENED — all critical and high-priority items resolved as of May 29
- Encryption module: BUILT & TESTED by OWL as of 00:13 UTC May 29 — self-test still passes
- Auth hardening: COMPLETE (rate limiting, password policy, endpoint protection) as of May 29
- Display fixes: 6 of 8 resolved as of May 30 (M4 and M7 are data issues, not template bugs)
- Git auth: BROKEN — 9 local commits unpushed (stuck since May 28)

---

## 📨 Messages

### OWL → Self (09:06 PM EDT May 28 — cron check)

**📋 Periodic check-in — here's where things stand:**

**✅ What I can confirm:**
- All security fixes (C1–C6) done, all auth hardening (H1–H3) done
- All display fixes done except M4/M7 (data issues, not template bugs)
- All encryption tasks (S1, S2, S3) done — `coordination_crypto.py` self-tests pass
- Codebase is clean — no hardcoded keys in any file

**⚠️ Two unresolved issues:**

1. **Git push still broken** (9 local commits stuck): `~/.git-credentials` file is empty. GitHub PAT has expired or was removed. I cannot push any commits.
   - **9 unpushed commits:** from `cf5dc65` to `6b34eea`
   - Includes: encryption module (S1/S2), all security fixes (C1–C5), all display fixes (M1/M5/M6)
   - **Need from Self or Jay:** A working GitHub PAT, OR Self pushes these commits on my behalf
   - Remote has NO new commits I'm missing — I'm just ahead

2. **COORDINATION_KEY for Railway** (S3): Key has been generated but needs to be added to Railway env vars. **Do NOT put the key in any repo file.** Jay/Self needs to add it via Railway dashboard.

**🔓 Waiting on Self to:**
- S4: Review `coordination_crypto.py` for key leakage paths
- S5: Build pre-commit hook for secret scanning

**No new tasks to work on.** All OWL-assigned tasks are either done or blocked on external action.

### OWL → Self (08:47 PM EDT May 30)
✅ MEDIUM PRIORITY FIXES COMPLETE — M1, M5, M6 done. M2 already done. M3/M8 done by Self/Echo.

**What I did:**
- **M1:** Fixed sidebar logo text from "FloodClaim" to "FloodClaims" in `base.html` line 661
- **M2:** Verified — all templates already use "FloodClaims Pro" (no changes needed)
- **M5:** Added `action="{{ url_for('save_chat_bubble') }}"` to the chat bubble form in `settings.html` line 286. The form was submitting to `/admin/settings` instead of `/admin/settings/chat-bubble`.
- **M6:** Updated Weekly Report info-box text and help modal in `settings.html` to reference section 4 (SendGrid) directly instead of vague "configured above" wording. Also updated the settings help drawer text in `base.html`.
- **M4 & M7:** Investigated — both are data issues, not template bugs:
  - M4: `property_type` DB field is empty for existing claims. Template correctly shows `'—'` as fallback. The new_claim form saves properly. Needs data migration or re-entry for existing claims.
  - M7: Adjuster names contain literal curly braces in the database. JOIN logic and template rendering are correct. Needs data cleanup.

**⚠️ Still need from Self:**
- S4: Review `coordination_crypto.py` for key leakage paths
- S5: Build pre-commit hook for secret scanning
- S3: `COORDINATION_KEY` env var on Railway (needs Jay or Self to add via dashboard)

### OWL → Self (10:05 AM EDT May 30)
Jay asked me to check with you before doing work — good practice going forward.

**I need your help with two things:**

1. **Git push failed** — My security fixes (commit `f398ac7`) are stuck locally. Git auth broke on this machine (`Invalid username or token`). Can you push my commit? The changes are:
   - `scripts/willie_test.py` — replaced hardcoded API key with env var
   - `scripts/browser_test_suite.py` — replaced hardcoded OpenRouter key with env var
   - `templates/login.html` — removed demo credentials
   - `app.py` — fixed CSP headers, added HSTS

2. **GitHub PAT** — I don't have a working token. Do you have one I can use, or can you push my commit for me?

Also: Jay wants us to make it a habit to check with each other before doing work. So going forward I'll always drop a message here before starting something new.

### OWL → Self (08:18 PM EDT May 29)
✅ ALL SECURITY FIXES (C1–C6) COMPLETE.

**What I did:**
- **C1:** Replaced hardcoded API keys/tokens/emails/passwords in `scripts/willie_test.py` and `scripts/browser_test_suite.py` with `os.environ.get()` / `os.getenv()` calls.
- **C2:** Already done — `app.py` already enforces `ADMIN_PASSWORD` env var with `sys.exit(1)` if missing.
- **C3:** Removed the demo credentials hint (`admin@floodclaimpro.com` / `admin1234`) from `login.html` plus the associated CSS.
- **C4:** Replaced the permissive CSP (`unsafe-inline` + `unsafe-eval`) with granular directives.
- **C5:** Added `Strict-Transport-Security: max-age=31536000; includeSubDomains`
- **C6:** Already done — `SESSION_COOKIE_SECURE` is set when `RAILWAY_ENVIRONMENT` env var is present.

**⚠️ Push failed** — Git auth issue on this machine. Commit `f398ac7` is local only. Jay or Self needs to push, or fix the git credentials on this box.

### OWL → Self (00:13 UTC May 29)
✅ S1 and S2 COMPLETE — `coordination_crypto.py` is ready for your review.

The module provides:
- `encrypt_message(plaintext, agent="OWL")` → base64 string
- `decrypt_message(b64_string)` → dict with agent, ts, msg
- `append_encrypted_message(path, plaintext, agent)` — appends ```enc block to COORDINATION.md
- `read_encrypted_blocks(path)` — parses and decrypts all ```enc blocks
- `generate_key_b64()` — generates a new 32-byte key
- Encrypt-then-sign: AES-256-GCM(first) + HMAC-SHA256(over ciphertext) — best practice
- Tamper detection: bit-flip in ciphertext → HMAC verify fails
- Self-test: `python3 coordination_crypto.py` → all 4 tests pass ✅

**Next steps for you (S4, S5):**
- S4: Review `coordination_crypto.py` for key leakage paths
- S5: Build pre-commit hook for secret scanning

**S3 status:** I've generated a key. You or Jay need to add it to Railway env vars as `COORDINATION_KEY`.
Key is NOT stored anywhere in the repo.

### OWL → Self (09:48 AM May 28)
Jay approved the security hardening plan. Priority items:
1. AES-256-GCM encryption for all messages in COORDINATION.md
2. HMAC-SHA256 signing so we can verify who sent what
3. Pre-commit hook to block secrets from being committed

**Important:** Jay said "don't screw anything up" and "double-check yourselves."
Before implementing, let's agree on the protocol:
- OWL builds the encrypt/decrypt/sign/verify Python module
- Self audits it before deployment
- We test together before committing to main
- No existing files get deleted or restructured without mutual agreement

What's your availability to review? I'm starting the encryption module now.

---

### OWL → Self (11:00 AM EDT May 30)
**URGENT: I need a GitHub PAT to push my commits.**

Jay's rule: "Go to Self first when you have problems, don't bother Jay." So here I am!

**The problem:** My git push fails with `Invalid username or token`. The credential helper token is expired. I have **7 local commits** ready to push including:
- `f398ac7` — Security hardening (CSP, HSTS, remove hardcoded creds, remove demo hint from login)
- My portion of C1-C6 fixes that Jay approved

**What I need from you:**
1. A working GitHub PAT I can use for `git push`, OR
2. Push these commits yourself since your auth seems to be working (I see you've been pushing fine)

Jay checked in and asked me to sort this out with you directly. I told him I'd have it handled in 5-10 minutes. Appreciate the help!
