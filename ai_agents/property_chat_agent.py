from __future__ import annotations
from typing import Optional, List, Dict, Any
import os
import json
import asyncio
from datetime import datetime
from bson.objectid import ObjectId
from dotenv import load_dotenv
from dedalus_labs import AsyncDedalus, DedalusRunner
from config.db import get_db

load_dotenv()

DEDALUS_API_KEY = os.getenv("DEDALUS_API_KEY")
DEDALUS_MODEL = os.getenv("DEDALUS_MODEL", "openai/gpt-5")

class PropertyChatAgent:
    """Dedalus-powered chat agent that answers questions about a specific property."""

    def __init__(self) -> None:
        self.db = get_db()
        self._has_dedalus = bool(DEDALUS_API_KEY)

    def ask(self, property_id: str, user_message: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        """Ask a question about a property and get a Dedalus-generated answer.

        Returns a dict: { success, message, session_id }
        """
        try:
            prop = self._get_property(property_id)
            if not prop:
                return {"success": False, "error": "Property not found"}

            session = session_id or str(property_id)
            # Persist user message
            self._save_msg(property_id, session, role="user", message=user_message)

            assistant_message = self._dedalus_answer(prop, user_message, session)
            if not assistant_message:
                assistant_message = (
                    "I'm unable to retrieve the information right now. "
                    "Please try again later or contact the host."
                )

            # Persist assistant response
            self._save_msg(property_id, session, role="assistant", message=assistant_message)

            return {
                "success": True,
                "message": assistant_message,
                "session_id": session
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    # ---------------- Internal helpers ---------------- #
    def _get_property(self, property_id: str) -> Optional[Dict[str, Any]]:
        return self.db.property.find_one({"_id": ObjectId(property_id)})

    def _get_history(self, property_id: str, session_id: str, limit: int = 12) -> List[Dict[str, Any]]:
        cur = self.db.propertyChatHistory.find({
            "property": ObjectId(property_id),
            "sessionId": session_id
        }).sort("createdAt", 1).limit(limit)
        return list(cur)

    def _format_context(self, prop: Dict[str, Any]) -> str:
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

    def _dedalus_answer(self, prop: Dict[str, Any], user_message: str, session_id: str) -> Optional[str]:
        if not self._has_dedalus:
            return None
        try:
            context = self._format_context(prop)
            history = self._get_history(str(prop['_id']), session_id, limit=12)
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

            async def _run():
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

            return asyncio.run(_run())
        except Exception as e:
            print(f"Dedalus property chat error: {e}")
            return None

    def _save_msg(self, property_id: str, session_id: str, role: str, message: str) -> None:
        self.db.propertyChatHistory.insert_one({
            "property": ObjectId(property_id),
            "sessionId": session_id,
            "role": role,
            "message": message,
            "createdAt": datetime.utcnow(),
        })
