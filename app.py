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
    port = int(os.environ.get("PORT", 5000))
    app.run(
        host='0.0.0.0',
        port=port,
        debug=os.environ.get("FLASK_ENV", "").lower() == "development",
        use_reloader=False
    )
