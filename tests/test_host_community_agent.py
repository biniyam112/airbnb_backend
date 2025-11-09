import os, sys
from datetime import datetime, timedelta
from bson.objectid import ObjectId

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, '..'))
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from config.db import get_db
from ai_agents.host_community_agent import HostCommunityAgent


def seed_host_with_properties(db, property_count: int = 2):
    host_id = ObjectId()
    property_ids = []
    for i in range(property_count):
        prop = {
            "host": host_id,
            "title": f"Sample Property {i+1}",
            "description": "Test listing",
            "location": {"address": "123 Test St", "city": "Austin", "country": "USA", "coordinates": {"lat": 30.26, "lng": -97.74}},
            "rooms": [{"type": "bedroom", "count": 1, "details": {"bedType": "queen"}}],
            "amenities": ["wifi", "ac", "parking"] if i == 0 else ["wifi", "kitchen"],
            "pricePerNight": 120 + (i * 10),
            "dynamicPrice": None,
            "isAvailable": True,
            "createdAt": datetime.utcnow(),
        }
        result = db.property.insert_one(prop)
        property_ids.append(result.inserted_id)

    # Seed a confirmed booking for first property
    start = datetime.utcnow() - timedelta(days=10)
    end = start + timedelta(days=3)
    db.booking.insert_one({
        "property": property_ids[0],
        "guest": ObjectId(),
        "startDate": start,
        "endDate": end,
        "nights": (end - start).days,
        "nightlyPrice": 130,
        "totalPrice": 130 * (end - start).days,
        "pricingSource": "test",
        "status": "confirmed",
        "createdAt": datetime.utcnow(),
        "updatedAt": datetime.utcnow(),
    })

    return str(host_id)


def test_host_advice():
    db = get_db()
    host_id = seed_host_with_properties(db)
    agent = HostCommunityAgent()
    advice = agent.get_host_advice(host_id)
    assert advice.get("success"), f"Advice failed: {advice}"
    data = advice.get("data", {})
    assert "recommendations" in data, "Missing recommendations list"
    assert len(data.get("recommendations", [])) > 0, "Expected at least one recommendation"


if __name__ == "__main__":
    test_host_advice()
    print("HostCommunityAgent basic test passed")
