"""FloodClaims Pro — Service layer.

Extracted from app.py Phase 2.
Contains: AI calls, email, FEMA, claims, Willie API.
"""

from services.ai import call_openrouter, ai_describe_photo, ai_describe_photo_detailed
from services.email import send_email, notify_client_status_change
from services.fema import lookup_fema_flood_zone
from services.claims import gen_claim_number, recalc_claim
from services.willie import get_willie_token, willie_auth
