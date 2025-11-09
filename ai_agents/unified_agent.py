from __future__ import annotations
from typing import Dict, Any, Optional, List, Callable
from datetime import datetime, timedelta
import os
import asyncio
import json
import re
from bson.objectid import ObjectId
from dotenv import load_dotenv
from dedalus_labs import AsyncDedalus, DedalusRunner
from config.db import get_db
from ai_agents.pricing_agent import PricingAgent

load_dotenv()

DEDALUS_API_KEY = os.getenv("DEDALUS_API_KEY")
DEDALUS_MODEL = os.getenv("DEDALUS_MODEL", "openai/gpt-5")


class UnifiedAgent:
    """Unified AI agent that handles booking, property chat, host advice, pricing, and general queries."""
    
    def __init__(self) -> None:
        self.db = get_db()
        self.pricing_agent = PricingAgent()
        self._has_dedalus = bool(DEDALUS_API_KEY)
    
    # ==================== BOOKING OPERATIONS ====================
    
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
                "status": "quote",
                "createdAt": datetime.utcnow(),
                "updatedAt": datetime.utcnow(),
            }
            result = self.db.booking.insert_one(booking_doc)
            booking_id = str(result.inserted_id)

            ai_message = self._booking_ai_message(prop, booking_doc, price_result)
            self._save_booking_chat(booking_id, role="assistant", message=ai_message)

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

    def booking_chat(self, booking_id: str, user_message: str) -> Dict[str, Any]:
        """Continue booking conversation; AI clarifies or moves toward confirmation."""
        try:
            booking = self._get_booking(booking_id)
            if not booking:
                return {"success": False, "error": "Booking not found"}
            prop = self._get_property(str(booking["property"]))
            if not prop:
                return {"success": False, "error": "Property missing for booking"}

            self._save_booking_chat(booking_id, role="user", message=user_message)
            ai_reply = self._booking_chat_ai(prop, booking, user_message)
            self._save_booking_chat(booking_id, role="assistant", message=ai_reply)
            return {"success": True, "reply": ai_reply}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def confirm_booking(self, booking_id: str) -> Dict[str, Any]:
        """Confirm booking if still available."""
        try:
            booking = self._get_booking(booking_id)
            if not booking:
                return {"success": False, "error": "Booking not found"}
            if booking.get("status") == "confirmed":
                return {"success": False, "error": "Already confirmed"}

            if not self._is_available(str(booking["property"]), booking["startDate"], booking["endDate"], exclude_booking=booking_id):
                return {"success": False, "error": "Dates no longer available"}

            self.db.booking.update_one({"_id": booking["_id"]}, {"$set": {"status": "confirmed", "updatedAt": datetime.utcnow()}})
            message = "Booking confirmed! We look forward to hosting you."
            self._save_booking_chat(booking_id, role="assistant", message=message)
            return {"success": True, "message": message}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    # ==================== PROPERTY CHAT OPERATIONS ====================
    
    def property_chat(self, property_id: str, user_message: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        """Ask a question about a property and get a Dedalus-generated answer."""
        try:
            prop = self._get_property(property_id)
            if not prop:
                return {"success": False, "error": "Property not found"}

            session = session_id or str(property_id)
            self._save_property_chat(property_id, session, role="user", message=user_message)

            assistant_message = self._dedalus_property_answer(prop, user_message, session)
            if not assistant_message:
                assistant_message = (
                    "I'm unable to retrieve the information right now. "
                    "Please try again later or contact the host."
                )

            self._save_property_chat(property_id, session, role="assistant", message=assistant_message)

            return {
                "success": True,
                "message": assistant_message,
                "session_id": session
            }
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    # ==================== HOST COMMUNITY OPERATIONS ====================
    
    def get_host_advice(self, host_id: str, focus: Optional[str] = None) -> Dict[str, Any]:
        """Return structured advice for a host."""
        try:
            properties = self._get_host_properties(host_id)
            if not properties:
                return {"success": False, "error": "Host has no properties"}

            perf_window_days = 90
            now = datetime.utcnow()
            window_start = now - timedelta(days=perf_window_days)
            host_metrics = self._aggregate_host_metrics(properties, window_start)
            comparison = self._get_top_performer_sample(properties, window_start)

            if self._has_dedalus:
                ai_json = self._dedalus_host_advice(host_metrics, comparison, focus)
                if ai_json:
                    if focus:
                        ai_json["recommendations"] = [r for r in ai_json.get("recommendations", []) if r.get("category") == focus]
                    return {"success": True, "data": ai_json, "source": "dedalus"}

            fallback = self._fallback_host_advice(host_metrics, comparison, focus)
            return {"success": True, "data": fallback, "source": "fallback"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def host_chat(self, host_id: str, question: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        """Chat-style Q&A for a host seeking guidance."""
        try:
            properties = self._get_host_properties(host_id)
            if not properties:
                return {"success": False, "error": "Host has no properties"}

            session = session_id or host_id
            self._save_host_chat(session, role="user", message=question, host_id=host_id)
            response = self._dedalus_host_chat(properties, question, session) if self._has_dedalus else self._fallback_host_chat(question)
            self._save_host_chat(session, role="assistant", message=response, host_id=host_id)
            return {"success": True, "message": response, "session_id": session}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    # ==================== PRICING OPERATIONS ====================
    
    def suggest_price(self, property_id: str) -> Dict[str, Any]:
        """Suggest optimal price for a property."""
        return self.pricing_agent.suggest_price(property_id)
    
    # ==================== GENERAL CHAT / ROUTING ====================
    
    def chat(self, user_input: str, context: Optional[Dict[str, Any]] = None, conversation_history: Optional[str] = None) -> Dict[str, Any]:
        """Intelligent routing to appropriate functionality based on user input.
        
        Context can include:
        - property_id: for property-specific queries
        - booking_id: for booking-specific queries
        - host_id: for host-specific queries
        - session_id: for maintaining conversation context
        """
        try:
            if not self._has_dedalus:
                return {
                    "success": True,
                    "reply": "I'm an AI assistant for property rentals. I can help with bookings, property questions, host advice, and pricing. Please specify what you need help with."
                }
            
            # Extract context from conversation history if not provided
            if context is None:
                context = self._extract_context_from_history(conversation_history)
            
            # Build context about available capabilities
            system_context = (
                "You are a unified AI assistant for a property rental platform. "
                "You can help users with:\n"
                "1. BOOKING OPERATIONS:\n"
                "   - Create booking quotes (use _tool_create_quote when user wants to book or get a quote)\n"
                "   - Chat about existing bookings (use _tool_booking_chat when user asks about a booking)\n"
                "   - Confirm bookings (use _tool_confirm_booking when user wants to confirm)\n"
                "   - Check availability (use _tool_check_availability)\n"
                "   - List bookings (use _tool_list_bookings)\n\n"
                "2. PROPERTY INFORMATION:\n"
                "   - Answer questions about properties (use _tool_property_chat when user asks about a property)\n"
                "   - Get property details (use _tool_get_property)\n"
                "   - List properties (use _tool_list_properties)\n"
                "   - Search properties by location (use _tool_search_properties_by_location when user wants properties in a city/country)\n"
                "   - Search properties by amenities (use _tool_search_properties_by_amenities when user wants specific amenities)\n"
                "   - Search properties by rooms (use _tool_search_properties_by_rooms when user wants specific bedroom/bathroom counts)\n"
                "   - Search properties by price (use _tool_search_properties_by_price when user wants properties in a price range)\n"
                "   - Comprehensive property search (use _tool_search_properties when user wants to combine multiple filters)\n\n"
                "3. HOST OPERATIONS:\n"
                "   - Provide host advice (use _tool_get_host_advice when host asks for advice)\n"
                "   - Chat with hosts (use _tool_host_chat when host asks questions)\n"
                "   - Get host properties (use _tool_get_host_properties)\n\n"
                "4. PRICING:\n"
                "   - Suggest prices (use _tool_suggest_price when user asks about pricing)\n\n"
                "IMPORTANT: When the user wants to perform an action (book, confirm, ask about property, etc.), "
                "use the appropriate action tool. Extract IDs from the conversation or ask the user if needed.\n\n"
            )
            
            if context:
                if context.get("property_id"):
                    system_context += f"Current context: User is asking about property {context['property_id']}\n"
                if context.get("booking_id"):
                    system_context += f"Current context: User is asking about booking {context['booking_id']}\n"
                if context.get("host_id"):
                    system_context += f"Current context: User is a host (ID: {context['host_id']})\n"
            
            if conversation_history:
                system_context += f"Previous conversation:\n{conversation_history}\n\n"
            
            prompt = (
                f"{system_context}"
                f"User input: {user_input}\n\n"
                "Analyze the user's request and use the appropriate tools to help them. "
                "If they want to book a property, create a quote. If they're asking about a property, use property chat. "
                "If they're a host asking for advice, use host tools. Be proactive and helpful."
            )
            
            # Create comprehensive tool set including action tools
            tools = [
                # Query tools
                self._tool_get_property,
                self._tool_get_booking,
                self._tool_list_bookings,
                self._tool_check_availability,
                self._tool_get_booking_chat_history,
                self._tool_list_properties,
                self._tool_search_properties_by_location,
                self._tool_search_properties_by_amenities,
                self._tool_search_properties_by_rooms,
                self._tool_search_properties_by_price,
                self._tool_search_properties,
                self._tool_get_property_chat_history,
                self._tool_get_host_properties,
                self._tool_suggest_price,
                # Action tools
                self._tool_create_quote,
                self._tool_confirm_booking,
                self._tool_booking_chat,
                self._tool_property_chat,
                self._tool_get_host_advice,
                self._tool_host_chat,
            ]
            
            ai_reply = self._run_dedalus_with_tools(prompt, tools)
            return {"success": True, "reply": ai_reply}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def _extract_context_from_history(self, conversation_history: Optional[str] = None) -> Dict[str, Any]:
        """Extract context (property_id, booking_id, host_id) from conversation history."""
        context = {}
        if not conversation_history:
            return context
        
        # Simple extraction - look for IDs mentioned in conversation
        # Look for property IDs (24 char hex strings)
        property_matches = re.findall(r'property[_\s]*id[:\s]*([a-f0-9]{24})', conversation_history, re.IGNORECASE)
        if property_matches:
            context["property_id"] = property_matches[-1]  # Use most recent
        
        # Look for booking IDs
        booking_matches = re.findall(r'booking[_\s]*id[:\s]*([a-f0-9]{24})', conversation_history, re.IGNORECASE)
        if booking_matches:
            context["booking_id"] = booking_matches[-1]
        
        # Look for host IDs
        host_matches = re.findall(r'host[_\s]*id[:\s]*([a-f0-9]{24})', conversation_history, re.IGNORECASE)
        if host_matches:
            context["host_id"] = host_matches[-1]
        
        return context
    
    # ==================== DATABASE HELPERS ====================
    
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

    def _get_host_properties(self, host_id: str) -> List[Dict[str, Any]]:
        cur = self.db.property.find({"host": ObjectId(host_id)})
        return list(cur)

    def _is_available(self, property_id: str, start: datetime, end: datetime, exclude_booking: Optional[str] = None) -> bool:
        query: Dict[str, Any] = {
            "property": ObjectId(property_id),
            "status": "confirmed",
            "startDate": {"$lt": end},
            "endDate": {"$gt": start},
        }
        if exclude_booking:
            query["_id"] = {"$ne": ObjectId(exclude_booking)}
        conflict = self.db.booking.find_one(query)
        return conflict is None

    def _get_booking_chat_history(self, booking_id: str, limit: int = 10) -> List[Dict[str, Any]]:
        cur = self.db.bookingChatHistory.find({"booking": ObjectId(booking_id)}).sort("createdAt", 1).limit(limit)
        return list(cur)

    def _get_property_chat_history(self, property_id: str, session_id: str, limit: int = 12) -> List[Dict[str, Any]]:
        cur = self.db.propertyChatHistory.find({
            "property": ObjectId(property_id),
            "sessionId": session_id
        }).sort("createdAt", 1).limit(limit)
        return list(cur)

    def _get_host_chat_history(self, session_id: str, limit: int = 12) -> List[Dict[str, Any]]:
        cur = self.db.hostCommunityChatHistory.find({"sessionId": session_id}).sort("createdAt", 1).limit(limit)
        return list(cur)

    # ==================== CHAT HISTORY PERSISTENCE ====================
    
    def _save_booking_chat(self, booking_id: str, role: str, message: str) -> None:
        self.db.bookingChatHistory.insert_one({
            "booking": ObjectId(booking_id),
            "role": role,
            "message": message,
            "createdAt": datetime.utcnow(),
        })

    def _save_property_chat(self, property_id: str, session_id: str, role: str, message: str) -> None:
        self.db.propertyChatHistory.insert_one({
            "property": ObjectId(property_id),
            "sessionId": session_id,
            "role": role,
            "message": message,
            "createdAt": datetime.utcnow(),
        })

    def _save_host_chat(self, session_id: str, role: str, message: str, host_id: str) -> None:
        self.db.hostCommunityChatHistory.insert_one({
            "sessionId": session_id,
            "host": ObjectId(host_id),
            "role": role,
            "message": message,
            "createdAt": datetime.utcnow(),
        })
    
    # ==================== AI MESSAGE GENERATION ====================
    
    def _booking_ai_message(self, prop: Dict[str, Any], booking: Dict[str, Any], price_result: Dict[str, Any]) -> str:
        if not self._has_dedalus:
            return self._fallback_booking_message(prop, booking, price_result)
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
            return self._fallback_booking_message(prop, booking, price_result)

    def _booking_chat_ai(self, prop: Dict[str, Any], booking: Dict[str, Any], user_message: str) -> str:
        if not self._has_dedalus:
            return self._fallback_booking_followup(user_message)
        history = self._get_booking_chat_history(str(booking["_id"]))
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
            return self._fallback_booking_followup(user_message)

    def _dedalus_property_answer(self, prop: Dict[str, Any], user_message: str, session_id: str) -> Optional[str]:
        if not self._has_dedalus:
            return None
        try:
            context = self._format_property_context(prop)
            history = self._get_property_chat_history(str(prop['_id']), session_id, limit=12)
            history_snippet = self._format_history_snippet(history)

            system = (
                "You are a professional, helpful property concierge AI. "
                "Answer questions about the listing based strictly on the provided property context. "
                "If information is missing, say you are not sure and offer to check with the host. "
                "Be concise, friendly, and accurate."
            )

            prompt = (
                f"SYSTEM INSTRUCTIONS:\n{system}\n\n"
                f"PROPERTY CONTEXT:\n{context}\n\n"
                f"RECENT CHAT HISTORY:\n{history_snippet or '(no previous messages)'}\n\n"
                f"User: {user_message}\nAssistant:"
            )

            return self._run_dedalus(prompt)
        except Exception as e:
            print(f"Dedalus property chat error: {e}")
            return None

    def _dedalus_host_advice(self, host_metrics: Dict[str, Any], comparison: List[Dict[str, Any]], focus: Optional[str]) -> Optional[Dict[str, Any]]:
        try:
            prompt = (
                "You are an elite host performance optimization AI. Given the host portfolio summary and a sample of top performing comparable properties, "
                "produce ONLY valid JSON with keys: summary (string), recommendations (array of objects with category, advice, priority), quick_wins (array of short strings). "
                "Valid categories: listing_quality, pricing_strategy, guest_experience, occupancy_growth. Priorities: high|medium|low. Keep advice concise."
                f"\n\nHOST_METRICS={host_metrics}\nTOP_PERFORMERS={comparison}\nFOCUS={focus or 'all'}\n"
            )

            async def _run():
                client = AsyncDedalus(api_key=DEDALUS_API_KEY)
                runner = DedalusRunner(client)
                models = [DEDALUS_MODEL] if DEDALUS_MODEL else ["openai/gpt-5"]
                result = await runner.run(input=prompt, model=models, tools=[], mcp_servers=[], stream=False)
                raw = result.final_output.strip()
                try:
                    return json.loads(raw)
                except Exception:
                    start = raw.find('{')
                    end = raw.rfind('}')
                    if start != -1 and end != -1:
                        snippet = raw[start:end+1]
                        return json.loads(snippet)
                    return None

            return asyncio.run(_run())
        except Exception as e:
            print(f"Dedalus host advice error: {e}")
            return None

    def _dedalus_host_chat(self, properties: List[Dict[str, Any]], question: str, session_id: str) -> str:
        try:
            compact = [
                {
                    "title": p.get("title"),
                    "city": p.get("location", {}).get("city"),
                    "price": p.get("pricePerNight"),
                    "amenities": p.get("amenities", [])[:10]
                }
                for p in properties[:8]
            ]
            history = self._get_host_chat_history(session_id, limit=12)
            hist_lines = [f"{h['role']}: {h['message']}" for h in history]
            hist_block = "\n".join(hist_lines) if hist_lines else "(no previous messages)"
            prompt = (
                "You are a host advisory AI helping improve listing performance. Be concise, actionable, and data-grounded. If asked about pricing, you may reference the pricing agent heuristic benefits."
                f"\nPROPERTIES={compact}\nSESSION_HISTORY=\n{hist_block}\nQUESTION={question}\nAnswer:"
            )

            return self._run_dedalus(prompt)
        except Exception as e:
            print(f"Dedalus host chat error: {e}")
            return self._fallback_host_chat(question)
    
    # ==================== DEDALUS RUNNERS ====================
    
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
    
    # ==================== TOOLS FOR DEDALUS ====================
    
    def _tool_get_property(self, property_id: str) -> Dict[str, Any]:
        """Get property details by ID."""
        try:
            prop = self._get_property(property_id)
            if not prop:
                return {"success": False, "error": f"Property {property_id} not found"}
            prop_dict = {k: (str(v) if isinstance(v, ObjectId) else v) for k, v in prop.items()}
            return {"success": True, "property": prop_dict}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _tool_get_booking(self, booking_id: str) -> Dict[str, Any]:
        """Get booking details by ID."""
        try:
            booking = self._get_booking(booking_id)
            if not booking:
                return {"success": False, "error": f"Booking {booking_id} not found"}
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
        """List bookings, optionally filtered by property_id."""
        try:
            query = {}
            if property_id:
                query["property"] = ObjectId(property_id)
            
            bookings = []
            for b in self.db.booking.find(query).sort("createdAt", -1).limit(limit):
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
        """Check if property is available for given dates."""
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
        """Get chat history for a booking."""
        try:
            history = self._get_booking_chat_history(booking_id, limit)
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

    def _tool_list_properties(self, city: Optional[str] = None, limit: int = 25) -> Dict[str, Any]:
        """List properties, optionally filtered by city."""
        try:
            query = {}
            if city:
                query["location.city"] = city
            
            properties = []
            for p in self.db.property.find(query).sort("createdAt", -1).limit(limit):
                prop_dict = {}
                for k, v in p.items():
                    if isinstance(v, ObjectId):
                        prop_dict[k] = str(v)
                    elif isinstance(v, datetime):
                        prop_dict[k] = v.isoformat()
                    else:
                        prop_dict[k] = v
                properties.append(prop_dict)
            return {"success": True, "properties": properties, "count": len(properties)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _tool_search_properties_by_location(self, city: Optional[str] = None, country: Optional[str] = None, limit: int = 25) -> Dict[str, Any]:
        """Search properties by location (city and/or country).
        
        Args:
            city: City name to filter by (case-insensitive partial match)
            country: Country name to filter by (case-insensitive partial match)
            limit: Maximum number of results to return (default 25, max 100)
        """
        try:
            query = {}
            if city:
                query["location.city"] = {"$regex": city, "$options": "i"}
            if country:
                query["location.country"] = {"$regex": country, "$options": "i"}
            
            limit = min(max(1, limit), 100)
            
            properties = []
            for p in self.db.property.find(query).sort("createdAt", -1).limit(limit):
                prop_dict = {}
                for k, v in p.items():
                    if isinstance(v, ObjectId):
                        prop_dict[k] = str(v)
                    elif isinstance(v, datetime):
                        prop_dict[k] = v.isoformat()
                    else:
                        prop_dict[k] = v
                properties.append(prop_dict)
            return {"success": True, "properties": properties, "count": len(properties)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _tool_search_properties_by_amenities(self, amenities: List[str], require_all: bool = True, limit: int = 25) -> Dict[str, Any]:
        """Search properties by amenities.
        
        Args:
            amenities: List of amenity names to search for (e.g., ["wifi", "ac", "parking"])
            require_all: If True, property must have all amenities. If False, property must have at least one (default True)
            limit: Maximum number of results to return (default 25, max 100)
        """
        try:
            if not amenities:
                return {"success": False, "error": "At least one amenity must be provided"}
            
            query = {}
            if require_all:
                # Property must have all specified amenities
                query["amenities"] = {"$all": amenities}
            else:
                # Property must have at least one of the specified amenities
                query["amenities"] = {"$in": amenities}
            
            limit = min(max(1, limit), 100)
            
            properties = []
            for p in self.db.property.find(query).sort("createdAt", -1).limit(limit):
                prop_dict = {}
                for k, v in p.items():
                    if isinstance(v, ObjectId):
                        prop_dict[k] = str(v)
                    elif isinstance(v, datetime):
                        prop_dict[k] = v.isoformat()
                    else:
                        prop_dict[k] = v
                properties.append(prop_dict)
            return {"success": True, "properties": properties, "count": len(properties)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _tool_search_properties_by_rooms(self, min_bedrooms: Optional[int] = None, min_bathrooms: Optional[int] = None, limit: int = 25) -> Dict[str, Any]:
        """Search properties by room requirements.
        
        Args:
            min_bedrooms: Minimum number of bedrooms required
            min_bathrooms: Minimum number of bathrooms required
            limit: Maximum number of results to return (default 25, max 100)
        """
        try:
            query = {}
            
            # Build aggregation pipeline to filter by room counts
            pipeline = []
            
            # Add room count filters using aggregation
            if min_bedrooms is not None or min_bathrooms is not None:
                # Use aggregation to calculate room counts and filter
                pipeline.append({"$match": {}})  # Start with all properties
                
                # Add fields to calculate room counts
                pipeline.append({
                    "$addFields": {
                        "bedroomCount": {
                            "$sum": {
                                "$map": {
                                    "input": {"$filter": {"input": "$rooms", "as": "room", "cond": {"$eq": ["$$room.type", "bedroom"]}}},
                                    "as": "bedroom",
                                    "in": "$$bedroom.count"
                                }
                            }
                        },
                        "bathroomCount": {
                            "$sum": {
                                "$map": {
                                    "input": {"$filter": {"input": "$rooms", "as": "room", "cond": {"$eq": ["$$room.type", "bathroom"]}}},
                                    "as": "bathroom",
                                    "in": "$$bathroom.count"
                                }
                            }
                        }
                    }
                })
                
                # Filter by room counts
                match_conditions = {}
                if min_bedrooms is not None:
                    match_conditions["bedroomCount"] = {"$gte": min_bedrooms}
                if min_bathrooms is not None:
                    match_conditions["bathroomCount"] = {"$gte": min_bathrooms}
                
                if match_conditions:
                    pipeline.append({"$match": match_conditions})
                
                # Sort and limit
                pipeline.append({"$sort": {"createdAt": -1}})
                pipeline.append({"$limit": min(max(1, limit), 100)})
                
                properties = []
                for p in self.db.property.aggregate(pipeline):
                    prop_dict = {}
                    for k, v in p.items():
                        if isinstance(v, ObjectId):
                            prop_dict[k] = str(v)
                        elif isinstance(v, datetime):
                            prop_dict[k] = v.isoformat()
                        else:
                            prop_dict[k] = v
                    properties.append(prop_dict)
                return {"success": True, "properties": properties, "count": len(properties)}
            else:
                # No room filters, just return all properties
                limit = min(max(1, limit), 100)
                properties = []
                for p in self.db.property.find(query).sort("createdAt", -1).limit(limit):
                    prop_dict = {}
                    for k, v in p.items():
                        if isinstance(v, ObjectId):
                            prop_dict[k] = str(v)
                        elif isinstance(v, datetime):
                            prop_dict[k] = v.isoformat()
                        else:
                            prop_dict[k] = v
                    properties.append(prop_dict)
                return {"success": True, "properties": properties, "count": len(properties)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _tool_search_properties_by_price(self, min_price: Optional[float] = None, max_price: Optional[float] = None, limit: int = 25) -> Dict[str, Any]:
        """Search properties by price range.
        
        Args:
            min_price: Minimum price per night (inclusive)
            max_price: Maximum price per night (inclusive)
            limit: Maximum number of results to return (default 25, max 100)
        """
        try:
            query = {}
            
            # Build price query - check both pricePerNight and dynamicPrice
            if min_price is not None or max_price is not None:
                # Create conditions for price range
                price_cond = {}
                if min_price is not None:
                    price_cond["$gte"] = min_price
                if max_price is not None:
                    price_cond["$lte"] = max_price
                
                # Property must have pricePerNight within range, OR dynamicPrice within range (if it exists)
                query["$or"] = [
                    {"pricePerNight": price_cond},
                    {"$and": [
                        {"dynamicPrice": {"$exists": True, "$ne": None}},
                        {"dynamicPrice": price_cond}
                    ]}
                ]
            
            limit = min(max(1, limit), 100)
            
            properties = []
            for p in self.db.property.find(query).sort("createdAt", -1).limit(limit):
                prop_dict = {}
                for k, v in p.items():
                    if isinstance(v, ObjectId):
                        prop_dict[k] = str(v)
                    elif isinstance(v, datetime):
                        prop_dict[k] = v.isoformat()
                    else:
                        prop_dict[k] = v
                properties.append(prop_dict)
            return {"success": True, "properties": properties, "count": len(properties)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _tool_search_properties(self, city: Optional[str] = None, country: Optional[str] = None, 
                                amenities: Optional[List[str]] = None, require_all_amenities: bool = True,
                                min_bedrooms: Optional[int] = None, min_bathrooms: Optional[int] = None,
                                min_price: Optional[float] = None, max_price: Optional[float] = None,
                                limit: int = 25) -> Dict[str, Any]:
        """Comprehensive property search combining multiple filters.
        
        Args:
            city: City name to filter by (case-insensitive partial match)
            country: Country name to filter by (case-insensitive partial match)
            amenities: List of amenities to filter by
            require_all_amenities: If True, property must have all amenities (default True)
            min_bedrooms: Minimum number of bedrooms required
            min_bathrooms: Minimum number of bathrooms required
            min_price: Minimum price per night (inclusive)
            max_price: Maximum price per night (inclusive)
            limit: Maximum number of results to return (default 25, max 100)
        """
        try:
            # Build base query
            query = {}
            
            # Location filters
            if city:
                query["location.city"] = {"$regex": city, "$options": "i"}
            if country:
                query["location.country"] = {"$regex": country, "$options": "i"}
            
            # Amenities filter
            if amenities:
                if require_all_amenities:
                    query["amenities"] = {"$all": amenities}
                else:
                    query["amenities"] = {"$in": amenities}
            
            # Price filter
            if min_price is not None or max_price is not None:
                price_cond = {}
                if min_price is not None:
                    price_cond["$gte"] = min_price
                if max_price is not None:
                    price_cond["$lte"] = max_price
                
                # Build price filter that checks both pricePerNight and dynamicPrice
                price_filter = {
                    "$or": [
                        {"pricePerNight": price_cond},
                        {"$and": [
                            {"dynamicPrice": {"$exists": True, "$ne": None}},
                            {"dynamicPrice": price_cond}
                        ]}
                    ]
                }
                
                # Combine price filter with existing query conditions
                if query:
                    # If we already have query conditions, combine them with $and
                    query = {"$and": [query, price_filter]}
                else:
                    # If no other conditions, use price filter directly
                    query = price_filter
            
            # Room filters require aggregation
            if min_bedrooms is not None or min_bathrooms is not None:
                # Use aggregation pipeline
                pipeline = []
                pipeline.append({"$match": query})
                
                # Add fields to calculate room counts
                pipeline.append({
                    "$addFields": {
                        "bedroomCount": {
                            "$sum": {
                                "$map": {
                                    "input": {"$filter": {"input": "$rooms", "as": "room", "cond": {"$eq": ["$$room.type", "bedroom"]}}},
                                    "as": "bedroom",
                                    "in": "$$bedroom.count"
                                }
                            }
                        },
                        "bathroomCount": {
                            "$sum": {
                                "$map": {
                                    "input": {"$filter": {"input": "$rooms", "as": "room", "cond": {"$eq": ["$$room.type", "bathroom"]}}},
                                    "as": "bathroom",
                                    "in": "$$bathroom.count"
                                }
                            }
                        }
                    }
                })
                
                # Filter by room counts
                match_conditions = {}
                if min_bedrooms is not None:
                    match_conditions["bedroomCount"] = {"$gte": min_bedrooms}
                if min_bathrooms is not None:
                    match_conditions["bathroomCount"] = {"$gte": min_bathrooms}
                
                if match_conditions:
                    pipeline.append({"$match": match_conditions})
                
                # Sort and limit
                pipeline.append({"$sort": {"createdAt": -1}})
                pipeline.append({"$limit": min(max(1, limit), 100)})
                
                properties = []
                for p in self.db.property.aggregate(pipeline):
                    prop_dict = {}
                    for k, v in p.items():
                        if isinstance(v, ObjectId):
                            prop_dict[k] = str(v)
                        elif isinstance(v, datetime):
                            prop_dict[k] = v.isoformat()
                        else:
                            prop_dict[k] = v
                    properties.append(prop_dict)
                return {"success": True, "properties": properties, "count": len(properties)}
            else:
                # No room filters, use simple find
                limit = min(max(1, limit), 100)
                properties = []
                for p in self.db.property.find(query).sort("createdAt", -1).limit(limit):
                    prop_dict = {}
                    for k, v in p.items():
                        if isinstance(v, ObjectId):
                            prop_dict[k] = str(v)
                        elif isinstance(v, datetime):
                            prop_dict[k] = v.isoformat()
                        else:
                            prop_dict[k] = v
                    properties.append(prop_dict)
                return {"success": True, "properties": properties, "count": len(properties)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _tool_get_property_chat_history(self, property_id: str, session_id: str, limit: int = 12) -> Dict[str, Any]:
        """Get chat history for a property."""
        try:
            history = self._get_property_chat_history(property_id, session_id, limit)
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

    def _tool_get_host_properties(self, host_id: str) -> Dict[str, Any]:
        """Get all properties for a host."""
        try:
            properties = self._get_host_properties(host_id)
            prop_list = []
            for p in properties:
                prop_dict = {}
                for k, v in p.items():
                    if isinstance(v, ObjectId):
                        prop_dict[k] = str(v)
                    elif isinstance(v, datetime):
                        prop_dict[k] = v.isoformat()
                    else:
                        prop_dict[k] = v
                prop_list.append(prop_dict)
            return {"success": True, "properties": prop_list, "count": len(prop_list)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def _tool_suggest_price(self, property_id: str) -> Dict[str, Any]:
        """Suggest optimal price for a property."""
        return self.suggest_price(property_id)
    
    # ==================== ACTION TOOLS FOR DEDALUS ====================
    
    def _tool_create_quote(self, property_id: str, start_date: str, end_date: str, guest_id: Optional[str] = None) -> Dict[str, Any]:
        """Create a booking quote for a property. Use when user wants to book or get a quote.
        
        Args:
            property_id: Property ID (24 char hex string)
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format
            guest_id: Optional guest ID
        """
        return self.create_quote(property_id, guest_id, start_date, end_date)
    
    def _tool_confirm_booking(self, booking_id: str) -> Dict[str, Any]:
        """Confirm a booking. Use when user wants to confirm their booking.
        
        Args:
            booking_id: Booking ID (24 char hex string)
        """
        return self.confirm_booking(booking_id)
    
    def _tool_booking_chat(self, booking_id: str, message: str) -> Dict[str, Any]:
        """Chat about an existing booking. Use when user asks questions about their booking.
        
        Args:
            booking_id: Booking ID (24 char hex string)
            message: User's message/question
        """
        return self.booking_chat(booking_id, message)
    
    def _tool_property_chat(self, property_id: str, message: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        """Answer questions about a property. Use when user asks about a specific property.
        
        Args:
            property_id: Property ID (24 char hex string)
            message: User's question about the property
            session_id: Optional session ID for maintaining conversation context
        """
        if session_id is None:
            session_id = str(property_id)
        return self.property_chat(property_id, message, session_id=session_id)
    
    def _tool_get_host_advice(self, host_id: str, focus: Optional[str] = None) -> Dict[str, Any]:
        """Get advice for a host. Use when a host asks for advice about their listings.
        
        Args:
            host_id: Host ID (24 char hex string)
            focus: Optional focus area: listing_quality, pricing_strategy, guest_experience, occupancy_growth
        """
        return self.get_host_advice(host_id, focus)
    
    def _tool_host_chat(self, host_id: str, message: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        """Chat with a host. Use when a host asks questions about their properties or needs guidance.
        
        Args:
            host_id: Host ID (24 char hex string)
            message: Host's question
            session_id: Optional session ID for maintaining conversation context
        """
        if session_id is None:
            session_id = str(host_id)
        return self.host_chat(host_id, message, session_id=session_id)
    
    # ==================== HOST METRICS & COMPARISON ====================
    
    def _aggregate_host_metrics(self, properties: List[Dict[str, Any]], window_start: datetime) -> Dict[str, Any]:
        booking_counts = {}
        total_nights = 0
        total_bookings = 0
        for p in properties:
            pid = p["_id"]
            bookings = list(self.db.booking.find({
                "property": pid,
                "status": "confirmed",
                "startDate": {"$gte": window_start}
            }))
            count = len(bookings)
            booking_counts[str(pid)] = count
            total_bookings += count
            for b in bookings:
                nights = (b.get("endDate") - b.get("startDate")).days if b.get("endDate") and b.get("startDate") else 0
                total_nights += max(nights, 0)

        avg_price = self._avg([p.get("pricePerNight", 0) for p in properties])
        amenities_freq = self._amenities_frequency(properties)

        return {
            "property_count": len(properties),
            "avg_price": avg_price,
            "total_bookings": total_bookings,
            "total_nights": total_nights,
            "booking_counts": booking_counts,
            "amenities_freq": amenities_freq,
        }

    def _get_top_performer_sample(self, properties: List[Dict[str, Any]], window_start: datetime, sample_size: int = 5) -> List[Dict[str, Any]]:
        cities = {p.get("location", {}).get("city") for p in properties if p.get("location")}
        cur = self.db.property.find({
            "location.city": {"$in": list(cities)},
            "host": {"$ne": properties[0]["host"]}
        })
        comparison_props = []
        for p in cur:
            booking_count = self.db.booking.count_documents({
                "property": p["_id"],
                "status": "confirmed",
                "startDate": {"$gte": window_start}
            })
            p["_recentBookingCount"] = booking_count
            comparison_props.append(p)

        top = sorted(comparison_props, key=lambda x: x.get("_recentBookingCount", 0), reverse=True)[:sample_size]
        slim = [
            {
                "title": t.get("title"),
                "city": t.get("location", {}).get("city"),
                "pricePerNight": t.get("pricePerNight"),
                "amenities": t.get("amenities", [])[:15],
                "recentBookings": t.get("_recentBookingCount", 0)
            }
            for t in top
        ]
        return slim
    
    # ==================== FORMATTING HELPERS ====================
    
    def _format_property_context(self, prop: Dict[str, Any]) -> str:
        loc = prop.get("location", {})
        rooms = prop.get("rooms", [])
        amenities = prop.get("amenities", [])
        parts = [
            f"Title: {prop.get('title', 'N/A')}",
            f"Description: {prop.get('description', 'N/A')}",
            f"Location: {loc.get('address', '')}, {loc.get('city', '')}, {loc.get('country', '')}",
            f"Coordinates: {loc.get('coordinates', {})}",
            f"Amenities: {', '.join(amenities) if amenities else 'None'}",
            f"Rooms: {json.dumps(rooms)}",
            f"Base Price/Night: {prop.get('pricePerNight', 'N/A')}"
        ]
        if "dynamicPrice" in prop and prop["dynamicPrice"] is not None:
            parts.append(f"Dynamic Price/Night: {prop['dynamicPrice']}")
        return "\n".join(parts)

    def _format_history_snippet(self, history: List[Dict[str, Any]]) -> str:
        lines = []
        for h in history:
            ts = h.get("createdAt")
            ts_str = ts.isoformat() if isinstance(ts, datetime) else str(ts)
            lines.append(f"{h.get('role','user').title()} ({ts_str}): {h.get('message','')}")
        return "\n".join(lines)
    
    # ==================== FALLBACKS ====================
    
    def _fallback_booking_message(self, prop: Dict[str, Any], booking: Dict[str, Any], price_result: Dict[str, Any]) -> str:
        return (
            f"Quote for {prop.get('title')}: {booking['nights']} nights from {booking['startDate'].date()} to {booking['endDate'].date()} at ${booking['nightlyPrice']:.2f}/night. Total ${booking['totalPrice']:.2f}."
            " Amenities: " + ", ".join(prop.get('amenities', [])[:5]) + ". Reply with questions or 'confirm' to proceed."
        )

    def _fallback_booking_followup(self, user_message: str) -> str:
        lower = user_message.lower()
        if "confirm" in lower:
            return "To confirm, call the confirmation endpoint. Looking forward to hosting you!"
        return "Thanks for your message. Let me know if you'd like to confirm or adjust dates."

    def _fallback_host_advice(self, host_metrics: Dict[str, Any], comparison: List[Dict[str, Any]], focus: Optional[str]) -> Dict[str, Any]:
        recs: List[Dict[str, Any]] = []
        amenities_top = {a for c in comparison for a in c.get("amenities", [])}
        missing_popular = sorted(list(amenities_top - set(host_metrics.get("amenities_freq", {}).keys())))[:5]

        def add(cat: str, advice: str, priority: str = "medium"):
            if not focus or focus == cat:
                recs.append({"category": cat, "advice": advice, "priority": priority})

        add("listing_quality", f"Add high-demand amenities: {', '.join(missing_popular)}" if missing_popular else "Review photos for quality refresh", "high")
        add("pricing_strategy", "Run dynamic pricing weekly; compare against city median via pricing agent", "medium")
        add("guest_experience", "Automate messaging templates for check-in, local tips, and mid-stay feedback", "medium")
        add("occupancy_growth", "Experiment with 10% discount for stays >7 nights to boost shoulder season occupancy", "low")

        quick_wins = [r["advice"] for r in recs[:3]]
        return {
            "summary": "Heuristic advice generated without AI model.",
            "recommendations": recs,
            "quick_wins": quick_wins,
            "metrics_snapshot": host_metrics,
            "comparison_sample": comparison,
        }

    def _fallback_host_chat(self, question: str) -> str:
        lower = question.lower()
        if "price" in lower or "pricing" in lower:
            return "Consider reviewing dynamic pricing weekly and aligning with comparable median while highlighting unique amenities."
        if "amenit" in lower:
            return "Focus on top searched amenities: fast wifi, dedicated workspace, smart TV, basic kitchen staples."
        if "occupancy" in lower or "booking" in lower:
            return "Try length-of-stay discounts and optimize listing title with key amenities & location hooks."
        return "Optimize photos, keep response time <1hr, and gather mid-stay feedback to surface improvement areas."
    
    # ==================== UTILITIES ====================
    
    @staticmethod
    def _avg(values: List[float]) -> float:
        vals = [v for v in values if isinstance(v, (int, float))]
        return round(sum(vals) / len(vals), 2) if vals else 0.0

    @staticmethod
    def _amenities_frequency(properties: List[Dict[str, Any]]) -> Dict[str, int]:
        freq: Dict[str, int] = {}
        for p in properties:
            for a in p.get("amenities", []):
                freq[a] = freq.get(a, 0) + 1
        return freq

