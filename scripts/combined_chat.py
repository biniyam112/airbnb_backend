import os
import sys
from dotenv import load_dotenv

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, '..'))
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from ai_agents.unified_agent import UnifiedAgent

load_dotenv()


def main():
    print("=" * 60)
    print("Unified AI Agent - Natural Language Interface")
    print("=" * 60)
    print("Just type naturally! I can help with:")
    print("  - Booking properties (quotes, confirmations, questions)")
    print("  - Property information and questions")
    print("  - Host advice and guidance")
    print("  - Pricing suggestions")
    print("  - General property rental questions")
    print()
    print("Type 'exit' or 'quit' to end the conversation.")
    print()
    
    agent = UnifiedAgent()
    conversation_history = ""
    
    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        
        if not user_input:
            continue
        
        if user_input.lower() in {"exit", "quit"}:
            break
        
        # Track user input
        conversation_history += f"User: {user_input}\n"
        
        try:
            # Use the unified agent's chat method which handles everything
            # Context is automatically extracted from conversation history
            res = agent.chat(user_input, context=None, conversation_history=conversation_history)
            
            if res.get("success"):
                reply = res.get("reply", "I'm here to help with property rental questions.")
                print(f"AI: {reply}")
                conversation_history += f"AI: {reply}\n\n"
            else:
                error_msg = f"Error: {res.get('error', 'Unknown error')}"
                print(f"AI: {error_msg}")
                conversation_history += f"AI: {error_msg}\n\n"
        except Exception as e:
            error_msg = f"Error: {e}"
            print(f"AI: {error_msg}")
            conversation_history += f"AI: {error_msg}\n\n"
            import traceback
            traceback.print_exc()
    
    print("\nGoodbye!")

if __name__ == '__main__':
    main()

