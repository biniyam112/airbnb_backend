from pymongo import MongoClient
from dotenv import load_dotenv
import os
import certifi

load_dotenv()

MONGODB_URI = os.getenv('MONGODB_URI', 'mongodb+srv://ntd5_db_user:qx5K04VCPIR8BlCr@airbnb-cluster0.u9b8fvx.mongodb.net/airbnb-db?retryWrites=true&w=majority')
DATABASE_NAME = 'airbnb-db'

def get_db_client():
    try:
        # Use certifi CA bundle for proper TLS handshake
        client = MongoClient(
            MONGODB_URI,
            tls=True,
            tlsCAFile=certifi.where(),
            serverSelectionTimeoutMS=8000,
        )
        # Test the connection (will raise if not reachable)
        client.admin.command('ping')
        return client
    except Exception as e:
        print(f"Database connection error: {e}")
        raise

def get_db():
    client = get_db_client()
    db = client[DATABASE_NAME]
    return db