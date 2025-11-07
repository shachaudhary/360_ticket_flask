import os, uuid, mimetypes, botocore, boto3, requests
from datetime import datetime
from flask import Blueprint, request, jsonify
from app import db
import aiohttp
from aiohttp import BasicAuth
import asyncio
import os
import requests
from app.model import Ticket, TicketAssignment, TicketFile, TicketTag, TicketComment, TicketStatusLog, TicketAssignmentLog,EmailLog


# ─── S3 Config ──────────────────────────────────────────────
S3_BUCKET      = os.getenv("S3_BUCKET")
S3_REGION      = os.getenv("S3_REGION")
AWS_ACCESS_KEY = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
MAILGUN_API_URL = os.getenv("MAILGUN_API_URL")
MAILGUN_API_KEY = os.getenv("MAILGUN_API_KEY") 

s3 = boto3.client(
    "s3",
    region_name=S3_REGION,
    aws_access_key_id=AWS_ACCESS_KEY,
    aws_secret_access_key=AWS_SECRET_KEY,
)

ALLOWED_EXT = {"png", "jpg", "jpeg", "gif", "pdf", "doc", "docx", "csv", "xls", "xlsx"}

# ─── Helper: Upload file to S3 ─────────────────────────────
def upload_to_s3(f, folder="tickets"):
    ext = f.filename.rsplit(".", 1)[1].lower()
    if ext not in ALLOWED_EXT:
        raise ValueError("File type not allowed")
    key  = f"{folder}/{uuid.uuid4().hex}.{ext}"
    ctyp = f.content_type or mimetypes.guess_type(f.filename)[0] or "application/octet-stream"
    try:
        s3.upload_fileobj(f, S3_BUCKET, key, ExtraArgs={"ContentType": ctyp, "ACL": "private"})
    except botocore.exceptions.ClientError as e:
        raise RuntimeError(f"S3 upload failed: {e.response['Error']['Message']}")
    return f"https://{S3_BUCKET}.s3.{S3_REGION}.amazonaws.com/{key}"

# ─── Helper: Send email (dummy) ─────────────────────────────

def send_email(to, subject, body_html, body_text=None):
    """
    Send email via Mailgun and log all attempts with response.
    """
    data = {
        "from": "support@360dentalbillingsolutions.com",
        "to": to,
        "subject": subject,
        "html": body_html
    }
    if body_text:
        data["text"] = body_text

    log_entry = EmailLog(
        to=to,
        subject=subject,
        body_html=body_html,
        body_text=body_text,
    )

    try:
        response = requests.post(
            MAILGUN_API_URL,
            auth=("api", MAILGUN_API_KEY),
            data=data,
            timeout=30
        )

        log_entry.status_code = response.status_code
        log_entry.mailgun_response = response.text
        log_entry.success = (response.status_code == 200)

        if log_entry.success:
            print(f"✅ Email successfully sent to {to}")
        else:
            print(f"❌ Failed to send email: {response.status_code} - {response.text}")

    except Exception as e:
        log_entry.mailgun_response = f"Exception: {str(e)}"
        log_entry.success = False
        print(f"⚠️ Email sending failed: {e}")

    # Save the log no matter what
    try:
        db.session.add(log_entry)
        db.session.commit()
    except Exception as db_err:
        db.session.rollback()
        print(f"⚠️ Failed to log email: {db_err}")

    return log_entry.success


# ─── Helper: Get user info from external API ────────────────
def get_user_info_by_id(user_id):
    try:
        url = f"https://api.dental360grp.com/api/user/{user_id}"
        resp = requests.get(url, timeout=5)
        if resp.status_code == 200:
            user = resp.json()
            return {
                "id": user.get("id"),
                "username": f"{user.get('first_name', '')} {user.get('last_name', '')}".strip(),
                "email": user.get("email"),
                "phone": user.get("phone"),
                "role": user.get("user_role")
            }
    except Exception as e:
        print(f"❌ Error fetching user info for {user_id}: {e}")
    return None


def update_ticket_status(ticket_id, new_status, user_id):
    ticket = Ticket.query.get(ticket_id)
    if not ticket:
        return None
    
    old_status = ticket.status
    ticket.status = new_status
    db.session.add(ticket)

    # Log entry
    log = TicketStatusLog(
        ticket_id=ticket.id,
        old_status=old_status,
        new_status=new_status,
        changed_by=user_id
    )
    db.session.add(log)
    db.session.commit()
    return ticket

def update_ticket_assignment_log(ticket_id, old_assign_to, new_assign_to, changed_by):
    ticket = Ticket.query.get(ticket_id)
    if not ticket:
        return None

    log = TicketAssignmentLog(
        ticket_id=ticket.id,
        old_assign_to=old_assign_to,
        new_assign_to=new_assign_to,
        changed_by=changed_by
    )
    db.session.add(log)
    db.session.commit()
    return log





