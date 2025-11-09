from ai_agents.maintenence_agent import MaintenanceAgent 
from dotenv import load_dotenv
import asyncio

load_dotenv()

async def main():
    agent = MaintenanceAgent()
    result = await agent.handle_checkout(
        property_id="673a1e000000000000000001",
        checkout_time="2023-11-08T11:00:00"
    )
    print(result)

# Run the async function
asyncio.run(main())