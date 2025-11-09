from pymongo import MongoClient
from bson.objectid import ObjectId
from datetime import datetime
import json
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Sample property data
test_property = {
    "title": "Modern Downtown Apartment",
    "description": "Beautiful modern apartment in the heart of the city",
    "location": {
        "address": "123 Main St",
        "city": "New York",
        "country": "USA",
        "coordinates": {
            "lat": 40.7128,
            "lng": -74.0060
        }
    },
    "rooms": [
        {
            "type": "bedroom",
            "count": 2,
            "details": {
                "bedType": "queen",
                "hasEnsuite": True
            }
        },
        {
            "type": "bathroom",
            "count": 1,
            "details": {
                "hasShower": True,
                "hasBathtub": True
            }
        },
        {
            "type": "kitchen",
            "count": 1,
            "details": {
                "appliances": ["Fridge", "Oven", "Microwave", "Dishwasher"]
            }
        }
    ],
    "amenities": ["wifi", "ac", "parking", "gym"],
    "pricePerNight": 150,
    "dynamicPrice": None,
    "isAvailable": True,
    "createdAt": datetime.utcnow()
}

def main():
    # Connect to MongoDB using URI from environment variables
    mongodb_uri = os.getenv('MONGODB_URI')
    client = MongoClient(mongodb_uri)
    db = client.get_database()
    
    # Insert test property
    result = db.property.insert_one(test_property)
    property_id = str(result.inserted_id)
    print(f"Created test property with ID: {property_id}")
    
    # Import and test PricingAgent
    from ai_agents.pricing_agent import PricingAgent
    agent = PricingAgent()
    
    # Get price suggestion
    suggestion = agent.suggest_price(property_id)
    print("\nPrice Suggestion:", json.dumps(suggestion, indent=2))

if __name__ == "__main__":
    main()