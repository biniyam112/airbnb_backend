from typing import Dict, List, Optional
from datetime import datetime
from bson import ObjectId
from config.db import get_db
import os
from dotenv import load_dotenv
import requests
from time import sleep

# Load environment variables
load_dotenv()

DEDALUS_API_KEY = os.getenv('DEDALUS_API_KEY')
DEDALUS_MCP_ENDPOINT = os.getenv('DEDALUS_MCP_ENDPOINT', 'https://mcp.dedalus.ai/v1')

class HostChatAgent:
    def __init__(self):
        """Initialize the chat agent with database connection and Dedalus MCP client"""
        self.db = get_db()
        if not DEDALUS_API_KEY:
            raise ValueError("DEDALUS_API_KEY environment variable is required")
        self.headers = {
            'Authorization': f'Bearer {DEDALUS_API_KEY}',
            'Content-Type': 'application/json'
        }

    async def process_message(self, chat_id: str, message: str, sender_type: str) -> dict:
        """
        Process an incoming chat message and generate a response using Dedalus MCP.
        
        Args:
            chat_id: The ID of the chat thread
            message: The incoming message text
            sender_type: Type of sender ('guest' or 'system')
        """
        try:
            # Get chat context
            chat_context = await self._get_chat_context(chat_id)
            property_id = chat_context.get('property_id')
            
            if not property_id:
                raise ValueError("Property ID not found in chat context")
            
            # Get property details for context
            print("Fetching property data for ID:", )
            property_data = self.db.property.find_one({"_id": ObjectId(property_id)})
            if not property_data:
                raise ValueError(f"Property {property_id} not found")
            
            # Format request for Dedalus MCP
            response = await self._get_mcp_response(
                message=message,
                chat_context=chat_context,
                property_data=property_data,
                sender_type=sender_type
            )
            
            # Save the message and response to chat history
            await self._save_chat_history(
                chat_id=chat_id,
                guest_message=message,
                host_response=response['message'],
                context_used=response.get('context_used', [])
            )
            
            return {
                "success": True,
                "response": response['message'],
                "sentiment": response.get('sentiment'),
                "suggested_actions": response.get('suggested_actions', [])
            }
            
        except Exception as e:
            return {
                "success": False,
                "error": str(e)
            }

    async def _get_chat_context(self, chat_id: str) -> Dict:
        """Retrieve the context of the chat thread"""
        chat_thread = self.db.ChatThreads.find_one({"_id": ObjectId(chat_id)})
        if not chat_thread:
            raise ValueError("Chat thread not found")
            
        # Get recent chat history
        history = list(self.db.ChatHistory.find(
            {"chat_id": ObjectId(chat_id)}
        ).sort("timestamp", -1).limit(10))
        
        return {
            "property_id": str(chat_thread.get("property")),
            "guest_id": str(chat_thread.get("guest")),
            "booking_id": str(chat_thread.get("booking")) if chat_thread.get("booking") else None,
            "chat_history": history,
            "thread_status": chat_thread.get("status", "active")
        }

    async def _get_mcp_response(self, message: str, chat_context: Dict, 
                              property_data: Dict, sender_type: str) -> Dict:
        """Get response from Dedalus MCP"""
        try:
            payload = {
                "input": {
                    "message": message,
                    "sender_type": sender_type,
                    "property_context": {
                        "id": str(property_data["_id"]),
                        "title": property_data["title"],
                        "location": property_data["location"],
                        "price_per_night": property_data["pricePerNight"],
                        "amenities": property_data["amenities"]
                    },
                    "chat_context": {
                        "thread_id": chat_context.get("chat_id"),
                        "guest_id": chat_context.get("guest_id"),
                        "booking_id": chat_context.get("booking_id"),
                        "thread_status": chat_context.get("thread_status"),
                        "recent_messages": [
                            {
                                "sender": msg["sender_type"],
                                "message": msg["message"],
                                "timestamp": msg["timestamp"].isoformat()
                            }
                            for msg in chat_context.get("chat_history", [])
                        ]
                    }
                },
                "config": {
                    "response_type": "host_chat",
                    "tone": "professional_friendly",
                    "max_tokens": 300
                }
            }
            
            response = requests.post(
                f"{DEDALUS_MCP_ENDPOINT}/chat/respond",
                headers=self.headers,
                json=payload
            )
            
            if response.status_code == 202:
                # Handle async response
                return await self._poll_chat_response(response.json()['task_id'])
            
            response.raise_for_status()
            return response.json()
            
        except Exception as e:
            print(f"Dedalus MCP Chat Error: {str(e)}")
            return self._get_fallback_response(message)

    async def _poll_chat_response(self, task_id: str, max_attempts: int = 10) -> Dict:
        """Poll for async chat response"""
        attempt = 0
        while attempt < max_attempts:
            try:
                response = requests.get(
                    f"{DEDALUS_MCP_ENDPOINT}/tasks/{task_id}",
                    headers=self.headers
                )
                
                if response.status_code == 200:
                    result = response.json()
                    if result.get('status') == 'completed':
                        return result.get('data', self._get_fallback_response(""))
                
                await sleep(1)  # Wait before next attempt
                attempt += 1
                
            except Exception:
                attempt += 1
        
        return self._get_fallback_response("")

    def _get_fallback_response(self, message: str) -> Dict:
        """Generate a fallback response when MCP is unavailable"""
        return {
            "message": "I apologize, but I'm having trouble processing your request right now. "
                      "Please try again in a few moments or contact our support team if the issue persists.",
            "sentiment": "neutral",
            "suggested_actions": ["contact_support"],
            "context_used": ["fallback_response"]
        }

    async def _save_chat_history(self, chat_id: str, guest_message: str, 
                               host_response: str, context_used: List[str]):
        """Save chat messages to history"""
        # Save guest message
        self.db.ChatHistory.insert_one({
            "chat_id": ObjectId(chat_id),
            "message": guest_message,
            "sender_type": "guest",
            "timestamp": datetime.utcnow(),
            "is_ai_response": False
        })
        
        # Save AI host response
        self.db.ChatHistory.insert_one({
            "chat_id": ObjectId(chat_id),
            "message": host_response,
            "sender_type": "host",
            "timestamp": datetime.utcnow(),
            "is_ai_response": True,
            "context_used": context_used
        })