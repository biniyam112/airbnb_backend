from datetime import datetime
from bson.objectid import ObjectId
import os, sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, '..'))
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from config.db import get_db
from ai_agents.host_community_agent import HostCommunityAgent


def seed_demo_host(db):
    host_id = ObjectId()
    p = {
        "host": host_id,
        "title": "Demo Bungalow",
        "description": "Cozy bungalow for demo",
        "location": {"address": "9 Demo Ave", "city": "Denver", "country": "USA", "coordinates": {"lat": 39.7, "lng": -104.9}},
        "rooms": [{"type": "bedroom", "count": 2, "details": {"bedType": "queen"}}],
        "amenities": ["wifi", "kitchen", "parking"],
        "pricePerNight": 140,
        "dynamicPrice": None,
        "isAvailable": True,
        "createdAt": datetime.utcnow(),
    }
    db.property.insert_one(p)
    return str(host_id)


def main():
    db = get_db()
    host_id = os.getenv("DEMO_HOST_ID") or seed_demo_host(db)
    agent = HostCommunityAgent()
    resp = agent.get_host_advice(host_id)
    print(resp)


if __name__ == "__main__":
    main()
