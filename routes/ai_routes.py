from flask import Blueprint, jsonify, request
from typing import Optional, TYPE_CHECKING

ai_routes = Blueprint('ai_routes', __name__)

# Lazy singletons to avoid heavy DB/model initialization at import time.
if TYPE_CHECKING:
    from ai_agents.pricing_agent import PricingAgent
    from ai_agents.host_community_agent import HostCommunityAgent

_pricing_agent: Optional["PricingAgent"] = None
_host_community_agent: Optional["HostCommunityAgent"] = None

def get_pricing_agent():
    global _pricing_agent
    if _pricing_agent is None:
        from ai_agents.pricing_agent import PricingAgent  # local import to defer dependencies
        _pricing_agent = PricingAgent()
    return _pricing_agent

def get_host_community_agent():
    global _host_community_agent
    if _host_community_agent is None:
        from ai_agents.host_community_agent import HostCommunityAgent  # local import to defer dependencies
        _host_community_agent = HostCommunityAgent()
    return _host_community_agent

@ai_routes.route('/dynamic-pricing/suggest', methods=['POST'])
def suggest_price_post():
    """Suggest price by POSTing JSON { "property_id": "..." }"""
    try:
        data = request.get_json(force=True) or {}
        property_id = data.get('property_id')
        if not property_id:
            return jsonify({"success": False, "error": "property_id is required"}), 400
        result = get_pricing_agent().suggest_price(property_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400

@ai_routes.route('/dynamic-pricing/suggest/<property_id>', methods=['GET'])
def suggest_price_get(property_id):
    """Suggest price via GET for compatibility with tests"""
    try:
        result = get_pricing_agent().suggest_price(property_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400


@ai_routes.route('/host-community/advice/<host_id>', methods=['GET'])
def host_advice_get(host_id):
    """Get host community performance advice. Optional query param: focus."""
    focus = request.args.get('focus')
    result = get_host_community_agent().get_host_advice(host_id, focus=focus)
    status = 200 if result.get('success') else 400
    return jsonify(result), status


@ai_routes.route('/host-community/chat', methods=['POST'])
def host_chat_post():
    """Chat with host community advisor. Body: { host_id, question, session_id? }"""
    try:
        data = request.get_json(force=True) or {}
        host_id = data.get('host_id')
        question = data.get('question')
        session_id = data.get('session_id')
        if not host_id or not question:
            return jsonify({"success": False, "error": "host_id and question required"}), 400
        result = get_host_community_agent().ask(host_id, question, session_id=session_id)
        status = 200 if result.get('success') else 400
        return jsonify(result), status
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400
