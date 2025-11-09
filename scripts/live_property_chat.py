import os
import sys
import uuid
from dotenv import load_dotenv

# Ensure local package path is available when script executed directly
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, '..'))
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from ai_agents.property_chat_agent import PropertyChatAgent

load_dotenv()

def main():
    if len(sys.argv) > 1:
        property_id = sys.argv[1]
    else:
        property_id = input("Enter property_id to chat about: ").strip()

    if not property_id:
        print("Property ID is required.")
        sys.exit(1)

    agent = PropertyChatAgent()
    session_id = str(uuid.uuid4())

    print("\nProperty Chat started. Type 'exit' or 'quit' to end.\n")
    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if user_input.lower() in {"exit", "quit"}:
            print("Goodbye!")
            break

        result = agent.ask(property_id, user_input, session_id=session_id)
        if not result.get("success"):
            print(f"Error: {result.get('error')}")
            continue
        print(f"AI: {result.get('message')}")

if __name__ == "__main__":
    main()
