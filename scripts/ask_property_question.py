import sys
import os
from dotenv import load_dotenv

# Ensure backend root on path
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, '..'))
if BACKEND_ROOT not in sys.path:
    sys.path.insert(0, BACKEND_ROOT)

from ai_agents.property_chat_agent import PropertyChatAgent

load_dotenv()

def main():
    if len(sys.argv) < 3:
        print("Usage: python scripts/ask_property_question.py <property_id> <question>")
        sys.exit(1)
    property_id = sys.argv[1]
    question = " ".join(sys.argv[2:])
    agent = PropertyChatAgent()
    res = agent.ask(property_id, question, session_id="single-run")
    print(res)

if __name__ == "__main__":
    main()
