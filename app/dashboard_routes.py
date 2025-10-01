import os
from functools import wraps
from datetime import datetime
import requests
from flask import Blueprint, request, jsonify, session, g


dashboard_bp = Blueprint('dashboard', __name__) 

# -------------------------------------------------------------------
# Load API Key from Environment
# -------------------------------------------------------------------
X_API_KEY = os.getenv("X_API_KEY", None)

# -------------------------------------------------------------------
# Decorator to validate API key
# -------------------------------------------------------------------
def require_api_key(f):
    """Decorator to ensure API key is provided and valid."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        api_key = request.headers.get('x-api-key')
        if not api_key:
            return jsonify({'error': 'API key is required.'}), 401

        if api_key != X_API_KEY:
            return jsonify({'error': 'Invalid API key.'}), 403

        return f(*args, **kwargs)

    return decorated_function

# -------------------------------------------------------------------
# Decorator to validate Bearer Token via Auth System
# -------------------------------------------------------------------
from functools import wraps
from flask import request, jsonify, g
import requests

def validate_token(func):
    @wraps(func)
    def decorated_function(*args, **kwargs):
        # Get the Bearer token from the request header
        auth_header = request.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            return jsonify({"error": "Bearer token is required in Authorization header."}), 401

        bearer_token = auth_header.split('Bearer ')[1]

        # URL of the Auth system's validate_token API
        auth_system_url = "https://api.dental360grp.com/validate_token"

        try:
            response = requests.get(
                auth_system_url,
                headers={"Authorization": f"Bearer {bearer_token}"}
            )

            # ✅ Always return actual response from Auth system
            if response.status_code == 200:
                g.user = response.json().get("user")
            return jsonify(response.json()), response.status_code

        except requests.exceptions.RequestException as e:
            return jsonify({"error": f"Error connecting to Auth system: {str(e)}"}), 500

    return decorated_function



# -------------------------------------------------------------------
# Check Dashboard Endpoint
# -------------------------------------------------------------------
@dashboard_bp.route('/dashboard/check', methods=['POST'])
@require_api_key
def check_dashboard():
    """
    Verify if the user's profile has the required dashboard (eligibility).
    If yes, set session values including clinic_id and return success.
    """
    try:
        profile_data = request.json.get('profile')

        if not profile_data:
            return jsonify({'error': 'Profile data not provided in request'}), 400

        # Get the list of dashboards
        dashboards = profile_data.get('dashboards', [])

        # Check if any dashboard name matches 'ticketsystem360'
        matching_dashboard = next((d for d in dashboards if d.get('name') == 'ticketsystem360'), None)

        if matching_dashboard:
            # Set session variables from the profile data
            session['user_id'] = profile_data.get('id')
            session['first_name'] = profile_data.get('first_name')
            session['last_name'] = profile_data.get('last_name')
            full_name = f"{session['first_name']} {session['last_name']}".strip()
            session['full_name'] = full_name
            session['email'] = profile_data.get('email')
            session['role'] = profile_data.get('role', {}).get('name')
            session['dashboard_id'] = matching_dashboard.get('id')
            session['dashboard_name'] = matching_dashboard.get('name')

            # ✅ Add clinic_id in session
            session['clinic_id'] = profile_data.get('clinic_id')

            return jsonify({
                'message': 'Dashboard matched successfully',
                'user_id': session.get('user_id'),
                'first_name': session.get('first_name'),
                'last_name': session.get('last_name'),
                'email': session.get('email'),
                'role': session.get('role'),
                'dashboard_id': session.get('dashboard_id'),
                'dashboard_name': session.get('dashboard_name'),
                'clinic_id': session.get('clinic_id')
            }), 200
        else:
            return jsonify({'error': 'No matching dashboard found'}), 400

    except Exception as e:
        return jsonify({'error': str(e)}), 500
