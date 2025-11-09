import os, sys, uuid
from datetime import datetime
from dotenv import load_dotenv

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, '..'))
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from ai_agents.booking_agent import BookingAgent
from ai_agents.property_chat_agent import PropertyChatAgent
from config.db import get_db

load_dotenv()

HELP = """Commands:
  quote <property_id> <start:YYYY-MM-DD> <end:YYYY-MM-DD> [guest_id]
  chat <booking_id> <message>
  confirm <booking_id>
  list-bookings [property_id]
  exit | quit
"""

def main():
    print("Interactive Booking Demo. Type 'help' for commands.")
    agent = BookingAgent()
    conversation_history = ""  # Track user inputs and responses
    
    while True:
        try:
            raw = input("booking> ").strip()
        except (EOFError, KeyboardInterrupt):
            print() ; break
        if not raw: continue
        if raw.lower() in {"exit", "quit"}: break
        
        # Track user input
        conversation_history += f"User: {raw}\n"
        
        if raw.lower() == "help":
            print(HELP)
            conversation_history += f"Response: {HELP}\n\n"
            continue
        parts = raw.split()
        cmd = parts[0].lower()
        try:
            response = None
            if cmd == "quote":
                if len(parts) < 4:
                    response = "Usage: quote <property_id> <start> <end> [guest_id]"
                    print(response)
                else:
                    property_id, start, end = parts[1], parts[2], parts[3]
                    guest_id = parts[4] if len(parts) > 4 else None
                    res = agent.create_quote(property_id, guest_id, start, end)
                    response = str(res)
                    print(res)
            elif cmd == "chat":
                if len(parts) < 3:
                    response = "Usage: chat <booking_id> <message>"
                    print(response)
                else:
                    booking_id = parts[1]
                    message = " ".join(parts[2:])
                    res = agent.chat(booking_id, message)
                    response = str(res)
                    print(res)
            elif cmd == "confirm":
                if len(parts) < 2:
                    response = "Usage: confirm <booking_id>"
                    print(response)
                else:
                    booking_id = parts[1]
                    res = agent.confirm(booking_id)
                    response = str(res)
                    print(res)
            elif cmd == "list-bookings":
                from config.db import get_db
                db = get_db()
                q = {}
                if len(parts) > 1:
                    from bson.objectid import ObjectId
                    q['property'] = ObjectId(parts[1])
                items = []
                for b in db.booking.find(q).sort('createdAt', -1).limit(25):
                    b['_id'] = str(b['_id'])
                    items.append({k: (str(v) if k in ['property','guest','_id'] else v) for k,v in b.items()})
                response = str(items)
                print(items)
            else:
                # Handle unrecognized commands with Dedalus
                res = agent.handle_general_chat(raw, conversation_history)
                if res.get("success"):
                    response = res.get("reply", "I'm here to help with booking-related questions.")
                else:
                    response = f"Error: {res.get('error', 'Unknown error')}"
                print(response)
            
            # Track response
            if response is not None:
                conversation_history += f"Response: {response}\n\n"
        except Exception as e:
            error_msg = f"Error: {e}"
            print(error_msg)
            conversation_history += f"Response: {error_msg}\n\n"
    
    print("Goodbye")
    # Optionally print or save conversation_history at the end
    # print("\n=== Conversation History ===")
    # print(conversation_history)

if __name__ == '__main__':
    main()
