from flask import Blueprint, request, jsonify
from app.model import db, EmailLog
from flask import Blueprint, request, jsonify, current_app
from app.model import db, EmailLog
from datetime import datetime
import requests
import os

mailgun_bp = Blueprint("mailgun_bp", __name__)

@mailgun_bp.route("/webhook/mailgun", methods=["POST"])
def mailgun_webhook():
    data = request.get_json(force=True, silent=True) or {}
    event = data.get("event")
    message_id = data.get("id") or data.get("Message-Id")
    recipient = data.get("recipient")
    status_detail = data.get("delivery-status", {}).get("message")

    print(f"📩 Mailgun webhook: {event} for {recipient}")

    if not recipient or not event:
        return jsonify({"error": "Invalid payload"}), 400

    try:
        log = (
            EmailLog.query.filter(EmailLog.to == recipient)
            .order_by(EmailLog.created_at.desc())
            .first()
        )
        if log:
            if event == "delivered":
                log.success = True
                log.status_code = 250
                log.mailgun_response = f"Delivered: {status_detail}"
            elif event in ["failed", "rejected", "bounced"]:
                log.success = False
                log.status_code = 550
                log.mailgun_response = f"Failed: {status_detail}"
            db.session.commit()
            print(f"✅ Updated EmailLog → {recipient} ({event})")
        else:
            print(f"⚠️ No matching EmailLog found for {recipient}")

        return jsonify({"status": "ok"}), 200
    except Exception as e:
        db.session.rollback()
        print(f"⚠️ Mailgun webhook error: {e}")
        return jsonify({"error": str(e)}), 500




GRAPH_TOKEN_URL = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"


def get_graph_token():
    """Get Microsoft Graph access token"""
    tenant_id = os.getenv("MICROSOFT_TENANT_ID")
    client_id = os.getenv("MICROSOFT_CLIENT_ID")
    client_secret = os.getenv("MICROSOFT_CLIENT_SECRET")
    print(f"🔑 Generating Microsoft Graph token for {tenant_id}",client_id,client_secret)

    token_url = GRAPH_TOKEN_URL.format(tenant_id=tenant_id)
    data = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": "https://graph.microsoft.com/.default",
    }

    res = requests.post(token_url, data=data, timeout=30)
    if res.status_code != 200:
        raise Exception(f"Token request failed: {res.text}")
    token = res.json().get("access_token")
    if not token:
        raise Exception("No access_token found in Graph response")
    return token


@mailgun_bp.route("/test_send_email", methods=["POST"])
def test_send_email():
    """
    📧 Test Microsoft Graph Email Sending
    Body: {
        "to": "user@example.com",
        "subject": "Test Email",
        "body_html": "<h3>Hello from Dental360!</h3>",
        "body_text": "Hello from Dental360!"
    }
    """
    data = request.get_json(force=True, silent=True) or {}
    to = data.get("to")
    subject = data.get("subject")
    body_html = data.get("body_html")
    body_text = data.get("body_text")

    if not to or not subject:
        return jsonify({"error": "Missing required fields: to, subject"}), 400

    try:
        token = get_graph_token()
        sender_email = current_app.config.get("MICROSOFT_SENDER_EMAIL", "patient@dental360grp.com")

        # Prepare Graph payload
        message = {
            "message": {
                "subject": subject,
                "body": {
                    "contentType": "HTML" if body_html else "Text",
                    "content": body_html or body_text or "No content provided",
                },
                "toRecipients": [{"emailAddress": {"address": str(to).strip()}}],
            },
            "saveToSentItems": True,
        }

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

        url = f"{GRAPH_BASE_URL}/users/{sender_email}/sendMail"
        response = requests.post(url, headers=headers, json=message, timeout=30)

        success = response.status_code in (200, 202)
        resp_text = response.text or response.reason

        # Save to EmailLog
        log_entry = EmailLog(
            to=str(to).strip(),
            subject=subject,
            body_html=body_html,
            body_text=body_text,
            mailgun_response=resp_text,
            status_code=response.status_code,
            success=success,
            created_at=datetime.utcnow(),
        )
        db.session.add(log_entry)
        db.session.commit()

        print(f"🪵 EmailLog saved → {to} | {response.status_code} | success={success}")

        return jsonify({
            "success": success,
            "status_code": response.status_code,
            "graph_response": resp_text,
            "to": to,
            "subject": subject
        }), (200 if success else 500)

    except Exception as e:
        db.session.rollback()
        print(f"⚠️ Graph send email error: {e}")
        return jsonify({"error": str(e)}), 500