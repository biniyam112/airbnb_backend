from flask import Blueprint, request, jsonify
from bson.objectid import ObjectId
from ai_agents.chat_agent import HostChatAgent
from ai_agents.unified_agent import UnifiedAgent
from auth.auth_middleware import require_auth
from datetime import datetime

chat_routes = Blueprint('chat_routes', __name__)
host_chat_agent = HostChatAgent()
unified_agent = UnifiedAgent()

@chat_routes.route('/api/chat/threads', methods=['POST'])
@require_auth
def create_chat_thread():
    """Create a new chat thread between guest and host"""
    try:
        data = request.get_json()
        property_id = data.get('property_id')
        guest_id = data.get('guest_id')
        
        if not property_id or not guest_id:
            return jsonify({
                "success": False,
                "error": "Property ID and Guest ID are required"
            }), 400
        
        # Create new chat thread
        thread = {
            "property": ObjectId(property_id),
            "guest": ObjectId(guest_id),
            "status": "active",
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow()
        }
        
        result = host_chat_agent.db.ChatThreads.insert_one(thread)
        
        return jsonify({
            "success": True,
            "thread_id": str(result.inserted_id)
        })
        
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@chat_routes.route('/api/chat/threads/<thread_id>/messages', methods=['POST'])
@require_auth
async def send_message(thread_id):
    """Send a message in a chat thread"""
    try:
        data = request.get_json()
        message = data.get('message')
        sender_type = data.get('sender_type', 'guest')
        
        if not message:
            return jsonify({
                "success": False,
                "error": "Message is required"
            }), 400
            
        if sender_type not in ['guest', 'system']:
            return jsonify({
                "success": False,
                "error": "Invalid sender type"
            }), 400
        
        # Process message and get AI response
        response = await host_chat_agent.process_message(
            chat_id=thread_id,
            message=message,
            sender_type=sender_type
        )
        
        return jsonify(response)
        
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@chat_routes.route('/api/chat/threads/<thread_id>/messages', methods=['GET'])
@require_auth
def get_messages(thread_id):
    """Get messages from a chat thread"""
    try:
        # Get pagination parameters
        page = int(request.args.get('page', 1))
        per_page = int(request.args.get('per_page', 20))
        
        # Get messages with pagination
        messages = list(host_chat_agent.db.ChatHistory.find(
            {"chat_id": ObjectId(thread_id)}
        ).sort(
            "timestamp", -1
        ).skip(
            (page - 1) * per_page
        ).limit(per_page))
        
        # Count total messages
        total_messages = host_chat_agent.db.ChatHistory.count_documents(
            {"chat_id": ObjectId(thread_id)}
        )
        
        return jsonify({
            "success": True,
            "messages": [{
                "id": str(msg["_id"]),
                "message": msg["message"],
                "sender_type": msg["sender_type"],
                "timestamp": msg["timestamp"].isoformat(),
                "is_ai_response": msg.get("is_ai_response", False)
            } for msg in messages],
            "pagination": {
                "current_page": page,
                "per_page": per_page,
                "total_messages": total_messages,
                "total_pages": (total_messages + per_page - 1) // per_page
            }
        })
        
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@chat_routes.route('/api/chat/threads', methods=['GET'])
@require_auth
def get_user_threads():
    """Get all chat threads for a user"""
    try:
        user_id = request.args.get('user_id')
        role = request.args.get('role', 'guest')  # guest or host
        
        if not user_id:
            return jsonify({
                "success": False,
                "error": "User ID is required"
            }), 400
        
        # Query based on user role
        query = {
            "guest": ObjectId(user_id) if role == 'guest' else None,
            "status": "active"
        }
        
        threads = list(host_chat_agent.db.ChatThreads.find(query).sort("updated_at", -1))
        
        return jsonify({
            "success": True,
            "threads": [{
                "id": str(thread["_id"]),
                "property_id": str(thread["property"]),
                "guest_id": str(thread["guest"]),
                "status": thread["status"],
                "created_at": thread["created_at"].isoformat(),
                "updated_at": thread["updated_at"].isoformat()
            } for thread in threads]
        })
        
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500

@chat_routes.route('/api/chat', methods=['POST'])
def general_chat():
    """General chat endpoint that uses the unified agent. No authentication required."""
    try:
        data = request.get_json() or {}
        message = data.get('message')
        session_id = data.get('session_id')
        context = data.get('context', {})
    

        if not message:
            return jsonify({
                "success": False,
                "error": "Message is required"
            }), 400
        
        # Use unified agent to handle the chat
        result = unified_agent.chat(
            user_input=message,
            context=context if context else None,
            conversation_history=None  # Could be enhanced to maintain conversation history
        )
        
        # Format response to match frontend expectations
        if result.get('success'):
            return jsonify({
                "success": True,
                "message": result.get('reply', result.get('message', '')),
                "reply": result.get('reply', result.get('message', ''))
            })
        else:
            return jsonify({
                "success": False,
                "error": result.get('error', 'An error occurred')
            }), 500
        
    except Exception as e:
        return jsonify({
            "success": False,
            "error": str(e)
        }), 500