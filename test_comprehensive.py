import requests
import json
from datetime import datetime
import os
from dotenv import load_dotenv
from bson.objectid import ObjectId
from config.db import get_db, DATABASE_NAME

# Load environment variables
load_dotenv()

# Sample property data with all required fields
test_property = {
    "host": ObjectId(),  # Placeholder host ID
    "title": "Luxury Downtown Apartment",
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
            "count": 2,
            "details": {
                "hasShower": True,
                "hasBathtub": True
            }
        },
        {
            "type": "kitchen",
            "count": 1,
            "details": {
                "appliances": ["Fridge", "Oven", "Microwave", "Dishwasher", "Coffee Maker"]
            }
        }
    ],
    "amenities": ["wifi", "ac", "parking", "gym", "pool", "doorman"],
    "pricePerNight": 200,
    "dynamicPrice": None,
    "isAvailable": True,
    "createdAt": datetime.utcnow()
}

def test_database_connection():
    """Test MongoDB connection and basic operations"""
    try:
        # Get database connection
        db = get_db()
        
        # Test write operation
        result = db.property.insert_one(test_property)
        property_id = str(result.inserted_id)
        print(f"✅ Database connection successful")
        print(f"✅ Test property created with ID: {property_id}")
        
        return property_id
    except Exception as e:
        print(f"❌ Database connection failed: {str(e)}")
        return None

def test_pricing_api(property_id):
    """Test the dynamic pricing API endpoint"""
    try:
        # Test the API endpoint
        response = requests.get(f"http://localhost:5000/api/ai/dynamic-pricing/suggest/{property_id}")
        result = response.json()
        
        if response.status_code == 200 and result.get("success"):
            print("\n✅ API endpoint test successful")
            print("\nPrice Suggestion Details:")
            print(json.dumps(result, indent=2))
        else:
            print(f"\n❌ API endpoint test failed: {result.get('error', 'Unknown error')}")
            
    except requests.exceptions.ConnectionError:
        print("\n❌ API endpoint test failed: Could not connect to the Flask server")
    except Exception as e:
        print(f"\n❌ API endpoint test failed: {str(e)}")

def test_direct_agent(property_id):
    """Test the PricingAgent directly"""
    try:
        from ai_agents.pricing_agent import PricingAgent
        
        agent = PricingAgent()
        result = agent.suggest_price(property_id)
        
        if result.get("success"):
            print("\n✅ Direct agent test successful")
            print("\nDirect Price Suggestion Details:")
            print(json.dumps(result, indent=2))
        else:
            print(f"\n❌ Direct agent test failed: {result.get('error', 'Unknown error')}")
            
    except Exception as e:
        print(f"\n❌ Direct agent test failed: {str(e)}")

def main():
    print("\n=== Starting Airbnb AI Backend Tests ===\n")
    
    # Test 1: Database Connection and Setup
    property_id = test_database_connection()
    if not property_id:
        print("\n❌ Tests aborted due to database connection failure")
        return
    
    # Test 2: API Endpoint
    test_pricing_api(property_id)
    
    # Test 3: Direct Agent Testing
    test_direct_agent(property_id)
    
    print("\n=== Test Suite Completed ===\n")

if __name__ == "__main__":
    main()