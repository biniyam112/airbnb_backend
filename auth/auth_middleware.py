from functools import wraps
from flask import request, jsonify
import jwt
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

JWT_SECRET = os.getenv('JWT_SECRET', 'your-secret-key')

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        
        # Check if token is in headers
        if 'Authorization' in request.headers:
            auth_header = request.headers['Authorization']
            try:
                token = auth_header.split(" ")[1]
            except IndexError:
                return jsonify({
                    "success": False,
                    "error": "Invalid token format"
                }), 401
        
        if not token:
            return jsonify({
                "success": False,
                "error": "Token is missing"
            }), 401
            
        try:
            # Verify token
            data = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
            # You can add user data to request context here if needed
            request.user = data
            
        except jwt.ExpiredSignatureError:
            return jsonify({
                "success": False,
                "error": "Token has expired"
            }), 401
        except jwt.InvalidTokenError:
            return jsonify({
                "success": False,
                "error": "Invalid token"
            }), 401
            
        return f(*args, **kwargs)
    
    return decorated