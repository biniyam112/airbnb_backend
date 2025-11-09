from flask import Blueprint, request, jsonify
from ai_agents.booking_agent import BookingAgent
from auth.auth_middleware import require_auth

booking_routes = Blueprint('booking_routes', __name__)
agent = BookingAgent()

@booking_routes.route('/api/bookings/quote', methods=['POST'])
@require_auth
def create_quote():
    data = request.get_json(force=True) or {}
    property_id = data.get('property_id')
    start_date = data.get('start_date')
    end_date = data.get('end_date')
    guest_id = data.get('guest_id') or (getattr(request, 'user', {}).get('id') if hasattr(request, 'user') else None)

    if not property_id or not start_date or not end_date:
        return jsonify({"success": False, "error": "property_id, start_date, end_date required"}), 400

    result = agent.create_quote(property_id, guest_id, start_date, end_date)
    status = 200 if result.get('success') else 400
    return jsonify(result), status

@booking_routes.route('/api/bookings/<booking_id>/chat', methods=['POST'])
@require_auth
def booking_chat(booking_id):
    data = request.get_json(force=True) or {}
    message = data.get('message')
    if not message:
        return jsonify({"success": False, "error": "message required"}), 400
    result = agent.chat(booking_id, message)
    status = 200 if result.get('success') else 400
    return jsonify(result), status

@booking_routes.route('/api/bookings/<booking_id>/confirm', methods=['POST'])
@require_auth
def confirm_booking(booking_id):
    result = agent.confirm(booking_id)
    status = 200 if result.get('success') else 400
    return jsonify(result), status

@booking_routes.route('/api/bookings/<booking_id>', methods=['GET'])
@require_auth
def get_booking(booking_id):
    from config.db import get_db
    db = get_db()
    b = db.booking.find_one({"_id": agent.db.booking._BaseObject__ensure_objectid(booking_id)}) if False else db.booking.find_one({"_id": __import__('bson').ObjectId(booking_id)})
    if not b:
        return jsonify({"success": False, "error": "Booking not found"}), 404
    b['id'] = str(b['_id'])
    b['_id'] = str(b['_id'])
    return jsonify({"success": True, "booking": b})

@booking_routes.route('/api/bookings', methods=['GET'])
@require_auth
def list_bookings():
    from config.db import get_db
    db = get_db()
    q = {}
    prop = request.args.get('property_id')
    status = request.args.get('status')
    if prop:
        q['property'] = __import__('bson').ObjectId(prop)
    if status:
        q['status'] = status
    items = []
    for b in db.booking.find(q).sort('createdAt', -1).limit(50):
        b['id'] = str(b['_id'])
        b['_id'] = str(b['_id'])
        items.append(b)
    return jsonify({"success": True, "bookings": items})
