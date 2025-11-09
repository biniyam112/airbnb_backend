from typing import Dict, List, Optional, Any
from dataclasses import dataclass
import json
from datetime import datetime
from bson import ObjectId
from config.db import get_db
import os
from dotenv import load_dotenv
import asyncio
from dedalus_labs import AsyncDedalus, DedalusRunner

# Load environment variables
load_dotenv()

DEDALUS_API_KEY = os.getenv('DEDALUS_API_KEY')
DEDALUS_MODEL = os.getenv('DEDALUS_MODEL', 'openai/gpt-5')  # configurable model list entry

# ---------- Helper tool functions passed to Dedalus (optional enrichment) ---------- #
def comp_avg_price(comparables: List[Dict[str, Any]]) -> float:
    prices = [c.get('pricePerNight', 0) for c in comparables if c.get('pricePerNight')]
    return round(sum(prices) / len(prices), 2) if prices else 0.0

def comp_median_price(comparables: List[Dict[str, Any]]) -> float:
    prices = sorted([c.get('pricePerNight', 0) for c in comparables if c.get('pricePerNight')])
    if not prices:
        return 0.0
    mid = len(prices) // 2
    if len(prices) % 2 == 0:
        return round((prices[mid - 1] + prices[mid]) / 2.0, 2)
    return float(prices[mid])

def occupancy_adjustment(occupancy_rates: List[float]) -> float:
    """Simple occupancy adjustment factor based on recent occupancy percentages."""
    if not occupancy_rates:
        return 1.0
    avg = sum(occupancy_rates) / len(occupancy_rates)
    # Scale: >85% demand high => increase, <50% demand low => decrease
    if avg >= 0.85:
        return 1.15
    if avg >= 0.70:
        return 1.08
    if avg >= 0.50:
        return 1.00
    return 0.9

@dataclass
class RoomInfo:
    type: str
    count: int
    details: Dict[str, Any]

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'RoomInfo':
        return cls(
            type=data['type'],
            count=data['count'],
            details=data['details']
        )

@dataclass
class ListingDetails:
    location: Dict[str, Any]
    title: str
    rooms: List[RoomInfo]
    amenities: List[str]
    property_id: str
    historical_pricing: Optional[List[Dict[str, Any]]] = None

    @classmethod
    def from_property_data(cls, property_data: Dict[str, Any], historical_pricing: List[Dict[str, Any]]) -> 'ListingDetails':
        return cls(
            location=property_data['location'],
            title=property_data['title'],
            rooms=[RoomInfo.from_dict(room) for room in property_data['rooms']],
            amenities=property_data['amenities'],
            property_id=str(property_data['_id']),
            historical_pricing=historical_pricing
        )

class PricingAgent:
    def __init__(self):
        """Initialize the pricing agent with database connection and optional Dedalus client."""
        self.db = get_db()
        # Dedalus is optional; if no API key we will fallback automatically.
        self._dedalus_available = bool(DEDALUS_API_KEY)

    def suggest_price(self, property_id: str) -> dict:
        """
        Suggests an optimal price for an Airbnb listing using Dedalus Labs models.

        Process:
        1. Gather property data & comparables.
        2. If Dedalus API key available: invoke model with structured prompt & helper tools.
        3. Parse JSON response for suggested_price, reasoning & factors.
        4. Persist pricing history.
        5. Fallback to internal heuristic if Dedalus fails or returns invalid output.
        """
        try:
            property_data = self.db.property.find_one({"_id": ObjectId(property_id)})
            if not property_data:
                raise ValueError("Property not found")

            comparables = list(self.db.property.find({
                "location.city": property_data["location"]["city"],
                "_id": {"$ne": ObjectId(property_id)}
            }).limit(25))  # limit for prompt size

            dedalus_result: Optional[dict] = None
            if self._dedalus_available:
                dedalus_result = self._invoke_dedalus(property_data, comparables)

            if dedalus_result and isinstance(dedalus_result, dict):
                suggested_price = float(dedalus_result.get('suggested_price', 0) or 0)
                if suggested_price > 0:
                    reasoning = dedalus_result.get('reasoning', '')
                    factors = dedalus_result.get('factors', [])
                    self._save_price_history(
                        property_id=property_id,
                        old_price=property_data.get('pricePerNight', 0),
                        new_price=suggested_price,
                        reason=f"Dedalus model pricing: {reasoning}"
                    )
                    return {
                        "success": True,
                        "source": "dedalus",
                        "suggested_price": round(suggested_price, 2),
                        "currency": "USD",
                        "factors_considered": factors,
                        "reasoning": reasoning
                    }

            # Fallback path
            fallback = self._fallback_price_calculation(property_data)
            fallback["source"] = "fallback"
            return fallback

        except Exception as e:
            return {"success": False, "error": str(e)}

    # ---------------- Dedalus integration ---------------- #
    def _invoke_dedalus(self, property_data: dict, comparables: List[dict]) -> Optional[dict]:
        """Run Dedalus model to get pricing suggestion. Returns parsed dict or None."""
        try:
            async def _run():
                client = AsyncDedalus(api_key=DEDALUS_API_KEY)
                runner = DedalusRunner(client)

                avg_price = comp_avg_price(comparables)
                median_price = comp_median_price(comparables)
                occupancy_rates = []  # TODO: derive actual occupancy metrics
                occ_factor = occupancy_adjustment(occupancy_rates)

                prompt = (
                    "You are a pricing optimization agent for short-term rental properties. "
                    "Given the target property details and a summary of comparable listings, "
                    "produce ONLY a valid JSON object with keys: suggested_price (number), reasoning (string summary with with 100 words max), factors (string array). "
                    "Price must be in USD and reflect market, amenity value, and occupancy factor. No extra text outside JSON.\n\n"
                    f"Target Property Title: {property_data.get('title')}\n"
                    f"Location: {property_data['location'].get('city')}, {property_data['location'].get('country')}\n"
                    f"Amenities: {', '.join(property_data.get('amenities', []))}\n"
                    f"Current Price: {property_data.get('pricePerNight', 0)}\n"
                    f"Comparable Avg Price: {avg_price}\nComparable Median Price: {median_price}\n"
                    f"Occupancy Adjustment Factor (heuristic): {occ_factor}\n"
                    "List up to 5 most influential factors in 'factors'."
                )

                # Ensure we never pass a None model entry
                model_list = [DEDALUS_MODEL] if DEDALUS_MODEL else ["openai/gpt-5"]
                result = await runner.run(
                    input=prompt,
                    model=model_list,
                    tools=[comp_avg_price, comp_median_price, occupancy_adjustment],
                    mcp_servers=[],  # could add weather/demand MCP servers later
                    stream=False
                )
                # result.final_output expected to be JSON string
                raw = result.final_output
                try:
                    return json.loads(raw)
                except Exception:
                    # attempt to extract JSON braces
                    start = raw.find('{')
                    end = raw.rfind('}')
                    if start != -1 and end != -1:
                        snippet = raw[start:end+1]
                        return json.loads(snippet)
                    return None

            return asyncio.run(_run())
        except Exception as e:
            print(f"Dedalus pricing invocation failed: {e}")
            return None

    def _fallback_price_calculation(self, property_data: dict) -> dict:
        """Fallback method for price calculation when MCP is unavailable"""
        try:
            # Create ListingDetails object for the fallback calculation
            historical_pricing = list(self.db.pricingHistory.find(
                {"property": property_data["_id"]}
            ).sort("createdAt", -1).limit(30))
            
            listing_details = ListingDetails.from_property_data(property_data, historical_pricing)
            
            # Calculate price using traditional factors
            base_price = self._calculate_base_price(listing_details)
            seasonal_adjustment = self._get_seasonal_adjustment()
            market_adjustment = self._calculate_market_adjustment(listing_details)
            
            suggested_price = base_price * seasonal_adjustment * market_adjustment
            
            # Save price suggestion to history
            self._save_price_history(
                property_id=str(property_data["_id"]),
                old_price=property_data.get("pricePerNight", 0),
                new_price=suggested_price,
                reason="Fallback pricing calculation (MCP unavailable)"
            )
            
            return {
                "success": True,
                "suggested_price": round(suggested_price, 2),
                "currency": "USD",
                "factors_considered": [
                    "base_property_features",
                    "seasonal_demand",
                    "local_market_trends"
                ],
                "analysis": {
                    "method": "fallback",
                    "base_price": base_price,
                    "seasonal_factor": seasonal_adjustment,
                    "market_factor": market_adjustment
                }
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }

    def _calculate_base_price(self, listing_details: ListingDetails) -> float:
        """Calculate base price based on property characteristics"""
        base_price = 100.0  # Starting base price
        
        # Calculate room-based adjustments
        for room in listing_details.rooms:
            if room.type == "bedroom":
                base_price += room.count * 50
                if room.details.get("bedType") == "queen":
                    base_price += 20
            elif room.type == "bathroom":
                base_price += room.count * 30
                if room.details.get("hasBathtub"):
                    base_price += 15
            elif room.type == "kitchen":
                base_price += len(room.details.get("appliances", [])) * 5
        
        return base_price

    def _get_seasonal_adjustment(self) -> float:
        """Get seasonal adjustment based on current date"""
        month = datetime.now().month
        if month in (12, 1, 2):
            season = "winter"
        elif month in (3, 4, 5):
            season = "spring"
        elif month in (6, 7, 8):
            season = "summer"
        else:
            season = "fall"
            
        return self._apply_seasonal_adjustment(season)

    def _calculate_market_adjustment(self, listing_details: ListingDetails) -> float:
        """Calculate market adjustment based on historical data and location"""
        try:
            # Get similar properties in the same city
            similar_properties = self.db.property.find({
                "location.city": listing_details.location["city"],
                "_id": {"$ne": ObjectId(listing_details.property_id)}
            })
            
            prices = [p.get("pricePerNight", 0) for p in similar_properties]
            if not prices:
                return 1.0
                
            avg_market_price = sum(prices) / len(prices)
            if avg_market_price == 0:
                return 1.0
                
            # Adjust towards market average but maintain some pricing power
            return 0.8 + (0.4 * (avg_market_price / self._calculate_base_price(listing_details)))
            
        except Exception:
            return 1.0

    def _save_price_history(self, property_id: str, old_price: float, new_price: float, reason: str):
        """Save price change to history"""
        self.db.pricingHistory.insert_one({
            "property": ObjectId(property_id),
            "oldPrice": old_price,
            "newPrice": new_price,
            "suggestedByAI": True,
            "reason": reason,
            "createdAt": datetime.utcnow()
        })

    def _apply_seasonal_adjustment(self, season: str) -> float:
        """Apply seasonal adjustment factors"""
        seasonal_factors = {
            "summer": 1.2,
            "winter": 1.1,
            "spring": 1.0,
            "fall": 0.9
        }
        return seasonal_factors.get(season.lower(), 1.0)

if __name__ == "__main__":
    pass  # Use test_pricing.py for testing