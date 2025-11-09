from __future__ import annotations
from typing import Optional, Dict, Any
import os
from datetime import datetime, timezone
from bson.objectid import ObjectId
from dotenv import load_dotenv
from dedalus_labs import AsyncDedalus, DedalusRunner
from config.db import get_db

load_dotenv()

DEDALUS_API_KEY = os.getenv("DEDALUS_API_KEY")
DEDALUS_MODEL = os.getenv("DEDALUS_MODEL", "openai/gpt-5")

class MaintenanceAgent:
    """Dedalus-powered agent that manages property cleaning schedules and maintenance logs."""

    def __init__(self) -> None:
        self.db = get_db()
        self._has_dedalus = bool(DEDALUS_API_KEY)

    async def handle_checkout(self, property_id: str, checkout_time: str) -> Dict[str, Any]:
        """Handle guest checkout and schedule cleaning.
        
        Args:
            property_id: ID of the property being checked out
            checkout_time: ISO format checkout timestamp
        
        Returns:
            Dict with operation status and cleaning details
        """
        try:
            # Convert IDs and dates
            prop_id = ObjectId(property_id)
            checkout_dt = datetime.fromisoformat(checkout_time)

            # Fetch property details
            property_doc = self.db.property.find_one({"_id": prop_id})
            
            if not property_doc:
                return {"success": False, "error": "Property not found"}
            
            if "cleaner_id" not in property_doc:
                return {"success": False, "error": "No cleaner assigned to this property"}

            cleaner_id = property_doc["cleaner_id"]
            
            # Find next booking
            next_booking = self.db.booking.find_one({
                "property": prop_id,
                "startDate": {"$gt": checkout_dt},
                "status": "confirmed"
            }, sort=[("startDate", 1)])

            # Calculate cleaning window
            time_to_finish = None
            if next_booking:
                next_checkin = next_booking["startDate"]
                cleaning_window = next_checkin - checkout_dt
                time_to_finish = int(cleaning_window.total_seconds() / 60)

            ai_response = ""
            # Generate AI response using Dedalus
            if self._has_dedalus:
                context = self._format_cleaning_context(
                    property_doc, 
                    checkout_dt, 
                    next_booking,
                    time_to_finish
                )
                ai_response = await self._get_dedalus_response(context)
            else:
                ai_response = "Cleaning schedule created successfully."
            # Create cleaning log
            log_entry = {
                "propertyId": prop_id,
                "cleanerId": cleaner_id,
                "checkoutTime": checkout_dt,
                "timeToFinishMinutes": time_to_finish,
                "nextBookingId": next_booking["_id"] if next_booking else None,
                "createdAt": datetime.now(timezone.utc),
                "aiResponse": ai_response
            }
            result = self.db.cleanerLogs.insert_one(log_entry)


            return {
                "success": True,
                "log_id": str(result.inserted_id),
                "cleaner_id": str(cleaner_id),
                "time_to_finish": time_to_finish,
                "next_booking": str(next_booking["_id"]) if next_booking else None,
                "ai_response": ai_response
            }
            
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _format_cleaning_context(
        self, 
        property_doc: Dict[str, Any], 
        checkout_time: datetime,
        next_booking: Optional[Dict[str, Any]],
        cleaning_window: Optional[int]
    ) -> str:
        """Format property and cleaning details for AI context"""
        parts = [
            f"Property: {property_doc.get('title', 'N/A')}",
            f"Location: {property_doc.get('location', {}).get('address', 'N/A')}",
            f"Checkout Time: {checkout_time.isoformat()}",
            f"Cleaning Window: {cleaning_window} minutes" if cleaning_window else "No time constraint",
        ]
        
        if next_booking:
            parts.append(f"Next Check-in: {next_booking['startDate'].isoformat()}")
        
        return "\n".join(parts)

    async def _get_dedalus_response(self, context: str) -> str:
        """Get AI response using Dedalus"""
        system = (
            "You are a professional maintenance coordinator. "
            "Review the cleaning schedule details and provide a brief, "
            "Clear and precise summary of the cleaning requirements and timing with less than three sentences."
        )

        prompt = (
            f"SYSTEM INSTRUCTIONS:\n{system}\n\n"
            f"CLEANING CONTEXT:\n{context}\n\n"
            "Assistant:"
        )

        try:
            client = AsyncDedalus(api_key=DEDALUS_API_KEY)
            runner = DedalusRunner(client)
            model_list = [DEDALUS_MODEL] if DEDALUS_MODEL else ["openai/gpt-5"]
            result = await runner.run(
                input=prompt,
                model=model_list,
                tools=[],
                mcp_servers=[],
                stream=False
            )
            return result.final_output
        except Exception as e:
            print(f"Dedalus maintenance response error: {e}")
            return "Cleaning schedule created successfully."