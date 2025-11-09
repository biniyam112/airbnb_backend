"""Insert a new property document with 5 unique random images and realistic defaults.

Run from project root:
    python backend/scripts/add_property.py --title "Cozy Loft in Downtown" --city Austin --country USA --price 189

If --host-id is not provided, a random existing user with role=host will be used (if available), 
otherwise a placeholder host will be created on the fly.
"""
from __future__ import annotations
import os
import sys
import random
import argparse
from datetime import datetime, timedelta

SCRIPT_DIR = os.path.dirname(__file__)
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, '..'))
sys.path.insert(0, PROJECT_ROOT)

from config.db import get_db  # type: ignore
from scripts.image_population import image_urls  # type: ignore


def pick_images(n: int = 5) -> list[str]:
    if n > len(image_urls):
        raise RuntimeError("Requested more images than available in pool")
    return random.sample(image_urls, n)


def ensure_host(db, host_id: str | None):
    if host_id:
        from bson.objectid import ObjectId
        user = db.user.find_one({"_id": ObjectId(host_id)})
        if user:
            return user["_id"]
        print("Provided host-id not found. A new host will be created.")

    # Try to pick an existing host
    user = db.user.find_one({"role": "host"})
    if user:
        return user["_id"]

    # Create a minimal host placeholder
    res = db.user.insert_one({
        "firstName": "Host",
        "lastName": str(random.randint(1000, 9999)),
        "email": f"host{random.randint(1000,9999)}@example.com",
        "role": "host",
        "createdAt": datetime.utcnow(),
        "updatedAt": datetime.utcnow(),
    })
    return res.inserted_id


def main():
    parser = argparse.ArgumentParser(description="Add a single property with images")
    parser.add_argument("--title", required=False, default="Stylish Loft", help="Property title")
    parser.add_argument("--description", required=False, default="Bright space with great location.")
    parser.add_argument("--city", required=False, default="Austin")
    parser.add_argument("--country", required=False, default="USA")
    parser.add_argument("--price", type=int, required=False, default=149, help="Price per night")
    parser.add_argument("--host-id", required=False, help="Existing host _id to assign")
    args = parser.parse_args()

    db = get_db()
    host_id = ensure_host(db, args.host_id)

    doc = {
        "host": host_id,
        "title": args.title,
        "description": args.description,
        "location": {
            "address": f"{random.randint(100,9999)} Main St",
            "city": args.city,
            "country": args.country,
        },
        "pricePerNight": args.price,
        "amenities": ["Wifi", "Kitchen", "Washer", "Air conditioning"],
        "images": pick_images(5),
        "rooms": [
            {"name": "Bedroom 1", "type": "bedroom", "beds": 1, "bedType": "Queen"},
            {"name": "Bathroom 1", "type": "bathroom", "beds": 0, "bedType": None},
        ],
        "createdAt": datetime.utcnow(),
        "updatedAt": datetime.utcnow(),
    }

    res = db.property.insert_one(doc)
    print("Inserted property:", str(res.inserted_id))


if __name__ == "__main__":
    main()
