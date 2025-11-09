from flask import Blueprint, jsonify, request
from bson.objectid import ObjectId
from config.db import get_db
from datetime import datetime
from bson import json_util
import json


from typing import Any, List
from collections.abc import Mapping

def _safe_json(value: Any) -> Any:
    """Recursively convert BSON/unsupported types (ObjectId, datetime) to JSON-safe values.

    Falls back to bson.json_util for any unhandled complex structures.
    """
    try:
        if isinstance(value, ObjectId):
            return str(value)
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, list):
            return [_safe_json(v) for v in value]
        if isinstance(value, tuple):
            return [_safe_json(v) for v in value]  # convert tuples to lists
        # Important: use collections.abc.Mapping (or dict) for isinstance checks
        if isinstance(value, Mapping):
            return {str(k): _safe_json(v) for k, v in value.items()}
        return value
    except Exception:
        # Last resort: json_util to ensure serialization
        try:
            return json.loads(json_util.dumps(value))
        except Exception:
            return str(value)


def _find_object_ids(value: Any, path: str = "") -> List[str]:
    """Return a list of dotted paths where ObjectId instances still exist."""
    found: List[str] = []
    if isinstance(value, ObjectId):
        found.append(path or "root")
    elif isinstance(value, dict):
        for k, v in value.items():
            new_path = f"{path}.{k}" if path else k
            found.extend(_find_object_ids(v, new_path))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            new_path = f"{path}[{i}]" if path else f"[{i}]"
            found.extend(_find_object_ids(v, new_path))
    return found

property_routes = Blueprint('property_routes', __name__)


def serialize_property(prop: dict, db, include_host_details: bool = False) -> dict:
    """Serialize a property document for API response."""
    prop['id'] = str(prop['_id'])
    prop['_id'] = str(prop['_id'])
    
    # Convert dates to ISO strings
    if 'createdAt' in prop and isinstance(prop['createdAt'], datetime):
        prop['createdAt'] = prop['createdAt'].isoformat()
    if 'updatedAt' in prop and isinstance(prop['updatedAt'], datetime):
        prop['updatedAt'] = prop['updatedAt'].isoformat()
    
    # Ensure location has expected structure
    if 'location' not in prop:
        prop['location'] = {}
    
    # Handle host information
    if 'host' in prop and isinstance(prop['host'], ObjectId):
        host_id = prop['host']
        prop['host'] = str(host_id)
        
        if include_host_details:
            host = db.user.find_one({'_id': host_id})
            if host:
                prop['hostDetails'] = {
                    'id': str(host['_id']),
                    'firstName': host.get('firstName', ''),
                    'lastName': host.get('lastName', ''),
                    'email': host.get('email', ''),
                    'isSuperhost': host.get('isSuperhost', False),
                }
    elif 'host' in prop:
        prop['host'] = str(prop['host'])
    
    # Add mock rating if not present (you can calculate from reviews later)
    if 'rating' not in prop:
        prop['rating'] = 4.5 + (hash(prop['id']) % 10) / 20  # Generate consistent mock rating 4.5-5.0
    
    if 'reviewCount' not in prop:
        prop['reviewCount'] = (hash(prop['id']) % 50) + 10  # Generate consistent mock count 10-60
    
    # Ensure images is a list
    if 'images' not in prop or not prop['images']:
        prop['images'] = []
    
    # Ensure amenities is a list
    if 'amenities' not in prop or not prop['amenities']:
        prop['amenities'] = []
    
    # Final pass: recursively convert any residual ObjectId/datetime in nested structures
    serialized: dict = _safe_json(prop)  # type: ignore
    assert isinstance(serialized, dict), "Serialized property must be a dict"
    # One more defensive pass via bson.json_util to catch exotic nested types
    try:
        serialized = json.loads(json_util.dumps(serialized))
    except Exception:
        pass
    return serialized


@property_routes.route('/api/properties', methods=['GET'])
def list_properties():
    """List properties with basic filters and pagination.

    Query params:
      - q: text search on title/description
      - city: filter by city
      - limit: page size (default 20, max 50)
      - page: page number starting at 1
    """
    try:
        db = get_db()
        q: dict = {}
        search = request.args.get('q')
        city = request.args.get('city')
        if search:
            # Simple regex search on title/description
            q['$or'] = [
                {'title': {'$regex': search, '$options': 'i'}},
                {'description': {'$regex': search, '$options': 'i'}}
            ]
        if city:
            q['location.city'] = city

        try:
            limit = max(1, min(int(request.args.get('limit', '20')), 50))
        except Exception:
            limit = 20
        try:
            page = max(1, int(request.args.get('page', '1')))
        except Exception:
            page = 1

        skip = (page - 1) * limit

        cursor = db.property.find(q).sort('createdAt', -1).skip(skip).limit(limit)
        items = []
        for p in cursor:
            items.append(serialize_property(p, db, include_host_details=False))

        total = db.property.count_documents(q)
        response_payload = {
            'success': True,
            'items': items,
            'page': page,
            'limit': limit,
            'total': total
        }
        # Deep sanitize entire response before jsonify
        sanitized = _safe_json(response_payload)
        try:
            return jsonify(sanitized)
        except TypeError as te:
            # Identify residual ObjectIds and attempt bson.json_util path
            offending_paths = _find_object_ids(response_payload)
            fallback_sanitized = json.loads(json_util.dumps(response_payload))
            return jsonify({
                'success': True,
                'items': fallback_sanitized.get('items', []),
                'page': fallback_sanitized.get('page'),
                'limit': fallback_sanitized.get('limit'),
                'total': fallback_sanitized.get('total'),
                '_debug': {
                    'serialization_error': str(te),
                    'offending_paths': offending_paths
                }
            })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@property_routes.route('/api/properties/<property_id>', methods=['GET'])
def get_property(property_id):
    """Get a single property by ID with full details including host information."""
    try:
        db = get_db()
        prop = db.property.find_one({'_id': ObjectId(property_id)})
        if not prop:
            return jsonify({'success': False, 'error': 'Property not found'}), 404
        
        serialized = serialize_property(prop, db, include_host_details=True)
        return jsonify({'success': True, 'property': serialized})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 400


@property_routes.route('/api/properties/health', methods=['GET'])
def properties_health():
    """Lightweight diagnostics endpoint for properties collection."""
    try:
        db = get_db()
        count = db.property.count_documents({})
        sample = []
        for p in db.property.find({}, {'_id': 1}).limit(3):
            sample.append(str(p['_id']))
        return jsonify({'success': True, 'count': count, 'sample': sample})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
