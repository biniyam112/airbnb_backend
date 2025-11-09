import os, sys
from datetime import datetime, timedelta
from dotenv import load_dotenv

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, '..'))
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from config.db import get_db
from ai_agents.booking_agent import BookingAgent
from bson.objectid import ObjectId

load_dotenv()

def seed_property(db):
    prop = {
        "host": ObjectId(),
        "title": "Test Loft",
        "description": "Spacious test loft",
        "location": {"address": "1 Unit Test Way", "city": "New York", "country": "USA", "coordinates": {"lat": 40.7, "lng": -74.0}},
        "rooms": [{"type": "bedroom", "count": 1, "details": {"bedType": "queen"}}],
        "amenities": ["wifi"],
        "pricePerNight": 150,
        "dynamicPrice": None,
        "isAvailable": True,
        "createdAt": datetime.utcnow(),
    }
    result = db.property.insert_one(prop)
    return str(result.inserted_id)

def test_quote_and_confirm():
    db = get_db()
    agent = BookingAgent()
    property_id = seed_property(db)
    today = datetime.utcnow().date()
    start = today + timedelta(days=2)
    end = start + timedelta(days=3)
    quote = agent.create_quote(property_id, None, start.isoformat(), end.isoformat())
    assert quote.get("success"), f"Quote failed: {quote}"
    booking_id = quote.get("booking_id")
    assert booking_id, "Missing booking_id"
    # Chat step
    chat = agent.chat(booking_id, "Can I get a late checkout?")
    assert chat.get("success"), f"Chat failed: {chat}"
    # Confirm
    confirm = agent.confirm(booking_id)
    assert confirm.get("success"), f"Confirm failed: {confirm}"
    # Overlap rejection
    overlap = agent.create_quote(property_id, None, start.isoformat(), end.isoformat())
    assert not overlap.get("success"), "Overlap should have failed"

if __name__ == "__main__":
    test_quote_and_confirm()
    print("Booking agent basic test passed")
