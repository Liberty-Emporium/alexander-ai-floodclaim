# 🤝 OWL + Self — Shared Coordination

## Active Tasks

### 🔴 URGENT: Security Fixes
- [ ] C1: Remove hardcoded API keys from scripts/ (willie_test.py, browser_test_suite.py)
- [ ] C2: Remove hardcoded admin1234 fallback — require env var
- [ ] C3: Remove demo credentials from login.html
- [ ] C4: Fix CSP headers (remove unsafe-inline/unsafe-eval from script-src)
- [ ] C5: Add HSTS header
- [ ] C6: Set SESSION_COOKIE_SECURE = True

### 🟠 HIGH: Auth Hardening
- [ ] H1: Add rate limiting on login endpoint
- [ ] H2: Enforce password policy (min 8 chars, confirmation)
- [ ] H3: Protect /api/status endpoint

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
