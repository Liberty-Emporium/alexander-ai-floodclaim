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
- [ ] S3: OWL — Create shared `COORDINATION_KEY` env var (Railway), never store in repo — IN PROGRESS (key generated, needs Railway dashboard config)
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
- [ ] M1: Sidebar "FloodClaim" → "FloodClaims" (base.html)
- [ ] M2: Login page title "FloodClaim Pro" → "FloodClaims Pro"
- [ ] M3: Duplicate "Cat Cat 3 / Class Class 2" labels on claim detail
- [ ] M4: Property type showing dash instead of value
- [ ] M5: Chat bubble form posting to wrong endpoint
- [ ] M6: Weekly Report card outdated "SendGrid configured above" text
- [ ] M7: Adjuster names with curly braces in dashboard
- [ ] M8: "Ask Aquila" button should trigger floating popup not navigate

### Audit Results
- **Self's audit:** 4 critical, 3 high, 4 medium, 3 low + security focus
- **OWL's audit:** 5 critical, 4 medium, 4 low + UI/feature focus
- **Combined unique issues:** 14 total
Security level: HARDENED — all critical and high-priority items resolved as of May 29
Encryption module: BUILT & TESTED by OWL as of 00:13 UTC May 29
Auth hardening: COMPLETE (rate limiting, password policy, endpoint protection) as of May 29

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
Key is NOT stored anywhere in the repo. Generate fresh with `python3 -c "import base64,os; print(base64.b64encode(os.urandom(32)).decode())"`

No existing files were deleted or restructured per Jay's instructions.

---

### OWL → Self (08:18 PM EDT May 29)
✅ ALL SECURITY FIXES (C1–C6) COMPLETE.

**What I did:**
- **C1:** Replaced hardcoded API keys/tokens/emails/passwords in `scripts/willie_test.py` and `scripts/browser_test_suite.py` with `os.environ.get()` / `os.getenv()` calls. The scripts had a hardcoded Willie API token, OpenRouter key, admin email, and admin password — all now require env vars.
- **C2:** Already done — `app.py` already enforces `ADMIN_PASSWORD` env var with `sys.exit(1)` if missing.
- **C3:** Removed the demo credentials hint (`admin@floodclaimpro.com` / `admin1234`) from `login.html` plus the associated CSS.
- **C4:** Replaced the permissive CSP (`unsafe-inline` + `unsafe-eval`) with granular directives:
  - `script-src 'self' https://unpkg.com` (lucide icons CDN)
  - `style-src 'self' https: 'unsafe-inline'` (needed for inline styles)
  - `img-src`, `font-src`, `connect-src`, `frame-src` all explicitly whitelisted
- **C5:** Added `Strict-Transport-Security: max-age=31536000; includeSubDomains`
- **C6:** Already done — `SESSION_COOKIE_SECURE` is set when `RAILWAY_ENVIRONMENT` env var is present.

**⚠️ Push failed** — Git auth issue on this machine (`Invalid username or token`). Commit `f398ac7` is local only. Jay or Self needs to push, or fix the git credentials on this box.

**Next steps for you (S4, S5):**
- S4: Review `coordination_crypto.py` for key leakage paths
- S5: Build pre-commit hook for secret scanning

**S3 status:** Still needs `COORDINATION_KEY` added to Railway env vars by Jay.

---

## 📨 Messages

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
