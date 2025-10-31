from flask import Blueprint, request, jsonify, redirect
from app import db
from app.model import Category, ContactFormSubmission, Ticket, ContactFormTicketLink, TicketAssignment, TicketAssignmentLog,TicketFile,TicketComment,TicketStatusLog,TicketTag, TicketFollowUp
from app.utils.helper_function import get_user_info_by_id
from app.dashboard_routes import require_api_key, validate_token
from datetime import datetime, timedelta
from app import llm_client
import threading, requests
from google_auth_oauthlib.flow import Flow
import os
import json
import requests
from datetime import datetime
from flask import Blueprint, jsonify, redirect, request
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
google_review_routes = Blueprint("google_review_routes", __name__)
AUTH_SYSTEM_URL = "https://api.dental360grp.com/api"


API_KEY = "AIzaSyCh2I913wPpd-efUREJ45JOpfE4ycT1xhI"
BUSINESS_PROFILE_ID = "14878371824930493439"
CLIENT_ID = "1035023216305-71mirf8qjof7metku95l9uebsboq87ic.apps.googleusercontent.com"
CLIENT_SECRET = "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"
SCOPES = ["https://www.googleapis.com/auth/business.manage"]
REDIRECT_URI = "https://acafe694ba63.ngrok-free.app/api/oauth2callback"
TOKEN_FILE = "google_token.json"


# 🧠 Helper — Save token to JSON
def save_token(creds: Credentials):
    data = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": creds.scopes,
        "expiry": creds.expiry.isoformat() if creds.expiry else None,
    }
    with open(TOKEN_FILE, "w") as f:
        json.dump(data, f)
    print("✅ Token saved to", TOKEN_FILE)


# 🧠 Helper — Load token if available
def load_token():
    if os.path.exists(TOKEN_FILE):
        with open(TOKEN_FILE, "r") as f:
            data = json.load(f)
            creds = Credentials.from_authorized_user_info(data, SCOPES)
            return creds
    return None


# 🧠 Helper — Get valid access token (auto refresh if needed)
def get_valid_token():
    creds = load_token()
    if creds and creds.expired and creds.refresh_token:
        print("♻️ Refreshing expired token...")
        creds.refresh(Request())
        save_token(creds)
    elif not creds or not creds.valid:
        return None
    return creds.token


# ---- Step 1: OAuth Login ----
@google_review_routes.route("/authorize")
def authorize():
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "redirect_uris": [REDIRECT_URI],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=SCOPES,
    )
    flow.redirect_uri = REDIRECT_URI
    auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline", include_granted_scopes="true")
    return redirect(auth_url)


# ---- Step 2: OAuth Callback ----
@google_review_routes.route("/oauth2callback")
def oauth2callback():
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
                "redirect_uris": [REDIRECT_URI],
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=SCOPES,
    )
    flow.redirect_uri = REDIRECT_URI
    flow.fetch_token(authorization_response=request.url)
    credentials = flow.credentials
    save_token(credentials)

    print("\n==== GOOGLE AUTH SUCCESS ====")
    print(f"Access Token: {credentials.token}")
    print(f"Token Expiry: {credentials.expiry}")
    print("==============================\n")

    return jsonify({
        "message": "Authorization successful! Token saved.",
        "access_token": credentials.token,
        "token_expiry": credentials.expiry.isoformat()
    })


# ---- Step 3: Fetch Reviews ----
@google_review_routes.route("/google_reviews", methods=["GET"])
def get_google_reviews():
    access_token = get_valid_token()
    if not access_token:
        print("⚠️ No valid token found, please visit /api/authorize first")
        return jsonify({"error": "Missing or expired token. Visit /api/authorize to connect Google again."}), 401

    print("📥 Using Access Token:", access_token[:20] + "...")

    headers = {"Authorization": f"Bearer {access_token}"}
    reviews_url = f"https://mybusiness.googleapis.com/v4/accounts/{BUSINESS_PROFILE_ID}/locations/-/reviews"
    response = requests.get(reviews_url, headers=headers)

    if response.status_code != 200:
        print("❌ Failed to fetch reviews:", response.text)
        return jsonify({"error": "Failed to fetch reviews", "details": response.text}), response.status_code

    reviews = response.json().get("reviews", [])
    print(f"✅ Fetched {len(reviews)} reviews")
    return jsonify({"reviews": reviews})