from flask import Flask, jsonify
from routes.ai_routes import ai_routes
from routes.chat_routes import chat_routes
from routes.booking_routes import booking_routes
from routes.property_routes import property_routes
import os
from dotenv import load_dotenv
from flask_cors import CORS

# Load environment variables
load_dotenv()

app = Flask(__name__)
CORS(app)  # Enable CORS for all routes

# Register blueprints
app.register_blueprint(ai_routes, url_prefix='/api/ai')
app.register_blueprint(chat_routes)
app.register_blueprint(booking_routes)
app.register_blueprint(property_routes)

@app.route('/')
def health_check():
    return jsonify({"status": "healthy", "service": "airbnb-ai-backend"})

if __name__ == '__main__':
    # Disable Flask's reloader when running directly
    app.run(
        host='127.0.0.1',
        port=5000,
        debug=True,
        use_reloader=False  # This fixes the socket error
    )