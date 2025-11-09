from __future__ import annotations
"""HostCommunityAgent: Provides AI-driven guidance for hosts based on portfolio & top performers.

Features:
 - Aggregate a host's properties & recent booking performance (last 90 days)
 - Compare against top performing properties in same cities (booking volume & price)
 - Generate structured JSON advice via Dedalus (if API key present)
 - Fallback heuristic advice when Dedalus unavailable
 - Lightweight chat interface for ongoing host Q&A sessions

Public Methods:
 - get_host_advice(host_id: str, focus: Optional[str] = None) -> Dict[str, Any]
 - ask(host_id: str, question: str, session_id: Optional[str] = None) -> Dict[str, Any]

Focus values (optional filter): listing_quality | pricing_strategy | guest_experience | occupancy_growth
"""

import os
import asyncio
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from bson.objectid import ObjectId
from dotenv import load_dotenv
from dedalus_labs import AsyncDedalus, DedalusRunner

from config.db import get_db
from ai_agents.pricing_agent import PricingAgent

load_dotenv()

DEDALUS_API_KEY = os.getenv("DEDALUS_API_KEY")
DEDALUS_MODEL = os.getenv("DEDALUS_MODEL", "openai/gpt-5")


class HostCommunityAgent:
    """AI advisor for hosts leveraging patterns from top-performing listings."""

    def __init__(self) -> None:
        self.db = get_db()
        self._has_dedalus = bool(DEDALUS_API_KEY)
        self.pricing_agent = PricingAgent()

    # -------------------- Public API -------------------- #
    def get_host_advice(self, host_id: str, focus: Optional[str] = None) -> Dict[str, Any]:
        """Return structured advice for a host.

        Args:
            host_id: Host identifier string
            focus: Optional category to filter recommendations
        Returns: Dict with success flag, advice data or error.
        """
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
                    # Ensure filtering if focus specified but model returned arbitrary items
                    if focus:
                        ai_json["recommendations"] = [r for r in ai_json.get("recommendations", []) if r.get("category") == focus]
                    return {"success": True, "data": ai_json, "source": "dedalus"}

            # Fallback path
            fallback = self._fallback_host_advice(host_metrics, comparison, focus)
            return {"success": True, "data": fallback, "source": "fallback"}
        except Exception as e:
            return {"success": False, "error": str(e)}

    def ask(self, host_id: str, question: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        """Chat-style Q&A for a host seeking guidance."""
        try:
            properties = self._get_host_properties(host_id)
            if not properties:
                return {"success": False, "error": "Host has no properties"}

            session = session_id or host_id
            self._save_chat(session, role="user", message=question, host_id=host_id)
            response = self._dedalus_chat(properties, question, session) if self._has_dedalus else self._fallback_chat(question)
            self._save_chat(session, role="assistant", message=response, host_id=host_id)
            return {"success": True, "message": response, "session_id": session}
        except Exception as e:
            return {"success": False, "error": str(e)}

    # -------------------- Data Gathering -------------------- #
    def _get_host_properties(self, host_id: str) -> List[Dict[str, Any]]:
        cur = self.db.property.find({"host": ObjectId(host_id)})
        return list(cur)

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
        # Choose same cities as host's properties
        cities = {p.get("location", {}).get("city") for p in properties if p.get("location")}
        # Filter properties from those cities excluding host's own
        cur = self.db.property.find({
            "location.city": {"$in": list(cities)},
            "host": {"$ne": properties[0]["host"]}
        })
        comparison_props = []
        for p in cur:
            # Count bookings as performance proxy
            booking_count = self.db.booking.count_documents({
                "property": p["_id"],
                "status": "confirmed",
                "startDate": {"$gte": window_start}
            })
            p["_recentBookingCount"] = booking_count
            comparison_props.append(p)

        # Sort by booking count desc and take sample
        top = sorted(comparison_props, key=lambda x: x.get("_recentBookingCount", 0), reverse=True)[:sample_size]
        # Minimal fields for context
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

    # -------------------- Dedalus Integrations -------------------- #
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
                import json
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

    def _dedalus_chat(self, properties: List[Dict[str, Any]], question: str, session_id: str) -> str:
        try:
            # Compact property list for context
            compact = [
                {
                    "title": p.get("title"),
                    "city": p.get("location", {}).get("city"),
                    "price": p.get("pricePerNight"),
                    "amenities": p.get("amenities", [])[:10]
                }
                for p in properties[:8]
            ]
            history = self._get_chat_history(session_id, limit=12)
            hist_lines = [f"{h['role']}: {h['message']}" for h in history]
            hist_block = "\n".join(hist_lines) if hist_lines else "(no previous messages)"
            prompt = (
                "You are a host advisory AI helping improve listing performance. Be concise, actionable, and data-grounded. If asked about pricing, you may reference the pricing agent heuristic benefits."
                f"\nPROPERTIES={compact}\nSESSION_HISTORY=\n{hist_block}\nQUESTION={question}\nAnswer:"
            )

            async def _run():
                client = AsyncDedalus(api_key=DEDALUS_API_KEY)
                runner = DedalusRunner(client)
                models = [DEDALUS_MODEL] if DEDALUS_MODEL else ["openai/gpt-5"]
                result = await runner.run(input=prompt, model=models, tools=[], mcp_servers=[], stream=False)
                return result.final_output.strip()

            return asyncio.run(_run())
        except Exception as e:
            print(f"Dedalus host chat error: {e}")
            return self._fallback_chat(question)

    # -------------------- Fallbacks -------------------- #
    def _fallback_host_advice(self, host_metrics: Dict[str, Any], comparison: List[Dict[str, Any]], focus: Optional[str]) -> Dict[str, Any]:
        # Simple heuristic recommendations
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

    def _fallback_chat(self, question: str) -> str:
        lower = question.lower()
        if "price" in lower or "pricing" in lower:
            return "Consider reviewing dynamic pricing weekly and aligning with comparable median while highlighting unique amenities."
        if "amenit" in lower:
            return "Focus on top searched amenities: fast wifi, dedicated workspace, smart TV, basic kitchen staples."
        if "occupancy" in lower or "booking" in lower:
            return "Try length-of-stay discounts and optimize listing title with key amenities & location hooks."
        return "Optimize photos, keep response time <1hr, and gather mid-stay feedback to surface improvement areas."

    # -------------------- Persistence -------------------- #
    def _save_chat(self, session_id: str, role: str, message: str, host_id: str) -> None:
        self.db.HostCommunityChatHistory.insert_one({
            "sessionId": session_id,
            "host": ObjectId(host_id),
            "role": role,
            "message": message,
            "createdAt": datetime.utcnow(),
        })

    def _get_chat_history(self, session_id: str, limit: int = 12) -> List[Dict[str, Any]]:
        cur = self.db.HostCommunityChatHistory.find({"sessionId": session_id}).sort("createdAt", 1).limit(limit)
        return list(cur)

    # -------------------- Utilities -------------------- #
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


if __name__ == "__main__":
    pass
