"""
Lucy Caller ID Lookup Middleware
================================
Handles ElevenLabs "Conversation Initiation Client Data Webhook"
Looks up the caller in GHL by phone number and returns dynamic variables
for Lucy's prompt: caller_history and greeting.

Expected inbound payload from ElevenLabs:
{
  "caller_id": "+447712345678",
  "called_number": "+447888871838",
  "call_sid": "...",
  ...
}

Expected response to ElevenLabs:
{
  "dynamic_variables": {
    "caller_history": "...",
    "greeting": "..."
  }
}

Keep-alive note:
  The self-ping background thread has been intentionally removed.
  Background threads inside Gunicorn worker processes accumulate on every
  restart/redeploy and are never cleaned up, causing the thread count to
  grow unboundedly. Keep-alive is handled externally via a Render Cron Job
  (see render.yaml) which pings /health every 14 minutes from outside the
  app process. This is the correct architecture.
"""

import os
import re
import random
import logging
import requests
from flask import Flask, request, jsonify
from datetime import datetime
import pytz

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Config (set as environment variables in production) ──────────────────────
GHL_API_KEY     = os.environ.get("GHL_API_KEY", "")
GHL_LOCATION_ID = os.environ.get("GHL_LOCATION_ID", "YOHjoiCFRkHFJV8uA3tl")
GHL_BASE_URL    = "https://services.leadconnectorhq.com"
GHL_HEADERS     = {
    "Authorization": f"Bearer {GHL_API_KEY}",
    "Content-Type": "application/json",
    "Version": "2021-07-28"
}

# ── Health check ─────────────────────────────────────────────────────────────
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "lucy-caller-lookup"}), 200


# ── Main caller lookup endpoint ───────────────────────────────────────────────
@app.route("/caller-lookup", methods=["POST"])
def caller_lookup():
    """
    Called by ElevenLabs before each call starts.
    Returns dynamic variables: caller_history + greeting.
    """
    data = request.get_json(silent=True) or {}
    logger.info(f"Inbound payload: {data}")

    # ElevenLabs sends caller_id for Twilio calls
    caller_phone = (
        data.get("caller_id") or
        data.get("from") or
        data.get("phone") or
        ""
    ).strip()

    logger.info(f"Caller phone: {caller_phone}")

    # Get current UK date for the greeting context
    uk_tz = pytz.timezone("Europe/London")
    now_uk = datetime.now(uk_tz)
    time_of_day = _time_of_day(now_uk.hour)

    if not caller_phone:
        logger.warning("No caller phone in payload — returning default greeting")
        return _default_response(time_of_day)

    # Normalise phone number for GHL lookup
    normalised = _normalise_phone(caller_phone)
    logger.info(f"Normalised phone: {normalised}")

    # Look up contact in GHL
    contact = _lookup_contact(normalised)

    if not contact:
        logger.info("Contact not found — new caller")
        return _default_response(time_of_day)

    # Build personalised response
    first_name = contact.get("firstName", "") or contact.get("first_name", "")
    last_name  = contact.get("lastName", "")  or contact.get("last_name", "")
    full_name  = f"{first_name} {last_name}".strip() or "there"

    # Pull Lucy custom fields
    custom_fields = {cf.get("id", cf.get("key", "")): cf.get("value", "")
                     for cf in contact.get("customFields", [])}

    # Try to get booking history from tags and custom fields
    tags = contact.get("tags", [])
    booking_count = _estimate_booking_count(tags, custom_fields)
    last_booking  = _get_last_booking(custom_fields)

    # Pull email and phone from contact for pre-emptive data extraction
    contact_email = contact.get("email", "") or ""
    contact_phone = contact.get("phone", "") or normalised

    # Build caller_history string — includes contact details so Lucy never
    # needs to ask for information she already has
    history_parts = []
    if booking_count == 0:
        history_parts.append(f"Contact exists in CRM but no confirmed bookings on record for {full_name}.")
    elif booking_count == 1:
        history_parts.append(f"{full_name} has visited OAO once before.")
        if last_booking:
            history_parts.append(f"Last booking: {last_booking}.")
    else:
        history_parts.append(f"{full_name} is a returning guest with {booking_count} visits on record.")
        if last_booking:
            history_parts.append(f"Most recent booking: {last_booking}.")

    # Append known contact fields so Lucy can skip asking for them
    if contact_phone:
        history_parts.append(f"Phone on file: {contact_phone}.")
    if contact_email:
        history_parts.append(f"Email on file: {contact_email}.")

    caller_history = " ".join(history_parts)

    # Build personalised greeting — rotate variations for returning callers
    if first_name:
        variations = [
            f"Good {time_of_day} {first_name}, lovely to hear from you again — this is Lucy at OAO, how can I help?",
            f"Good {time_of_day} {first_name}! Great to have you call again — it's Lucy at OAO, what can I do for you?",
            f"Hello {first_name}, good {time_of_day}! Always a pleasure — it's Lucy at OAO, how can I help today?",
            f"Good {time_of_day} {first_name}, welcome back! This is Lucy at OAO — what can I help you with?",
            f"Hello {first_name}! Good {time_of_day} — it's Lucy at OAO, lovely to hear from you. How can I help?",
        ]
        greeting = random.choice(variations)
    else:
        greeting = f"Good {time_of_day}, OAO Restaurant — this is Lucy, how can I help?"

    logger.info(f"Returning personalised response for {full_name}: {caller_history[:80]}...")

    # Format current date/time for Lucy's prompt
    current_datetime = _format_datetime(now_uk)

    return jsonify({
        "dynamic_variables": {
            "caller_history": caller_history,
            "greeting": greeting,
            "current_datetime": current_datetime
        }
    }), 200


# ── Helper functions ──────────────────────────────────────────────────────────

def _format_datetime(now_uk) -> str:
    """Format a UK datetime as a human-readable string for Lucy's prompt."""
    day = now_uk.day
    suffix = "th" if 11 <= day <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return now_uk.strftime(f"%A {day}{suffix} %B %Y, %H:%M BST")


def _default_response(time_of_day="day"):
    """Return standard greeting for unknown callers."""
    new_caller_greetings = [
        f"Good {time_of_day}, OAO Restaurant — this is Lucy, how can I help?",
        f"Good {time_of_day}! You're through to OAO Restaurant, this is Lucy — how can I help?",
        f"Good {time_of_day}, thanks for calling OAO — it's Lucy, how can I help you today?",
    ]
    uk_tz = pytz.timezone("Europe/London")
    now_uk = datetime.now(uk_tz)
    current_datetime = _format_datetime(now_uk)

    return jsonify({
        "dynamic_variables": {
            "caller_history": "No previous bookings on record.",
            "greeting": random.choice(new_caller_greetings),
            "current_datetime": current_datetime
        }
    }), 200


def _time_of_day(hour: int) -> str:
    if hour < 12:
        return "morning"
    elif hour < 17:
        return "afternoon"
    else:
        return "evening"


def _normalise_phone(phone: str) -> str:
    """Normalise phone number to E.164 format for GHL lookup."""
    digits = re.sub(r"[^\d+]", "", phone)
    if digits.startswith("07") and not digits.startswith("+"):
        digits = "+44" + digits[1:]
    elif digits.startswith("447"):
        digits = "+" + digits
    elif not digits.startswith("+") and len(digits) >= 10:
        digits = "+44" + digits.lstrip("0")
    return digits


def _lookup_contact(phone: str) -> dict | None:
    """Look up a contact in GHL by phone number."""
    try:
        url = f"{GHL_BASE_URL}/contacts/search/duplicate"
        params = {
            "locationId": GHL_LOCATION_ID,
            "phone": phone
        }
        resp = requests.get(url, headers=GHL_HEADERS, params=params, timeout=3)
        logger.info(f"GHL lookup status: {resp.status_code}")

        if resp.status_code == 200:
            result = resp.json()
            contact = result.get("contact")
            if contact:
                logger.info(f"Contact found: {contact.get('id')} - {contact.get('firstName')}")
                return contact

        # Fallback: search contacts
        url2 = f"{GHL_BASE_URL}/contacts/"
        params2 = {
            "locationId": GHL_LOCATION_ID,
            "query": phone,
            "limit": 1
        }
        resp2 = requests.get(url2, headers=GHL_HEADERS, params=params2, timeout=3)
        if resp2.status_code == 200:
            contacts = resp2.json().get("contacts", [])
            if contacts:
                return contacts[0]

    except requests.exceptions.Timeout:
        logger.warning("GHL lookup timed out — returning default")
    except Exception as e:
        logger.error(f"GHL lookup error: {e}")

    return None


def _estimate_booking_count(tags: list, custom_fields: dict) -> int:
    """Estimate number of visits from tags."""
    booking_tags = [t for t in tags if any(
        kw in t.lower() for kw in ["booking", "confirmed", "visited", "dined"]
    )]
    return len(booking_tags)


def _get_last_booking(custom_fields: dict) -> str:
    """Extract last booking date from custom fields."""
    for key, val in custom_fields.items():
        if val and any(kw in str(key).lower() for kw in ["booking_date", "last_visit", "date"]):
            return str(val)
    return ""


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
