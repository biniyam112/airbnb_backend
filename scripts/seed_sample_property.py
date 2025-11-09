from datetime import datetime
from bson.objectid import ObjectId
from dotenv import load_dotenv
from config.db import get_db

load_dotenv()

def main():
    db = get_db()
    sample = {
        "host": ObjectId(),
        "title": "Sample Downtown Apartment",
        "description": "Modern apartment close to attractions.",
        "location": {
            "address": "123 Main St",
            "city": "New York",
            "country": "USA",
            "coordinates": {"lat": 40.7128, "lng": -74.0060},
        },
        "rooms": [
            {"type": "bedroom", "count": 2, "details": {"bedType": "queen", "hasEnsuite": True}},
            {"type": "bathroom", "count": 1, "details": {"hasShower": True, "hasBathtub": False}},
            {"type": "kitchen", "count": 1, "details": {"appliances": ["Fridge", "Oven", "Microwave"]}},
        ],
        "amenities": ["wifi", "ac", "parking", "gym"],
        "pricePerNight": 200,
        "dynamicPrice": None,
        "isAvailable": True,
        "createdAt": datetime.utcnow(),
    }
    result = db.property.insert_one(sample)
    print(str(result.inserted_id))

if __name__ == "__main__":
    main()
