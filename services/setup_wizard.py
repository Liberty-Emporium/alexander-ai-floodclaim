"""
AI-Guided Setup Wizard Service
================================
Aquila walks the user through configuring every integration:
OpenRouter, Twilio, SendGrid, Stripe, Meshy — with real-time
connection testing and conversational guidance.

State machine: each service has steps → Aquila asks → user provides →
Aquila tests → pass/fail → next step or retry.
"""
import os
import json
import logging
import requests as _req

logger = logging.getLogger(__name__)

# ── Service definitions ────────────────────────────────────────────────────────

SETUP_SERVICES = {
    "openrouter": {
        "label": "OpenRouter AI",
        "emoji": "🤖",
        "description": "Powers Aquila chat, photo analysis, and AI estimates",
        "fields": [
            {
                "key": "openrouter_api_key",
                "label": "OpenRouter API Key",
                "placeholder": "sk-or-v1-...",
                "help": "Get your key at openrouter.ai → Settings → API Keys. Free sign-up gives you pay-as-you-go access.",
                "test": "test_openrouter",
                "required": True,
            },
        ],
    },
    "twilio": {
        "label": "Twilio SMS",
        "emoji": "📱",
        "description": "Send SMS notifications to clients when their claim status changes",
        "fields": [
            {
                "key": "twilio_account_sid",
                "label": "Account SID",
                "placeholder": "ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
                "help": "Found on your Twilio dashboard at twilio.com. Starts with 'AC'.",
                "test": None,
                "required": True,
            },
            {
                "key": "twilio_auth_token",
                "label": "Auth Token",
                "placeholder": "Your auth token",
                "help": "Found on the same Twilio dashboard page as your Account SID.",
                "test": None,
                "required": True,
            },
            {
                "key": "twilio_from_number",
                "label": "From Phone Number",
                "placeholder": "+15551234567",
                "help": "Your Twilio phone number in E.164 format (starts with +1 for US).",
                "test": "test_twilio",
                "required": True,
            },
        ],
    },
    "sendgrid": {
        "label": "SendGrid Email",
        "emoji": "📧",
        "description": "Send email notifications, weekly reports, and claim updates",
        "fields": [
            {
                "key": "sendgrid_api_key",
                "label": "SendGrid API Key",
                "placeholder": "SG.xxxxxxxxxxxxxxxx",
                "help": "Create a key at sendgrid.com → Settings → API Keys → Create API Key.",
                "test": None,
                "required": True,
            },
            {
                "key": "from_email",
                "label": "From Email Address",
                "placeholder": "noreply@yourdomain.com",
                "help": "Must be a verified sender in your SendGrid account. Go to SendGrid → Settings → Sender Authentication.",
                "test": "test_sendgrid",
                "required": True,
            },
        ],
    },
    "stripe": {
        "label": "Stripe Billing",
        "emoji": "💳",
        "description": "Accept payments for subscriptions (Basic $49, Pro $149, Agency $249/mo)",
        "fields": [
            {
                "key": "stripe_secret_key",
                "label": "Stripe Secret Key",
                "placeholder": "sk_live_... or sk_test_...",
                "help": "Found at stripe.com → Developers → API Keys. Use test keys for testing.",
                "test": None,
                "required": True,
            },
            {
                "key": "stripe_publishable_key",
                "label": "Stripe Publishable Key",
                "placeholder": "pk_live_... or pk_test_...",
                "help": "The public key from the same Stripe page. Starts with 'pk'.",
                "test": "test_stripe",
                "required": True,
            },
        ],
    },
    "meshy": {
        "label": "Meshy AI (Aquila 3D)",
        "emoji": "🏗️",
        "description": "Generate 3D models from flood damage photos",
        "fields": [
            {
                "key": "meshy_api_key",
                "label": "Meshy API Key",
                "placeholder": "msy-xxxxxxxxxxxxxxxx",
                "help": "Get your key at meshy.ai → Account → API Keys. Free tier available.",
                "test": "test_meshy",
                "required": False,
            },
        ],
    },
}


# ── Connection testers ─────────────────────────────────────────────────────────


def _get_setting(key):
    """Read a setting from the DB or env."""
    import sqlite3
    DB_PATH = os.environ.get('DB_PATH') or '/data/floodclaims.db'
    try:
        db = sqlite3.connect(DB_PATH)
        db.row_factory = sqlite3.Row
        row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
        db.close()
        val = row['value'] if row else ''
    except Exception:
        val = ''
    return val or os.environ.get(key.upper(), '')


def _set_setting(key, value):
    """Write a setting to the DB."""
    import sqlite3
    DB_PATH = os.environ.get('DB_PATH') or '/data/floodclaims.db'
    db = sqlite3.connect(DB_PATH)
    db.execute(
        'INSERT INTO settings (key, value) VALUES (?,?) '
        'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
        (key, value))
    db.commit()
    db.close()


def test_openrouter(api_key):
    """Test OpenRouter API key validity."""
    if not api_key:
        return {"ok": False, "message": "No API key provided."}
    if not api_key.startswith("sk-or-"):
        return {"ok": False, "message": "Key doesn't look right — OpenRouter keys start with 'sk-or-'. Double-check at openrouter.ai → Settings → API Keys."}
    try:
        r = _req.get(
            "https://openrouter.ai/api/v1/auth/key",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json().get("data", {})
            return {
                "ok": True,
                "message": f"✅ OpenRouter connected! Model access confirmed. (Label: {data.get('label', 'default')})",
            }
        elif r.status_code == 401:
            return {"ok": False, "message": "❌ Invalid key. Go to openrouter.ai → Settings → API Keys and copy a fresh key."}
        elif r.status_code == 402:
            return {"ok": False, "message": "❌ Key valid but account has no credits. Add credits at openrouter.ai/credits."}
        else:
            return {"ok": False, "message": f"⚠️ OpenRouter returned status {r.status_code}. Try again in a moment."}
    except _req.exceptions.Timeout:
        return {"ok": False, "message": "⏱️ Connection timed out. Check your internet and try again."}
    except Exception as e:
        return {"ok": False, "message": f"⚠️ Connection error: {e}"}


def test_twilio(account_sid, auth_token, from_number):
    """Test Twilio credentials and phone number."""
    if not account_sid or not auth_token:
        return {"ok": False, "message": "Account SID and Auth Token are required."}
    if not account_sid.startswith("AC"):
        return {"ok": False, "message": "Account SID should start with 'AC'. Check your Twilio dashboard."}
    try:
        r = _req.get(
            f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}.json",
            auth=(account_sid, auth_token),
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            status = data.get("status", "unknown")
            friendly_name = data.get("friendly_name", "your account")
            # Also check the from number exists in incoming phone numbers
            num_r = _req.get(
                f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/IncomingPhoneNumbers.json",
                auth=(account_sid, auth_token),
                timeout=10,
            )
            number_ok = True
            number_msg = ""
            if num_r.status_code == 200:
                numbers = num_r.json().get("incoming_phone_numbers", [])
                phone_numbers = [n["phone_number"] for n in numbers]
                if from_number and from_number not in phone_numbers:
                    number_ok = False
                    number_msg = f" ⚠️ But {from_number} isn't in your Twilio account's phone numbers. Available: {', '.join(phone_numbers[:3]) or 'none'}."

            msg = f"✅ Twilio connected! Account: {friendly_name} (status: {status})"
            if not number_ok:
                msg += number_msg
            return {"ok": True, "message": msg, "warning": not number_ok}
        elif r.status_code == 401:
            return {"ok": False, "message": "❌ Invalid SID or Token. Go to twilio.com → Dashboard → Account Info."}
        else:
            return {"ok": False, "message": f"⚠️ Twilio returned status {r.status_code}: {r.text[:100]}"}
    except _req.exceptions.Timeout:
        return {"ok": False, "message": "⏱️ Connection timed out. Check your internet."}
    except Exception as e:
        return {"ok": False, "message": f"⚠️ Connection error: {e}"}


def test_sendgrid(api_key, from_email):
    """Test SendGrid API key and sender."""
    if not api_key:
        return {"ok": False, "message": "API key is required."}
    if not api_key.startswith("SG."):
        return {"ok": False, "message": "SendGrid keys start with 'SG.'. Check sendgrid.com → Settings → API Keys."}
    try:
        # Test the API key by trying to get account info
        r = _req.get(
            "https://api.sendgrid.com/v3/user/profile",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=10,
        )
        if r.status_code == 200:
            return {
                "ok": True,
                "message": f"✅ SendGrid connected! Account: {r.json().get('username', 'verified')} — from_email set to {from_email}",
            }
        elif r.status_code == 401:
            return {"ok": False, "message": "❌ Invalid API key. Go to sendgrid.com → Settings → API Keys."}
        else:
            # Try sender verification check
            s = _req.get(
                f"https://api.sendgrid.com/v3/senders",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=10,
            )
            if s.status_code == 200:
                return {"ok": True, "message": f"✅ SendGrid API key valid. {from_email} set as sender."}
            return {"ok": False, "message": f"⚠️ SendGrid returned status {r.status_code}."}
    except _req.exceptions.Timeout:
        return {"ok": False, "message": "⏱️ Connection timed out."}
    except Exception as e:
        return {"ok": False, "message": f"⚠️ Connection error: {e}"}


def test_stripe(secret_key):
    """Test Stripe API key."""
    if not secret_key:
        return {"ok": False, "message": "Secret key is required."}
    if not secret_key.startswith(("sk_live_", "sk_test_")):
        return {"ok": False, "message": "Stripe secret keys start with 'sk_live_' or 'sk_test_'. Check stripe.com → Developers → API Keys."}
    mode = "live" if "live" in secret_key else "test"
    try:
        r = _req.get(
            "https://api.stripe.com/v1/account",
            headers={"Authorization": f"Bearer {secret_key}"},
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            name = data.get("business_name") or data.get("display_name") or "connected"
            return {
                "ok": True,
                "message": f"✅ Stripe connected ({mode} mode)! Account: {name}",
            }
        elif r.status_code == 401:
            return {"ok": False, "message": "❌ Invalid Stripe key. Copy the correct key from stripe.com → Developers → API Keys."}
        else:
            return {"ok": False, "message": f"⚠️ Stripe returned status {r.status_code}."}
    except _req.exceptions.Timeout:
        return {"ok": False, "message": "⏱️ Connection timed out."}
    except Exception as e:
        return {"ok": False, "message": f"⚠️ Connection error: {e}"}


def test_meshy(api_key):
    """Test Meshy API key."""
    if not api_key:
        return {"ok": False, "message": "API key is required."}
    try:
        r = _req.get(
            "https://api.meshy.ai/v2/user/credits",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            credits = data.get("credits", "?")
            return {
                "ok": True,
                "message": f"✅ Meshy AI connected! Credits: {credits}",
            }
        elif r.status_code == 401:
            return {"ok": False, "message": "❌ Invalid Meshy key. Get yours at meshy.ai → Account."}
        else:
            return {"ok": False, "message": f"⚠️ Meshy returned status {r.status_code}."}
    except _req.exceptions.Timeout:
        return {"ok": False, "message": "⏱️ Connection timed out."}
    except Exception as e:
        return {"ok": False, "message": f"⚠️ Connection error: {e}"}


# ── Test dispatch ──────────────────────────────────────────────────────────────

TEST_FUNCTIONS = {
    "test_openrouter": lambda vals: test_openrouter(vals.get("openrouter_api_key", "")),
    "test_twilio": lambda vals: test_twilio(
        vals.get("twilio_account_sid", ""),
        vals.get("twilio_auth_token", ""),
        vals.get("twilio_from_number", ""),
    ),
    "test_sendgrid": lambda vals: test_sendgrid(
        vals.get("sendgrid_api_key", ""),
        vals.get("from_email", ""),
    ),
    "test_stripe": lambda vals: test_stripe(vals.get("stripe_secret_key", "")),
    "test_meshy": lambda vals: test_meshy(vals.get("meshy_api_key", "")),
}


def run_test(service_id, values):
    """Run the connection test for a service. Returns {ok, message}."""
    test_fn_name = None
    svc = SETUP_SERVICES.get(service_id)
    if svc:
        for field in svc["fields"]:
            if field.get("test"):
                test_fn_name = field["test"]
                break

    if test_fn_name and test_fn_name in TEST_FUNCTIONS:
        try:
            return TEST_FUNCTIONS[test_fn_name](values)
        except Exception as e:
            return {"ok": False, "message": f"⚠️ Test error: {e}"}
    return {"ok": True, "message": "Saved (no connection test available for this service)."}


def get_setup_status():
    """Check which services are fully configured. Returns dict of service_id → {configured, missing}."""
    status = {}
    for svc_id, svc in SETUP_SERVICES.items():
        missing = []
        configured = []
        for field in svc["fields"]:
            val = _get_setting(field["key"])
            if field["required"] and not val:
                missing.append(field["key"])
            elif val:
                configured.append(field["key"])
        status[svc_id] = {
            "configured": len(missing) == 0,
            "partial": len(configured) > 0 and len(missing) > 0,
            "missing": missing,
            "configured_fields": configured,
        }
    return status


def get_completion_pct():
    """Return setup completion percentage (0-100)."""
    status = get_setup_status()
    total = len(status)
    done = sum(1 for s in status.values() if s["configured"])
    return int((done / total) * 100) if total > 0 else 0
