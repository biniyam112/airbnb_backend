from __future__ import annotations
from typing import Dict, Any, Optional, List, Callable
from datetime import datetime, timedelta
import os, asyncio, json
from bson.objectid import ObjectId
from dotenv import load_dotenv
from dedalus_labs import AsyncDedalus, DedalusRunner
from config.db import get_db
from ai_agents.pricing_agent import PricingAgent

load_dotenv()

DEDALUS_API_KEY = os.getenv("DEDALUS_API_KEY")
DEDALUS_MODEL = os.getenv("DEDALUS_MODEL", "openai/gpt-5")

class BookingAgent:
    """Handles booking workflow: quoting, conversational refinement, confirmation."""
    def __init__(self) -> None:
        self.db = get_db()
        self.pricing_agent = PricingAgent()
        self._has_dedalus = bool(DEDALUS_API_KEY)

    # ---- Public API ---- #
    def create_quote(self, property_id: str, guest_id: Optional[str], start_date: str, end_date: str) -> Dict[str, Any]:
        """Generate a booking quote including nightly price, total, availability check, AI message."""
        try:
            prop = self._get_property(property_id)
            if not prop:
                return {"success": False, "error": "Property not found"}
            start_dt, end_dt = self._parse_dates(start_date, end_date)
            if not start_dt or not end_dt:
                return {"success": False, "error": "Invalid date format. Use YYYY-MM-DD."}
            if end_dt <= start_dt:
                return {"success": False, "error": "End date must be after start date"}

            nights = (end_dt - start_dt).days
            availability = self._is_available(property_id, start_dt, end_dt)
            if not availability:
                return {"success": False, "error": "Property not available for selected dates"}

            price_result = self.pricing_agent.suggest_price(property_id)
            nightly = price_result.get("suggested_price", prop.get("pricePerNight", 0))
            total_price = round(nightly * nights, 2)

            booking_doc = {
                "property": ObjectId(property_id),
                "guest": ObjectId(guest_id) if guest_id else None,
                "startDate": start_dt,
                "endDate": end_dt,
                "nights": nights,
                "nightlyPrice": nightly,
                "totalPrice": total_price,
                "pricingSource": price_result.get("source", "fallback"),
                "status": "quote",  # quote, pending, confirmed, cancelled
                "createdAt": datetime.utcnow(),
                "updatedAt": datetime.utcnow(),
            }
            result = self.db.booking.insert_one(booking_doc)
            booking_id = str(result.inserted_id)

            ai_message = self._booking_ai_message(prop, booking_doc, price_result)
            self._save_chat(booking_id, role="assistant", message=ai_message)

            return {
                "success": True,
                "booking_id": booking_id,
                "nights": nights,
                "nightly_price": nightly,
                "total_price": total_price,
                "pricing_source": price_result.get("source"),
                "message": ai_message,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def chat(self, booking_id: str, user_message: str) -> Dict[str, Any]:
        """Continue booking conversation; AI clarifies or moves toward confirmation."""
        try:
            booking = self._get_booking(booking_id)
            if not booking:
                return {"success": False, "error": "Booking not found"}
            prop = self._get_property(str(booking["property"]))
            if not prop:
                return {"success": False, "error": "Property missing for booking"}

            self._save_chat(booking_id, role="user", message=user_message)
            ai_reply = self._booking_chat_ai(prop, booking, user_message)
            self._save_chat(booking_id, role="assistant", message=ai_reply)
            return {"success": True, "reply": ai_reply}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def confirm(self, booking_id: str) -> Dict[str, Any]:
        """Confirm booking if still available."""
        try:
            booking = self._get_booking(booking_id)
            if not booking:
                return {"success": False, "error": "Booking not found"}
            if booking.get("status") == "confirmed":
                return {"success": False, "error": "Already confirmed"}

            # Double-check availability (defensive)
            if not self._is_available(str(booking["property"]), booking["startDate"], booking["endDate"], exclude_booking=booking_id):
                return {"success": False, "error": "Dates no longer available"}

            self.db.booking.update_one({"_id": booking["_id"]}, {"$set": {"status": "confirmed", "updatedAt": datetime.utcnow()}})
            message = "Booking confirmed! We look forward to hosting you."
            self._save_chat(booking_id, role="assistant", message=message)
            return {"success": True, "message": message}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def handle_general_chat(self, user_input: str, conversation_history: Optional[str] = None) -> Dict[str, Any]:
        """Handle general chat/questions using Dedalus when command is not recognized."""
        try:
            if not self._has_dedalus:
                return {
                    "success": True,
                    "reply": "I'm a booking assistant. Available commands: quote, chat, confirm, list-bookings. Type 'help' for more info."
                }
            
            # Build context about available commands and booking system
            context = (
                "You are an AI booking assistant for a property rental platform. "
                "You have access to tools to query the database for properties, bookings, and availability. "
                "Available commands:\n"
                "- quote <property_id> <start:YYYY-MM-DD> <end:YYYY-MM-DD> [guest_id]: Create a booking quote\n"
                "- chat <booking_id> <message>: Continue a conversation about a specific booking\n"
                "- confirm <booking_id>: Confirm a booking\n"
                "- list-bookings [property_id]: List bookings\n\n"
            )
            
            if conversation_history:
                context += f"Previous conversation:\n{conversation_history}\n\n"
            
            prompt = (
                f"{context}"
                f"User question/input: {user_input}\n\n"
                "Use the available tools to query the database if the user asks about properties, bookings, or availability. "
                "Provide a helpful, concise response. If the user is asking about booking functionality, "
                "guide them to use the appropriate commands. Be friendly and helpful."
            )
            
            # Create tools bound to this instance
            tools = [
                self._tool_get_property,
                self._tool_get_booking,
                self._tool_list_bookings,
                self._tool_check_availability,
                self._tool_get_booking_chat_history,
            ]
            
            ai_reply = self._run_dedalus_with_tools(prompt, tools)
            return {"success": True, "reply": ai_reply}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ---- Helpers ---- #
    def _parse_dates(self, start: str, end: str) -> (Optional[datetime], Optional[datetime]):
        fmt = "%Y-%m-%d"
        try:
            return datetime.strptime(start, fmt), datetime.strptime(end, fmt)
        except Exception:
            return None, None

    def _get_property(self, property_id: str) -> Optional[Dict[str, Any]]:
        return self.db.property.find_one({"_id": ObjectId(property_id)})

    def _get_booking(self, booking_id: str) -> Optional[Dict[str, Any]]:
        return self.db.booking.find_one({"_id": ObjectId(booking_id)})

    def _get_chat_history(self, booking_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        cur = self.db.bookingChatHistory.find({"booking": ObjectId(booking_id)}).sort("createdAt", 1).limit(limit)
        return list(cur)

    def _is_available(self, property_id: str, start: datetime, end: datetime, exclude_booking: Optional[str] = None) -> bool:
        query: Dict[str, Any] = {
            "property": ObjectId(property_id),
            "status": "confirmed",
            "startDate": {"$lt": end},  # existing start < new end
            "endDate": {"$gt": start},   # existing end > new start (overlap)
        }
        if exclude_booking:
            query["_id"] = {"$ne": ObjectId(exclude_booking)}
        conflict = self.db.booking.find_one(query)
        return conflict is None

    def _save_chat(self, booking_id: str, role: str, message: str) -> None:
        self.db.bookingChatHistory.insert_one({
            "booking": ObjectId(booking_id),
            "role": role,
            "message": message,
            "createdAt": datetime.utcnow(),
        })

    # ---- AI prompt generation ---- #
    def _booking_ai_message(self, prop: Dict[str, Any], booking: Dict[str, Any], price_result: Dict[str, Any]) -> str:
        if not self._has_dedalus:
            return self._fallback_message(prop, booking, price_result)
        try:
            nightly = booking["nightlyPrice"]
            total = booking["totalPrice"]
            prompt = (
                "You are an AI booking assistant. Provide a concise, friendly quote summary and invite the guest to ask questions or confirm.\n"
                f"Property: {prop.get('title')} in {prop['location'].get('city')}, {prop['location'].get('country')}\n"
                f"Dates: {booking['startDate'].date()} to {booking['endDate'].date()} ({booking['nights']} nights)\n"
                f"Nightly Price: ${nightly:.2f} (source: {price_result.get('source')})\nTotal: ${total:.2f}\n"
                "Mention any standout amenities briefly."
            )
            return self._run_dedalus(prompt)
        except Exception:
            return self._fallback_message(prop, booking, price_result)

    def _booking_chat_ai(self, prop: Dict[str, Any], booking: Dict[str, Any], user_message: str) -> str:
        if not self._has_dedalus:
            return self._fallback_followup(user_message)
        history = self._get_chat_history(str(booking["_id"]))
        hist_lines = [f"{h['role']}: {h['message']}" for h in history]
        hist_block = "\n".join(hist_lines) if hist_lines else "(no previous messages)"
        prompt = (
            "You are continuing a booking conversation. Keep responses short, helpful, and progress toward confirmation if user seems ready.\n"
            f"Property: {prop.get('title')} | City: {prop['location'].get('city')}\n"
            f"Current status: {booking.get('status')} | Nights: {booking.get('nights')} | Total: ${booking.get('totalPrice'):.2f}\n"
            f"History:\n{hist_block}\n\nUser: {user_message}\nAssistant:"
        )
        try:
            return self._run_dedalus(prompt)
        except Exception:
            return self._fallback_followup(user_message)

    def _run_dedalus(self, prompt: str) -> str:
        async def _run():
            client = AsyncDedalus(api_key=DEDALUS_API_KEY)
            runner = DedalusRunner(client)
            models = [DEDALUS_MODEL] if DEDALUS_MODEL else ["openai/gpt-5"]
            result = await runner.run(input=prompt, model=models, tools=[], mcp_servers=[], stream=False)
            return result.final_output.strip()
        return asyncio.run(_run())

    def _run_dedalus_with_tools(self, prompt: str, tools: List[Callable]) -> str:
        """Run Dedalus with provided tools."""
        async def _run():
            client = AsyncDedalus(api_key=DEDALUS_API_KEY)
            runner = DedalusRunner(client)
            models = [DEDALUS_MODEL] if DEDALUS_MODEL else ["openai/gpt-5"]
            result = await runner.run(input=prompt, model=models, tools=tools, mcp_servers=[], stream=False)
            return result.final_output.strip()
        return asyncio.run(_run())

    # ---- Tools for Dedalus ---- #
    def _tool_get_property(self, property_id: str) -> Dict[str, Any]:
        """Get property details by ID. Returns property information or error message."""
        try:
            prop = self._get_property(property_id)
            if not prop:
                return {"success": False, "error": f"Property {property_id} not found"}
            # Convert ObjectId to string for JSON serialization
            prop_dict = {k: (str(v) if isinstance(v, ObjectId) else v) for k, v in prop.items()}
            return {"success": True, "property": prop_dict}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _tool_get_booking(self, booking_id: str) -> Dict[str, Any]:
        """Get booking details by ID. Returns booking information or error message."""
        try:
            booking = self._get_booking(booking_id)
            if not booking:
                return {"success": False, "error": f"Booking {booking_id} not found"}
            # Convert ObjectId and datetime to serializable formats
            booking_dict = {}
            for k, v in booking.items():
                if isinstance(v, ObjectId):
                    booking_dict[k] = str(v)
                elif isinstance(v, datetime):
                    booking_dict[k] = v.isoformat()
                else:
                    booking_dict[k] = v
            return {"success": True, "booking": booking_dict}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _tool_list_bookings(self, property_id: Optional[str] = None, limit: int = 25) -> Dict[str, Any]:
        """List bookings, optionally filtered by property_id. Returns list of bookings."""
        try:
            query = {}
            if property_id:
                query["property"] = ObjectId(property_id)
            
            bookings = []
            for b in self.db.Booking.find(query).sort("createdAt", -1).limit(limit):
                booking_dict = {}
                for k, v in b.items():
                    if isinstance(v, ObjectId):
                        booking_dict[k] = str(v)
                    elif isinstance(v, datetime):
                        booking_dict[k] = v.isoformat()
                    else:
                        booking_dict[k] = v
                bookings.append(booking_dict)
            return {"success": True, "bookings": bookings, "count": len(bookings)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _tool_check_availability(self, property_id: str, start_date: str, end_date: str, exclude_booking_id: Optional[str] = None) -> Dict[str, Any]:
        """Check if property is available for given dates. Returns availability status."""
        try:
            start_dt, end_dt = self._parse_dates(start_date, end_date)
            if not start_dt or not end_dt:
                return {"success": False, "error": "Invalid date format. Use YYYY-MM-DD."}
            if end_dt <= start_dt:
                return {"success": False, "error": "End date must be after start date"}
            
            is_available = self._is_available(property_id, start_dt, end_dt, exclude_booking=exclude_booking_id)
            nights = (end_dt - start_dt).days
            return {
                "success": True,
                "available": is_available,
                "property_id": property_id,
                "start_date": start_date,
                "end_date": end_date,
                "nights": nights
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _tool_get_booking_chat_history(self, booking_id: str, limit: int = 10) -> Dict[str, Any]:
        """Get chat history for a booking. Returns list of messages."""
        try:
            history = self._get_chat_history(booking_id, limit)
            messages = []
            for h in history:
                msg_dict = {}
                for k, v in h.items():
                    if isinstance(v, ObjectId):
                        msg_dict[k] = str(v)
                    elif isinstance(v, datetime):
                        msg_dict[k] = v.isoformat()
                    else:
                        msg_dict[k] = v
                messages.append(msg_dict)
            return {"success": True, "messages": messages, "count": len(messages)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ---- Fallbacks ---- #
    def _fallback_message(self, prop: Dict[str, Any], booking: Dict[str, Any], price_result: Dict[str, Any]) -> str:
        return (
            f"Quote for {prop.get('title')}: {booking['nights']} nights from {booking['startDate'].date()} to {booking['endDate'].date()} at ${booking['nightlyPrice']:.2f}/night. Total ${booking['totalPrice']:.2f}."
            " Amenities: " + ", ".join(prop.get('amenities', [])[:5]) + ". Reply with questions or 'confirm' to proceed."  # guidance
        )

    def _fallback_followup(self, user_message: str) -> str:
        lower = user_message.lower()
        if "confirm" in lower:
            return "To confirm, call the confirmation endpoint. Looking forward to hosting you!"
        return "Thanks for your message. Let me know if you'd like to confirm or adjust dates."
