import os, uuid, mimetypes, botocore, boto3, requests
from datetime import datetime
from flask import Blueprint, request, jsonify
from app import db
import aiohttp
from aiohttp import BasicAuth
import asyncio, sys, threading
from sqlalchemy import or_, and_, func
import re
import html

from app.model import Ticket, TicketAssignment, TicketFile, TicketTag, TicketComment, Category, TicketFollowUp, \
    TicketStatusLog, TicketAssignmentLog, ContactFormTicketLink, EmailProcessedLog, TicketAssignLocation, \
    ProjectTicket, Project, ProjectTag, ProjectAssignment
from app.utils.helper_function import upload_to_s3, send_email, get_user_info_by_id, update_ticket_status, update_ticket_assignment_log, get_user_id_by_email, get_graph_token, GRAPH_BASE_URL
from app.utils.email_templete import send_tag_email,send_assign_email, send_follow_email, send_update_ticket_email
from app.notification_route import create_notification
from app.dashboard_routes import require_api_key, validate_token
from app import llm_client
# ─── Windows Fix for asyncio ─────────────────────────────────────────────
# if sys.platform.startswith("win"):
#     asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# ─── Blueprint ─────────────────────────────────────────────
ticket_bp = Blueprint("tickets", __name__, url_prefix="/api/tickets")

import requests
import os

AUTH_SYSTEM_URL = os.getenv(
    "AUTH_SYSTEM_URL",
    "https://api.dental360grp.com/api"
)

def get_clinic_locations_map(clinic_id):
    """
    Returns:
      { location_id (int): location_display_name (str) }
    Based on Auth API response:
      { "locations": [ ... ] }
    """
    try:
        url = f"{AUTH_SYSTEM_URL}/clinic_locations/get_all/{clinic_id}"
        resp = requests.get(url, timeout=5)

        if resp.status_code != 200:
            return {}

        payload = resp.json()

        # ✅ EXACT key from your response
        locations = payload.get("locations", [])
        if not isinstance(locations, list):
            return {}

        location_map = {}
        for loc in locations:
            if not isinstance(loc, dict):
                continue

            loc_id = loc.get("id")
            loc_name = (
                loc.get("location_name")   # ✅ primary
                or loc.get("display_name") # fallback
                or f"Location #{loc_id}"
            )

            if loc_id:
                location_map[int(loc_id)] = loc_name.strip()

        return location_map

    except Exception as e:
        print("❌ Failed to fetch clinic locations:", e)
        return {}


# ─────────────────────────────────────────────
# Create Ticket with files and @username tags
@ticket_bp.route("/ticket", methods=["POST"])
@require_api_key
# @validate_token
def create_ticket():
    data = request.form

    # Parse due_date
    due_date = None
    if data.get("due_date"):
        try:
            due_date = datetime.strptime(data["due_date"], "%Y-%m-%d").date()
        except ValueError:
            return jsonify({"error": "Invalid due_date format. Use YYYY-MM-DD"}), 400

    ticket = Ticket(
        clinic_id=data.get("clinic_id"),
        location_id=data.get("location_id"),
        user_id=data.get("user_id"),
        title=data.get("title"),
        details=data.get("details"),
        category_id=data.get("category_id"),
        status=data.get("status", "Pending"),
        priority=data.get("priority"),
        due_date=due_date
    )
    db.session.add(ticket)
    db.session.commit()

    # Multiple file upload
    uploaded_files = []
    if "files" in request.files:
        for f in request.files.getlist("files"):
            if f.filename:
                try:
                    print(f.filename)
                    file_url = upload_to_s3(f, folder=f"tickets/{ticket.id}")
                    tf = TicketFile(ticket_id=ticket.id, file_url=file_url, file_name=f.filename)
                    db.session.add(tf)
                    uploaded_files.append({"name": f.filename, "url": file_url})
                except Exception as e:
                    db.session.rollback()
                    return jsonify({"error": str(e)}), 500
        db.session.commit()

    # -----------------------------
    # Follow-up users

    followup_user_ids = data.get("followup_user_ids")  # e.g. "12,15"
    if followup_user_ids:
        ids = [int(uid.strip()) for uid in followup_user_ids.split(",") if uid.strip().isdigit()]
        for uid in ids:
            user_info = get_user_info_by_id(uid)
            if user_info:
                # 1. Save TicketFollowUp entry
                followup = TicketFollowUp(
                    ticket_id=ticket.id,
                    user_id=uid,
                    note=f"Added as follow-up by user {ticket.user_id}"
                )
                db.session.add(followup)

                # 2. Send email
                send_follow_email(ticket, user_info)

                # 3. Save notification
                create_notification(
                    ticket_id=ticket.id,
                    receiver_id=uid,
                    sender_id=ticket.user_id,    # jisne ticket create ki
                    notification_type="followup",
                    message=f"Added as follow-up by user {ticket.user_id}"
                )
        db.session.commit()



    # Category assignee email bhejna
    if ticket.category_id:
        category = Category.query.get(ticket.category_id)
        if category and category.assignee_id:
            assignee_info = get_user_info_by_id(category.assignee_id)

            # 👇 Ticket creator ka info le aao
            assigner_info = get_user_info_by_id(ticket.user_id)

            print("Assignee:", assignee_info)
            print("Assigner:", assigner_info)

            if assignee_info:
                # 1. Save TicketAssignment
                assignment = TicketAssignment(
                    ticket_id=ticket.id,
                    assign_to=category.assignee_id,
                    assign_by=ticket.user_id
                )
                db.session.add(assignment)
                db.session.commit()

                # 2. Send email
                send_assign_email(ticket, assignee_info, assigner_info)

                # 3. Save notification
                create_notification(
                    ticket_id=ticket.id,
                    receiver_id=assignee_info["id"],
                    sender_id=ticket.user_id,   
                    notification_type="assign",
                    message=f"Assigned to you by {assigner_info['username']}"
                )



    return jsonify({
        "success": True,
        "message": "Ticket created",
        "ticket_id": ticket.id,
        "files": uploaded_files
    })

# ─────────────────────────────────────────────
# Update Ticket
# ─────────────────────────────────────────────
@ticket_bp.route("/ticket/<int:ticket_id>", methods=["PATCH"])
@require_api_key
@validate_token
def update_ticket(ticket_id):
    ticket = Ticket.query.get(ticket_id)
    if not ticket:
        return jsonify({"error": "Ticket not found"}), 404

    data = request.form if request.form else request.json
    updated_fields = []  # Track changes

    # 👇 Always resolve updater info first
    updater_id = int(data.get("updated_by")) if data.get("updated_by") else None
    updater_info = get_user_info_by_id(updater_id)

    # --- Title
    if "title" in data and data["title"] != ticket.title:
        updated_fields.append(("title", ticket.title, data["title"]))
        ticket.title = data["title"]

    # --- Details
    if "details" in data and data["details"] != ticket.details:
        updated_fields.append(("details", ticket.details, data["details"]))
        ticket.details = data["details"]

    # --- Priority
    if "priority" in data and data["priority"] != ticket.priority:
        updated_fields.append(("priority", ticket.priority, data["priority"]))
        ticket.priority = data["priority"]

    # --- Status
    if "status" in data and data["status"] != ticket.status:
        old_status = ticket.status
        new_status = data["status"]
        updated_fields.append(("status", old_status, new_status))
        # Helper call se status + log dono handle ho jaye ga
        update_ticket_status(ticket.id, new_status, updater_id)
        if new_status.lower() == "completed" and not ticket.completed_at:
            ticket.completed_at = datetime.utcnow()


    # --- Category
    category_changed = False
    if "category_id" in data and str(data["category_id"]) != str(ticket.category_id):
        updated_fields.append(("category_id", ticket.category_id, data["category_id"]))
        ticket.category_id = data["category_id"]
        category_changed = True

    clinic_id = 1
    location_map = get_clinic_locations_map(clinic_id)

    # --- Location ID
    if "location_id" in data:
        new_location_id = data["location_id"]

        if new_location_id == "" or new_location_id is None:
            new_location_id = None
        else:
            try:
                new_location_id = int(new_location_id)
            except (ValueError, TypeError):
                return jsonify({
                    "error": "Invalid location_id format. Must be an integer or null"
                }), 400

        if ticket.location_id != new_location_id:
            old_location_name = location_map.get(
                ticket.location_id,
                f"Location #{ticket.location_id}"
            )
            new_location_name = location_map.get(
                new_location_id,
                f"Location #{new_location_id}"
            )

            updated_fields.append((
                "location",
                old_location_name,
                new_location_name
            ))

            ticket.location_id = new_location_id


    # --- Due Date
    if "due_date" in data:
        try:
            new_due_date = datetime.strptime(data["due_date"], "%Y-%m-%d").date()
            if ticket.due_date != new_due_date:
                updated_fields.append(("due_date", str(ticket.due_date), str(new_due_date)))
                ticket.due_date = new_due_date
        except ValueError:
            return jsonify({"error": "Invalid due_date format. Use YYYY-MM-DD"}), 400
    # --- Assignee Change (main part)
    if "assign_to" in data:
        new_assign_to = int(data["assign_to"])
        assignment = TicketAssignment.query.filter_by(ticket_id=ticket.id).first()
        old_assign_to = assignment.assign_to if assignment else None
        if old_assign_to != new_assign_to:
            updated_fields.append(("assign_to", old_assign_to, new_assign_to))
            if assignment:
                assignment.assign_to = new_assign_to
                assignment.assign_by = updater_id
            else:
                assignment = TicketAssignment(
                    ticket_id=ticket.id,
                    assign_to=new_assign_to,
                    assign_by=updater_id
                )
                db.session.add(assignment)
            # ✅ Save assignment log
            update_ticket_assignment_log(ticket.id, old_assign_to, new_assign_to, updater_id)

    db.session.commit()

    # -----------------------------
    # 📩 Helper: Get recipients (assign_by + assign_to + ALL followups)
    def get_notification_recipients(ticket, updater_id):
        recipients = set()
        assignment = TicketAssignment.query.filter_by(ticket_id=ticket.id).first()
        assign_by = assignment.assign_by if assignment else None
        assign_to = assignment.assign_to if assignment else None

        if assign_by and assign_by != updater_id:
            recipients.add(assign_by)
        if assign_to and assign_to != updater_id:
            recipients.add(assign_to)

        # 🔹 Explicitly include ALL followups (except updater)
        followups = TicketFollowUp.query.filter_by(ticket_id=ticket.id).all()
        print("DEBUG followups in DB:", [(f.user_id, f.note) for f in followups])
        for fu in followups:
            if fu.user_id != updater_id:   # ✅ SKIP updater
                recipients.add(fu.user_id)

        return list(recipients)

    # -----------------------------
    # Notify targeted users about changes
    if updated_fields:
        recipients = get_notification_recipients(ticket, updater_id)
        change_summary = ", ".join([f"{f}: {o}  {n}" for f, o, n in updated_fields])

        # Fetch assignment once for debug printing
        assignment = TicketAssignment.query.filter_by(ticket_id=ticket.id).first()
        assign_by = assignment.assign_by if assignment else None
        assign_to = assignment.assign_to if assignment else None

        print("\n📩 Notification Debug Log")
        print(f"➡️ Updater ID: {updater_id}")
        print(f"➡️ assign_by: {assign_by}, assign_to: {assign_to}")
        print(f"➡️ Recipients selected: {recipients}")
        print(f"➡️ Changes: {change_summary}\n")

        for uid in recipients:
            user_info = get_user_info_by_id(uid)
            if not user_info:
                continue

            # Debug role printing
            role = []
            if uid == assign_by:
                role.append("ASSIGN_BY")
            if uid == assign_to:
                role.append("ASSIGN_TO")
            if TicketFollowUp.query.filter_by(ticket_id=ticket.id, user_id=uid).first():
                role.append("FOLLOWUP")

            print(f"✅ Email/Notif sent to user_id={uid}, "
                  f"username={user_info.get('username')}, "
                  f"roles={','.join(role) if role else 'N/A'}")

            # Send email + notification
            send_update_ticket_email(ticket, user_info, updater_info, updated_fields)
            create_notification(
                ticket_id=ticket.id,
                receiver_id=uid,
                sender_id=updater_id,
                notification_type="update",
                message=f"Ticket updated ({change_summary})"
            )

    # -----------------------------
    # Handle follower_ids (replace all followers with provided list)
    if "follower_ids" in data:
        follower_ids = data.get("follower_ids")
        if isinstance(follower_ids, list):
            # Remove duplicates
            follower_ids = list(set([int(uid) for uid in follower_ids if uid]))
            
            # Get current followers
            current_followups = TicketFollowUp.query.filter_by(ticket_id=ticket.id).all()
            current_follower_ids = {fu.user_id for fu in current_followups}
            
            # Find followers to remove (in current but not in new list)
            followers_to_remove = current_follower_ids - set(follower_ids)
            
            # Find followers to add (in new list but not in current)
            followers_to_add = set(follower_ids) - current_follower_ids
            
            # Remove followers
            for uid in followers_to_remove:
                if uid == updater_id:  # Skip if updater removing himself
                    continue
                fu = TicketFollowUp.query.filter_by(ticket_id=ticket.id, user_id=uid).first()
                if fu:
                    db.session.delete(fu)
                    db.session.commit()
                    
                    follower_info = get_user_info_by_id(uid)
                    follower_name = follower_info.get("username") if follower_info else f"User {uid}"
                    
                    # Notify assignees about removed follower
                    assignment = TicketAssignment.query.filter_by(ticket_id=ticket.id).first()
                    recipients = set()
                    if assignment:
                        if assignment.assign_by and assignment.assign_by != updater_id:
                            recipients.add(assignment.assign_by)
                        if assignment.assign_to and assignment.assign_to != updater_id:
                            recipients.add(assignment.assign_to)
                    
                    for rid in recipients:
                        user_info = get_user_info_by_id(rid)
                        if user_info:
                            send_update_ticket_email(
                                ticket,
                                user_info,
                                updater_info,
                                [("followup", "", f"{follower_name} unfollowed this ticket")]
                            )
                            create_notification(
                                ticket_id=ticket.id,
                                receiver_id=rid,
                                sender_id=updater_id,
                                notification_type="followup",
                                message=f"{follower_name} unfollowed this ticket"
                            )
            
            # Add new followers
            for uid in followers_to_add:
                if uid == updater_id:  # Skip if updater adding himself
                    continue
                existing = TicketFollowUp.query.filter_by(ticket_id=ticket.id, user_id=uid).first()
                if not existing:
                    fu = TicketFollowUp(
                        ticket_id=ticket.id,
                        user_id=uid,
                        note="Added as follow-up user",
                        created_at=datetime.utcnow()
                    )
                    db.session.add(fu)
                    db.session.commit()
                    
                    follower_info = get_user_info_by_id(uid)
                    follower_name = follower_info.get("username") if follower_info else f"User {uid}"
                    
                    # Notify new follower
                    create_notification(
                        ticket_id=ticket.id,
                        receiver_id=uid,
                        sender_id=updater_id,
                        notification_type="followup",
                        message=f"You are now following ticket #{ticket.id}"
                    )
                    
                    # Notify assignees about new follower
                    assignment = TicketAssignment.query.filter_by(ticket_id=ticket.id).first()
                    recipients = set()
                    if assignment:
                        if assignment.assign_by and assignment.assign_by != updater_id:
                            recipients.add(assignment.assign_by)
                        if assignment.assign_to and assignment.assign_to != updater_id:
                            recipients.add(assignment.assign_to)
                    
                    for rid in recipients:
                        user_info = get_user_info_by_id(rid)
                        if user_info:
                            send_update_ticket_email(
                                ticket,
                                user_info,
                                updater_info,
                                [("followup", "", f"{follower_name} started following this ticket")]
                            )
                            create_notification(
                                ticket_id=ticket.id,
                                receiver_id=rid,
                                sender_id=updater_id,
                                notification_type="followup",
                                message=f"{follower_name} has been added as a follow-up user"
                            )
    
    # -----------------------------
    # Handle newly added followups
    if "followup_user_ids_add" in data:
        ids = [int(uid.strip()) for uid in str(data["followup_user_ids_add"]).split(",") if uid.strip().isdigit()]
        for uid in ids:
            if uid == updater_id:   # ✅ skip if updater adding himself
                continue
            existing = TicketFollowUp.query.filter_by(ticket_id=ticket.id, user_id=uid).first()
            if not existing:
                fu = TicketFollowUp(
                    ticket_id=ticket.id,
                    user_id=uid,
                    note="Added as follow-up user",
                    created_at=datetime.utcnow()
                )
                db.session.add(fu)
                db.session.commit()

                follower_info = get_user_info_by_id(uid)
                follower_name = follower_info.get("username") if follower_info else f"User {uid}"

                assignment = TicketAssignment.query.filter_by(ticket_id=ticket.id).first()
                recipients = {uid}
                if assignment:
                    if assignment.assign_by and assignment.assign_by != updater_id:
                        recipients.add(assignment.assign_by)
                    if assignment.assign_to and assignment.assign_to != updater_id:
                        recipients.add(assignment.assign_to)

                for rid in recipients:
                    user_info = get_user_info_by_id(rid)
                    if user_info:
                        send_update_ticket_email(
                            ticket,
                            user_info,
                            updater_info,
                            [("followup", "", f"{follower_name} started following this ticket")]
                        )
                        create_notification(
                            ticket_id=ticket.id,
                            receiver_id=rid,
                            sender_id=updater_id,
                            notification_type="followup",
                            message=f"{follower_name} has been added as a follow-up user"
                        )

    # -----------------------------
    # Handle removed followups

    if "followup_user_ids_remove" in data:
        ids = [int(uid.strip()) for uid in str(data["followup_user_ids_remove"]).split(",") if uid.strip().isdigit()]
        for uid in ids:
            if uid == updater_id:   # ✅ skip if updater removing himself
                continue
            fu = TicketFollowUp.query.filter_by(ticket_id=ticket.id, user_id=uid).first()
            if fu:
                db.session.delete(fu)
                db.session.commit()

                follower_info = get_user_info_by_id(uid)
                follower_name = follower_info.get("username") if follower_info else f"User {uid}"

                assignment = TicketAssignment.query.filter_by(ticket_id=ticket.id).first()
                recipients = {uid}
                if assignment:
                    if assignment.assign_by and assignment.assign_by != updater_id:
                        recipients.add(assignment.assign_by)
                    if assignment.assign_to and assignment.assign_to != updater_id:
                        recipients.add(assignment.assign_to)

                for rid in recipients:
                    user_info = get_user_info_by_id(rid)
                    if user_info:
                        send_update_ticket_email(
                            ticket,
                            user_info,
                            updater_info,
                            [("followup", "", f"{follower_name} unfollowed this ticket")]
                        )
                        create_notification(
                            ticket_id=ticket.id,
                            receiver_id=rid,
                            sender_id=updater_id,
                            notification_type="followup",
                            message=f"{follower_name} unfollowed this ticket"
                        )

        # -----------------------------
    # Handle category assignee email if category changed
    if category_changed and ticket.category_id:
        category = Category.query.get(ticket.category_id)
        if category and category.assignee_id:
            assignee_info = get_user_info_by_id(category.assignee_id)
            assigner_info = get_user_info_by_id(ticket.user_id)
            if assignee_info:
                send_assign_email(ticket, assignee_info, assigner_info)
                create_notification(
                    ticket_id=ticket.id,
                    receiver_id=assignee_info["id"],
                    sender_id=updater_id,
                    notification_type="assign",
                    message=f"Assigned"
                )

    # -----------------------------
    # Handle file uploads
    uploaded_files = []
    if "files" in request.files:
        for f in request.files.getlist("files"):
            if f.filename:
                try:
                    file_url = upload_to_s3(f, folder=f"tickets/{ticket.id}")
                    tf = TicketFile(ticket_id=ticket.id, file_url=file_url, file_name=f.filename)
                    db.session.add(tf)
                    uploaded_files.append({"name": f.filename, "url": file_url})
                except Exception as e:
                    db.session.rollback()
                    return jsonify({"error": str(e)}), 500
        db.session.commit()

    return jsonify({
        "success": True,
        "message": "Ticket updated and notifications sent",
        "ticket": {
            "id": ticket.id,
            "title": ticket.title,
            "details": ticket.details,
            "priority": ticket.priority,
            "status": ticket.status,
            "due_date": ticket.due_date,
            "category_id": ticket.category_id,
            "assignees": [
                {
                    "assign_by": a.assign_by,
                    "assign_to": a.assign_to,
                    "assigned_at": a.assigned_at
                }
                for a in TicketAssignment.query.filter_by(ticket_id=ticket.id).all()
            ],
            "tags": [tag.tag_name for tag in TicketTag.query.filter_by(ticket_id=ticket.id).all()],
            "files": [{"name": f.file_name, "url": f.file_url} for f in TicketFile.query.filter_by(ticket_id=ticket.id).all()],
            "followups": [
                {
                    "note": f.note,
                    "user_id": f.user_id,
                    "created_at": f.created_at
                }
                for f in TicketFollowUp.query.filter_by(ticket_id=ticket.id).all()
            ]
        }
    })


# ─────────────────────────────────────────────
# Assign Ticket
@ticket_bp.route("/assign", methods=["POST"])
@require_api_key
@validate_token
def assign_ticket():
    data = request.get_json()

    ticket_id = data.get("ticket_id")
    assign_to = data.get("assign_to")
    assign_by = data.get("assign_by")
    priority = data.get("priority")  # Optional: Low | Medium | High

    ticket = Ticket.query.get(ticket_id)
    if not ticket:
        return jsonify({"error": "Ticket not found"}), 404

    # Prevent duplicate assignment
    existing = TicketAssignment.query.filter_by(ticket_id=ticket_id, assign_to=assign_to).first()
    if existing:
        return jsonify({"error": f"Ticket already assigned to user {assign_to}"}), 400

    # Update priority if provided
    if priority:
        ticket.priority = priority

    # ✅ Update status to in_progress
    ticket.status = "in_progress"
    db.session.commit()

    # Save assignment
    assignment = TicketAssignment(
        ticket_id=ticket_id,
        assign_to=assign_to,
        assign_by=assign_by
    )
    db.session.add(assignment)
    db.session.commit()

    # ─── Send email in background ───
    user_info = get_user_info_by_id(assign_to)
    assigner_info = get_user_info_by_id(assign_by)

    if user_info:
        send_assign_email(ticket, user_info, assigner_info)

        # ✅ Save notification
        create_notification(
            ticket_id=ticket.id,
            receiver_id=assign_to,
            sender_id=assign_by,
            notification_type="assign",
            message=f"Assigned by {assigner_info.get('username') if assigner_info else 'System'}"
        )

    return jsonify({
        "message": "Ticket assigned successfully",
        "ticket": {
            "id": ticket.id,
            "title": ticket.title,
            "status": ticket.status,
            "priority": getattr(ticket, "priority", None),
            "assign_to": assign_to,
            "assign_by": assign_by,
            "assigned_at": assignment.assigned_at
        }
    }), 200

# ─────────────────────────────────────────────
# Get All Tickets (with Pagination)
@ticket_bp.route("/tickets", methods=["GET"])
@require_api_key
@validate_token
def get_tickets():
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 10, type=int)

    # ✅ Filters
    status = request.args.get("status")
    category_id = request.args.get("category_id", type=int)
    assign_to = request.args.get("assign_to", type=int)
    assign_by = request.args.get("assign_by", type=int)
    followup = request.args.get("followup", type=int)
    tag = request.args.get("tag", type=int)
    created_by = request.args.get("created_by", type=int)
    search = request.args.get("search", "").strip()
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")

    query = Ticket.query

    if status:
        # Handle multiple statuses (comma-separated)
        status_list = [s.strip() for s in status.split(",") if s.strip()]
        
        # Only proceed if we have valid statuses
        if status_list:
            # Create filters for each status (case-insensitive, handles variations)
            status_filters = []
            for s in status_list:
                # Normalize: handle "in progress" vs "in_progress" variations
                s_normalized = s.lower().replace(" ", "_")
                status_filters.append(
                    or_(
                        Ticket.status.ilike(f"%{s}%"),
                        func.lower(Ticket.status).like(f"%{s_normalized}%"),
                        func.lower(Ticket.status).like(f"%{s_normalized.replace('_', ' ')}%")
                    )
                )
            
            query = query.filter(or_(*status_filters))
    if category_id:
        query = query.filter(Ticket.category_id == category_id)
    if start_date:
        query = query.filter(Ticket.created_at >= start_date)
    if end_date:
        query = query.filter(Ticket.created_at <= end_date)
    if search:
        # Check if search term is numeric (could be a ticket ID)
        search_conditions = [
            Ticket.title.ilike(f"%{search}%"),
            Ticket.details.ilike(f"%{search}%")
        ]
        
        # If search is numeric, also search by ticket ID
        if search.isdigit():
            search_conditions.append(Ticket.id == int(search))
        
        query = query.filter(or_(*search_conditions))

    if created_by:
        query = query.filter(Ticket.user_id == created_by)
    if assign_to:
        ticket_ids = [a.ticket_id for a in TicketAssignment.query.filter_by(assign_to=assign_to).all()]
        query = query.filter(Ticket.id.in_(ticket_ids))
    if assign_by:
        ticket_ids = [a.ticket_id for a in TicketAssignment.query.filter_by(assign_by=assign_by).all()]
        query = query.filter(Ticket.id.in_(ticket_ids))
    if followup:
        ticket_ids = [f.ticket_id for f in TicketFollowUp.query.filter_by(user_id=followup).all()]
        query = query.filter(Ticket.id.in_(ticket_ids))
    if tag:
        ticket_ids = [t.ticket_id for t in TicketTag.query.filter_by(tag_name=str(tag)).all()]
        query = query.filter(Ticket.id.in_(ticket_ids))

    query = query.order_by(Ticket.created_at.desc())
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    tickets = pagination.items

    result = []
    for t in tickets:
        created_by_info = get_user_info_by_id(t.user_id) if t.user_id else None

        assignments = TicketAssignment.query.filter_by(ticket_id=t.id).all()
        assignees = []
        for a in assignments:
            assign_by_info = get_user_info_by_id(a.assign_by) if a.assign_by else None
            assign_to_info = get_user_info_by_id(a.assign_to) if a.assign_to else None
            assignees.append({
                "assign_by": a.assign_by,
                "assign_by_username": assign_by_info["username"] if assign_by_info else None,
                "assign_to": a.assign_to,
                "assign_to_username": assign_to_info["username"] if assign_to_info else None,
                "assigned_at": a.assigned_at
            })

        files = [{"name": f.file_name, "url": f.file_url}
                 for f in TicketFile.query.filter_by(ticket_id=t.id).all()]
        tags = [tag.tag_name for tag in TicketTag.query.filter_by(ticket_id=t.id).all()]

        comments = []
        for c in TicketComment.query.filter_by(ticket_id=t.id).order_by(TicketComment.created_at.desc()).all():
            u_info = get_user_info_by_id(c.user_id)
            comments.append({
                "user_id": c.user_id,
                "username": u_info["username"] if u_info else None,
                "comment": c.comment,
                "created_at": c.created_at
            })

        followups = []
        for f in TicketFollowUp.query.filter_by(ticket_id=t.id).all():
            u_info = get_user_info_by_id(f.user_id) if f.user_id else None
            followups.append({
                "id": f.id,
                "note": f.note,
                "user_id": f.user_id,
                "username": u_info.get("username") if u_info else None,
                "followup_date": f.followup_date,
                "created_at": f.created_at
            })

        category = None
        if getattr(t, "category_id", None):
            cat = Category.query.get(t.category_id)
            if cat:
                category = {"id": cat.id, "name": cat.name, "is_active": cat.is_active}

        # ✅ Status Logs
        status_logs = []
        for log in TicketStatusLog.query.filter_by(ticket_id=t.id).order_by(TicketStatusLog.changed_at.desc()).all():
            u_info = get_user_info_by_id(log.changed_by)
            status_logs.append({
                "old_status": log.old_status,
                "new_status": log.new_status,
                "changed_by": log.changed_by,
                "changed_by_username": u_info["username"] if u_info else None,
                "changed_at": log.changed_at
            })

        # ✅ Project Information (if ticket is linked to a project)
        project_info = None
        project_ticket = ProjectTicket.query.filter_by(ticket_id=t.id).first()
        if project_ticket:
            project = Project.query.get(project_ticket.project_id)
            if project:
                project_info = {
                    "id": project.id,
                    "name": project.name,
                    "status": project.status,
                    "priority": project.priority,
                    "color": project.color
                }

        result.append({
            "id": t.id,
            "title": t.title,
            "details": t.details,
            "priority": t.priority,
            "status": t.status,
            "due_date": t.due_date,
            "created_at": t.created_at,
            "completed_at": getattr(t, "completed_at", None),
            "created_by": created_by_info,
            "assignees": assignees,
            "files": files,
            "tags": tags,
            "comments": comments,
            "followups": followups,
            "category": category,
            "status_logs": status_logs,
            "project": project_info  # Project information if ticket is linked to a project
        })

    return jsonify({
        "tickets": result,
        "pagination": {
            "page": pagination.page,
            "per_page": pagination.per_page,
            "total": pagination.total,
            "pages": pagination.pages
        }
    })


# ─────────────────────────────────────────────
# Get Ticket with all details
@ticket_bp.route("/ticket/<int:ticket_id>", methods=["GET"])
# @require_api_key
# @validate_token
def get_ticket(ticket_id):
    ticket = Ticket.query.get(ticket_id)
    if not ticket:
        return jsonify({"error": "Ticket not found"}), 404
    created_by = get_user_info_by_id(ticket.user_id) if ticket.user_id else None
    # --- Assignments (Current state)
    assignments = TicketAssignment.query.filter_by(ticket_id=ticket.id).all()
    assignees = []
    for a in assignments:
        assign_by_info = get_user_info_by_id(a.assign_by) if a.assign_by else None
        assign_to_info = get_user_info_by_id(a.assign_to) if a.assign_to else None
        assignees.append({
            "assign_by": a.assign_by,
            "assign_by_username": assign_by_info["username"] if assign_by_info else None,
            "assign_to": a.assign_to,
            "assign_to_username": assign_to_info["username"] if assign_to_info else None,
            "assigned_at": a.assigned_at
        })
    # --- Assignment Logs (History)
    assignment_logs = []
    for log in TicketAssignmentLog.query.filter_by(ticket_id=ticket.id).order_by(TicketAssignmentLog.changed_at.desc()).all():
        old_user_info = get_user_info_by_id(log.old_assign_to) if log.old_assign_to else None
        new_user_info = get_user_info_by_id(log.new_assign_to) if log.new_assign_to else None
        changed_by_info = get_user_info_by_id(log.changed_by) if log.changed_by else None
        assignment_logs.append({
            "old_assign_to": log.old_assign_to,
            "old_assign_to_username": old_user_info["username"] if old_user_info else None,
            "new_assign_to": log.new_assign_to,
            "new_assign_to_username": new_user_info["username"] if new_user_info else None,
            "changed_by": log.changed_by,
            "changed_by_username": changed_by_info["username"] if changed_by_info else None,
            "changed_at": log.changed_at
        })
    # --- Files
    files = [{"name": f.file_name, "url": f.file_url}
             for f in TicketFile.query.filter_by(ticket_id=ticket.id).all()]
    # --- Tags
    tags = [tag.tag_name for tag in TicketTag.query.filter_by(ticket_id=ticket.id).all()]
    # --- Comments
    comments = []
    for c in TicketComment.query.filter_by(ticket_id=ticket.id).order_by(TicketComment.created_at.desc()).all():
        u_info = get_user_info_by_id(c.user_id) if c.user_id else None
        comments.append({
            "user_id": c.user_id,
            "username": u_info["username"] if u_info else None,
            "comment": c.comment,
            "created_at": c.created_at
        })
    # --- Followups
    followups = []
    for f in TicketFollowUp.query.filter_by(ticket_id=ticket.id).all():
        u_info = get_user_info_by_id(f.user_id) if f.user_id else None
        followups.append({
            "id": f.id,
            "note": f.note,
            "user_id": f.user_id,
            "username": u_info.get("username") if u_info else None,
            "email": u_info.get("email") if u_info else None,
            "followup_date": f.followup_date,
            "created_at": f.created_at
        })
    # --- Category
    category = None
    if getattr(ticket, "category_id", None):
        cat = Category.query.get(ticket.category_id)
        if cat:
            category = {
                "id": cat.id,
                "name": cat.name,
                "is_active": cat.is_active
            }
    # --- Status Logs
    status_logs = []
    for log in TicketStatusLog.query.filter_by(ticket_id=ticket.id).order_by(TicketStatusLog.changed_at.desc()).all():
        u_info = get_user_info_by_id(log.changed_by)
        status_logs.append({
            "old_status": log.old_status,
            "new_status": log.new_status,
            "changed_by": log.changed_by,
            "changed_by_username": u_info["username"] if u_info else None,
            "changed_at": log.changed_at
        })
    # --- Contact Form Patient Information
    contact_form_info = None
    contact_form_link = ContactFormTicketLink.query.filter_by(ticket_id=ticket.id).first()
    if contact_form_link and contact_form_link.contact_form:
        contact_form = contact_form_link.contact_form
        contact_form_info = {
            "id": contact_form.id,
            "form_name": contact_form.form_name,
            "name": contact_form.name,
            "phone": contact_form.phone,
            "email": contact_form.email,
            "message": contact_form.message,
            "data": contact_form.data,
            "status": contact_form.status,
            "created_at": contact_form.created_at
        }
    
    # --- Location Details from Auth System
    location_details = None
    if ticket.location_id:
        try:
            AUTH_SYSTEM_URL = os.getenv("AUTH_SYSTEM_URL", "https://api.dental360grp.com/api")
            clinic_id = ticket.clinic_id or 1  # Default to 1 if clinic_id is None
            
            internal_api_url = f"{AUTH_SYSTEM_URL}/clinic_locations/get_all/{clinic_id}"
            print(f"Fetching location details from: {internal_api_url}")
            
            internal_response = requests.get(internal_api_url, timeout=10)
            
            if internal_response.status_code == 200:
                internal_data = internal_response.json()
                all_locations = internal_data.get("locations", [])
                
                # Find the location matching ticket.location_id
                for loc in all_locations:
                    if loc.get("id") == ticket.location_id:
                        location_details = {
                            "id": loc.get("id"),
                            "location_name": loc.get("location_name"),
                            "address": loc.get("address"),
                            "city": loc.get("city"),
                            "state": loc.get("state"),
                            "postal_code": loc.get("postal_code"),
                            "phone": loc.get("phone"),
                            "email": loc.get("email"),
                            "clinic_id": loc.get("clinic_id"),
                            "is_enable": loc.get("is_enable"),
                            "display_name": loc.get("display_name"),
                            "greeting_message": loc.get("greeting_message"),
                            "map_link": loc.get("map_link"),
                            "sip_uri": loc.get("sip_uri")
                        }
                        print(f"✅ Found location details for location_id: {ticket.location_id}")
                        break
                
                if not location_details:
                    print(f"⚠️ Location ID {ticket.location_id} not found in auth system")
            else:
                print(f"⚠️ Failed to fetch locations from auth system: {internal_response.status_code}")
        except Exception as e:
            print(f"⚠️ Error fetching location details from auth system: {e}")
            # Continue without location details
    
    # --- Project Information (if ticket is linked to a project)
    project_info = None
    project_ticket = ProjectTicket.query.filter_by(ticket_id=ticket.id).first()
    if project_ticket:
        project = Project.query.get(project_ticket.project_id)
        if project:
            created_by_project = get_user_info_by_id(project.created_by) if project.created_by else None
            project_tags = [tag.tag_name for tag in ProjectTag.query.filter_by(project_id=project.id).all()]
            project_info = {
                "id": project.id,
                "name": project.name,
                "description": project.description,
                "status": project.status,
                "priority": project.priority,
                "color": project.color,
                "due_date": project.due_date.isoformat() if project.due_date else None,
                "created_by": created_by_project,
                "tags": project_tags
            }
    
    # --- Final Response
    result = {
        "id": ticket.id,
        "title": ticket.title,
        "details": ticket.details,
        "priority": ticket.priority,
        "status": ticket.status,
        "due_date": ticket.due_date,
        "created_at": ticket.created_at,
        "completed_at": ticket.completed_at,
        "location_id": ticket.location_id,
        "location_details": location_details,  # Location details from auth system
        "clinic_id": ticket.clinic_id,
        "created_by": created_by,
        "assignees": assignees,
        "assignment_logs": assignment_logs,   # :white_check_mark: NEW
        "files": files,
        "tags": tags,
        "comments": comments,
        "followups": followups,
        "category": category,
        "status_logs": status_logs,
        "contact_form_info": contact_form_info,  # :white_check_mark: Patient contact information from contact form
        "project": project_info  # Project information if ticket is linked to a project
    }
    return jsonify(result)


# ─────────────────────────────────────────────
# Add Ticket Activity Comment, Tags 
@ticket_bp.route("/ticket/activity/<int:ticket_id>", methods=["POST"])
@require_api_key
@validate_token
def add_ticket_activity(ticket_id):
    ticket = Ticket.query.get(ticket_id)
    if not ticket:
        return jsonify({"error": "Ticket not found"}), 404

    data = request.json
    user_id = data.get("user_id")      # jis user ne action kiya
    comment_text = data.get("comment") # optional
    user_ids = data.get("user_ids")    # optional: tagging users e.g. [12, 15, 20]

    response_data = {
        "success": True,
        "comment": None,
        "tags": []
    }

    # ─── Add Comment ───
    comment = None
    if comment_text:
        comment = TicketComment(
            ticket_id=ticket_id,
            user_id=user_id,
            comment=comment_text
        )
        db.session.add(comment)
        db.session.commit()

        commenter_info = get_user_info_by_id(user_id)

        response_data["comment"] = {
            "id": comment.id,
            "user_id": comment.user_id,
            "username": commenter_info["username"] if commenter_info else None,
            "comment": comment.comment,
            "created_at": comment.created_at
        }

        # ✅ Collect recipients
        recipients = set()

        # 1. Saare followups (except commenter)
        followups = TicketFollowUp.query.filter_by(ticket_id=ticket_id).all()
        print("DEBUG followups in DB:", [(f.user_id, f.note) for f in followups])
        for fu in followups:
            if fu.user_id and fu.user_id != user_id:
                recipients.add(fu.user_id)

        # 2. assign_by / assign_to (except commenter)
        assignment = TicketAssignment.query.filter_by(ticket_id=ticket.id).first()
        if assignment:
            if assignment.assign_by and assignment.assign_by != user_id:
                recipients.add(assignment.assign_by)
            if assignment.assign_to and assignment.assign_to != user_id:
                recipients.add(assignment.assign_to)

        # ✅ Send email + notification to all recipients
        for uid in recipients:
            target_info = get_user_info_by_id(uid)
            if not target_info:
                continue

            send_update_ticket_email(
                ticket,
                target_info,
                commenter_info,
                [("comment", "-", comment.comment)]
            )
            create_notification(
                ticket_id=ticket.id,
                receiver_id=uid,
                sender_id=user_id,
                notification_type="comment",
                message=f"New comment added"
            )

    # ─── Add Tags ───
    if user_ids and isinstance(user_ids, list):
        added_tags = []
        for uid in user_ids:
            # check if tag already exists
            existing_tag = TicketTag.query.filter_by(ticket_id=ticket.id, tag_name=str(uid)).first()
            if not existing_tag:
                tag = TicketTag(ticket_id=ticket.id, tag_name=str(uid))
                db.session.add(tag)
                db.session.commit()
                added_tags.append(uid)

            # ✅ Always send email (new tag or already exists)
            user_info = get_user_info_by_id(uid)
            assigner_info = get_user_info_by_id(user_id)

            if user_info:
                send_tag_email(
                    ticket,
                    user_info,
                    assigner_info,
                    comment=comment
                )
                create_notification(
                    ticket_id=ticket.id,
                    receiver_id=uid,
                    sender_id=user_id,
                    notification_type="tag",
                    message=f"Tagged"
                )

        response_data["tags"] = added_tags

    # Agar kuch bhi na bheja jaye
    if not comment_text and not user_ids:
        return jsonify({"error": "Either comment or user_ids required"}), 400

    return jsonify(response_data), 200

# ─────────────────────────────────────────────
# Delete Ticket
@ticket_bp.route("/ticket/<int:ticket_id>", methods=["DELETE"])
@require_api_key
@validate_token
def delete_ticket(ticket_id):
    ticket = Ticket.query.get(ticket_id)
    if not ticket:
        return jsonify({"error": "Ticket not found"}), 404
    db.session.delete(ticket)
    db.session.commit()
    return jsonify({"success": True, "message": "Ticket deleted"})


# ─────────────────────────────────────────────
# Filtered Tickets API
@ticket_bp.route("/tickets/filter", methods=["GET"])
@require_api_key
@validate_token
def filter_tickets():
    user_id = request.args.get("user_id", type=int)
    if not user_id:
        return jsonify({"error": "user_id is required"}), 400

    # ✅ Get user info
    user_info = get_user_info_by_id(user_id)
    if not user_info:
        return jsonify({"error": "Invalid user"}), 404

    user_role = user_info.get("role", "").lower()

    # ✅ Filters
    status = request.args.get("status")
    category_id = request.args.get("category_id", type=int)
    start_date = request.args.get("start_date")  # e.g. 2025-09-01
    end_date = request.args.get("end_date")      # e.g. 2025-09-17
    search = request.args.get("search", "").strip()

    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 10, type=int)

    # ✅ Base query
    query = Ticket.query

    # Admin → all tickets, Normal → created + assigned only
    if user_role not in ["admin", "superadmin"]:
        created = Ticket.query.filter_by(user_id=user_id)
        assigned_ids = [a.ticket_id for a in TicketAssignment.query.filter_by(assign_to=user_id).all()]
        query = query.filter(or_(Ticket.id.in_(assigned_ids), Ticket.user_id == user_id))

    # ✅ Apply filters
    if status:
        # Handle multiple statuses (comma-separated)
        status_list = [s.strip() for s in status.split(",") if s.strip()]
        
        # Only proceed if we have valid statuses
        if status_list:
            # Create filters for each status (case-insensitive, handles variations)
            status_filters = []
            for s in status_list:
                # Normalize: handle "in progress" vs "in_progress" variations
                s_normalized = s.lower().replace(" ", "_")
                status_filters.append(
                    or_(
                        Ticket.status.ilike(f"%{s}%"),
                        func.lower(Ticket.status).like(f"%{s_normalized}%"),
                        func.lower(Ticket.status).like(f"%{s_normalized.replace('_', ' ')}%")
                    )
                )
            
            query = query.filter(or_(*status_filters))
    if category_id:
        query = query.filter(Ticket.category_id == category_id)
    if start_date:
        query = query.filter(Ticket.created_at >= start_date)
    if end_date:
        query = query.filter(Ticket.created_at <= end_date)
    if search:
        query = query.filter(or_(
            Ticket.title.ilike(f"%{search}%"),
            Ticket.details.ilike(f"%{search}%")
        ))

    # ✅ Order by latest
    query = query.order_by(Ticket.created_at.desc())

    # ✅ Pagination
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    tickets = pagination.items

    result = []
    for t in tickets:
        created_by = get_user_info_by_id(t.user_id) if t.user_id else None

        assignments = TicketAssignment.query.filter_by(ticket_id=t.id).all()
        assignees = []
        for a in assignments:
            assign_by_info = get_user_info_by_id(a.assign_by) if a.assign_by else None
            assign_to_info = get_user_info_by_id(a.assign_to) if a.assign_to else None
            assignees.append({
                "assign_by": a.assign_by,
                "assign_by_username": assign_by_info["username"] if assign_by_info else None,
                "assign_to": a.assign_to,
                "assign_to_username": assign_to_info["username"] if assign_to_info else None,
                "assigned_at": a.assigned_at
            })

        files = [{"name": f.file_name, "url": f.file_url} for f in TicketFile.query.filter_by(ticket_id=t.id).all()]
        tags = [tag.tag_name for tag in TicketTag.query.filter_by(ticket_id=t.id).all()]

        comments = []
        for c in TicketComment.query.filter_by(ticket_id=t.id).order_by(TicketComment.created_at.desc()).all():
            u_info = get_user_info_by_id(c.user_id) if c.user_id else None
            comments.append({
                "user_id": c.user_id,
                "username": u_info["username"] if u_info else None,
                "comment": c.comment,
                "created_at": c.created_at
            })

        category = None
        if getattr(t, "category_id", None):
            cat = Category.query.get(t.category_id)
            if cat:
                category = {"id": cat.id, "name": cat.name}

        role = None
        if user_role not in ["admin", "superadmin"]:
            role = "creator" if t.user_id == user_id else "assignee"

        result.append({
            "id": t.id,
            "title": t.title,
            "details": t.details,
            "priority": t.priority,
            "status": t.status,
            "due_date": t.due_date,
            "created_at": t.created_at,
            "completed_at": getattr(t, "completed_at", None),
            "created_by": created_by,
            "assignees": assignees,
            "files": files,
            "tags": tags,
            "comments": comments,
            "category": category,
            "role": role
        })

    return jsonify({
        "tickets": result,
        "total": pagination.total,
        "page": pagination.page,
        "per_page": pagination.per_page,
        "pages": pagination.pages
    })


def analyze_email_issue_with_llm(email_content: str) -> str:
    """
    Analyze email content using LLM to extract the main issue/problem.
    Returns a concise summary of what the issue is.
    """
    if not email_content or len(email_content.strip()) < 10:
        return email_content
    
    try:
        system_prompt = (
            "Extract the main issue or problem from this email. "
            "CRITICAL: Return ONLY the issue description, NO analysis, NO explanations, NO prefixes. "
            "Do NOT say 'the issue is' or 'the problem is' - just state the issue directly. "
            "Do NOT include greetings, signatures, or pleasantries. "
            "If asking for help, state what help is needed. "
            "If reporting a problem, state what the problem is. "
            "Keep it brief and actionable (2-3 sentences maximum). "
            "Output ONLY the issue text, nothing else."
        )
        
        user_prompt = f"""Extract ONLY the main issue from this email. Return ONLY the issue description:

{email_content[:2000]}"""
        
        response = llm_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            max_tokens=200
        )
        
        analyzed_issue = response.choices[0].message.content.strip()
        print(f"🔍 Raw analyzed issue: {analyzed_issue[:200]}")
        
        # Handle structured output format
        final_match = re.search(
            r"<\|channel\|>final<\|message\|>(.*?)(?:<\|channel\|>|$)",
            analyzed_issue,
            re.DOTALL | re.IGNORECASE
        )
        if final_match:
            analyzed_issue = final_match.group(1).strip()
        
        # Clean up any analysis artifacts
        analyzed_issue = re.sub(r"<\|[^|]+\|>", "", analyzed_issue).strip()
        
        # Remove common analysis prefixes
        analysis_prefixes = [
            "the issue is",
            "the problem is",
            "the main issue",
            "the problem",
            "issue:",
            "problem:",
            "based on the email",
            "the email indicates",
            "this email shows",
            "the user reports",
            "the user is reporting"
        ]
        
        for prefix in analysis_prefixes:
            if analyzed_issue.lower().startswith(prefix):
                analyzed_issue = analyzed_issue[len(prefix):].strip()
                if analyzed_issue.startswith(":"):
                    analyzed_issue = analyzed_issue[1:].strip()
                break
        
        # Remove analysis text patterns
        analysis_patterns = [
            r"analysis[:\s]*",
            r"the email[:\s]*",
            r"this email[:\s]*",
            r"we need to[:\s]*",
            r"the user says[:\s]*",
            r"let's parse[:\s]*"
        ]
        
        for pattern in analysis_patterns:
            analyzed_issue = re.sub(pattern, "", analyzed_issue, flags=re.IGNORECASE)
        
        analyzed_issue = analyzed_issue.strip()
        
        # If result is too short or looks like analysis, use fallback
        if (len(analyzed_issue) < 10 or 
            "analysis" in analyzed_issue.lower()[:100] or
            "the email" in analyzed_issue.lower()[:100] or
            "this email" in analyzed_issue.lower()[:100] or
            "we need to" in analyzed_issue.lower()[:100] or
            "the conversation shows" in analyzed_issue.lower()[:100]):
            print(f"⚠️ Analyzed issue contains analysis text, using fallback")
            # Fallback: use first meaningful sentence from content
            sentences = email_content.split('.')
            for sentence in sentences:
                sentence = sentence.strip()
                if len(sentence) > 20 and len(sentence) < 200:
                    return sentence
            return email_content[:200] if email_content else "(no content)"
        
        print(f"✅ Final analyzed issue: {analyzed_issue[:200]}")
        return analyzed_issue
        
    except Exception as e:
        print(f"⚠️ Error analyzing email issue with LLM: {e}")
        # Fallback: use first meaningful sentence
        sentences = email_content.split('.')
        for sentence in sentences:
            sentence = sentence.strip()
            if len(sentence) > 20 and len(sentence) < 200:
                return sentence
        return email_content[:200] if email_content else "(no content)"


import re
def sanitize_oss_output(text: str) -> str:
    # Remove <|...|> tokens
    text = re.sub(r"<\|[^|]+\|>", " ", text)

    # Remove common OSS channel words
    text = re.sub(
        r"\b(assistant|final|system|user|message|response|channel|start|end)\b",
        " ",
        text,
        flags=re.IGNORECASE
    )

    # Remove instruction echoes and analysis patterns
    text = re.sub(
        r"(return only the title\.?|only return the title\.?|title:|subject:|analysis|here is|based on|the title is|we need|user wants)",
        " ",
        text,
        flags=re.IGNORECASE
    )

    # Remove analysis text patterns (like "Analysisuser Wants A Ticket Title")
    # Split on word boundaries and remove "analysis" prefix if present
    words = text.split()
    if len(words) > 0:
        first_word_lower = words[0].lower()
        # Check if first word contains "analysis" (like "Analysisuser")
        if "analysis" in first_word_lower:
            # Remove "analysis" prefix from first word
            cleaned_first = re.sub(r"analysis", "", first_word_lower, flags=re.IGNORECASE)
            if cleaned_first:
                words[0] = cleaned_first.capitalize()
            else:
                words = words[1:]  # Remove the word entirely if it was just "analysis"
        # Also check for "wants" pattern (like "Wants A Ticket Title")
        if len(words) > 1 and words[1].lower() in ["wants", "needs", "requires"]:
            # Skip "wants/needs/requires" and the following words if they're generic
            if len(words) > 2 and words[2].lower() in ["a", "an", "the", "ticket", "title"]:
                # This looks like analysis text, extract meaningful words instead
                words = [w for w in words if w.lower() not in ["wants", "needs", "requires", "a", "an", "the", "ticket", "title"]]
        text = " ".join(words)

    # Normalize spaces
    text = re.sub(r"\s+", " ", text).strip()

    return text


def generate_ticket_title_with_llm(issue_summary: str,
                                   sender_name: str = None,
                                   sender_email: str = None) -> str:
    """
    Generate a short IT helpdesk ticket title.
    - Tries LLM first
    - Safely extracts a usable title line
    - Falls back to deterministic cleaning
    - Max 50 chars, sentence case
    """

    # -------------------------------
    # Safety check
    # -------------------------------
    if not issue_summary or len(issue_summary.strip()) < 5:
        return "Email issue reported"

    # -------------------------------
    # Helper: fallback title builder
    # -------------------------------
    STOP_WORDS = {
        "and", "the", "a", "an", "in", "to", "for", "of",
        "is", "are", "needs", "needed", "please", "kindly",
        "urgent", "immediate", "attention", "asap",
        "office", "test", "dev"
    }

    def build_fallback_title(text: str) -> str:
        # Try to extract first meaningful sentence or phrase
        # Remove common prefixes
        text = re.sub(r"^(the issue is|the problem is|issue:|problem:)\s*", "", text, flags=re.IGNORECASE)
        text = text.strip()
        
        # Try to get first sentence (up to first period or comma)
        first_sentence_match = re.match(r"^([^.,]{10,60})", text)
        if first_sentence_match:
            first_sentence = first_sentence_match.group(1).strip()
            words = re.findall(r"\b[a-zA-Z]+\b", first_sentence.lower())
            clean = [w for w in words if w not in STOP_WORDS]
            if len(clean) >= 2:
                title = " ".join(clean[:6]).capitalize()
                if len(title) >= 5:
                    return title
        
        # Fallback: extract meaningful words from whole text
        words = re.findall(r"\b[a-zA-Z]+\b", text.lower())
        clean = [w for w in words if w not in STOP_WORDS]

        title = " ".join(clean[:5]).capitalize()
        return title if len(title) >= 5 else "Email issue reported"

    # -------------------------------
    # Helper: extract usable title line
    # -------------------------------
    def extract_title_line(text: str) -> str:
        # First, try to extract from structured format if present
        # Pattern: <|channel|>final<|message|>ACTUAL_TITLE
        final_match = re.search(
            r"<\|channel\|>final<\|message\|>(.*?)(?:<\|channel\|>|$)",
            text,
            re.DOTALL | re.IGNORECASE
        )
        if final_match:
            text = final_match.group(1).strip()
        
        # CRITICAL: Extract quoted titles from analysis text
        # Pattern: "Title here" or 'Title here'
        quoted_titles = re.findall(r'["\']([^"\']{5,50})["\']', text)
        if quoted_titles:
            # Filter out titles that are too long or contain analysis words
            for quoted in quoted_titles:
                quoted = quoted.strip()
                # Skip if empty or too short
                if len(quoted) < 5:
                    continue
                lower_quoted = quoted.lower()
                # Skip if it contains analysis keywords
                if any(kw in lower_quoted for kw in ["analysis", "we need", "generate", "title:", "subject:"]):
                    continue
                word_count = len(quoted.split())
                if 2 <= word_count <= 8:
                    print(f"✅ Found quoted title: {quoted}")
                    return quoted
        
        # Try to extract titles after "maybe" or "or" patterns
        # Pattern: "maybe 'Title'" or "or 'Title'"
        maybe_match = re.search(r'(?:maybe|or|suggest|title:)\s*["\']([^"\']{5,50})["\']', text, re.IGNORECASE)
        if maybe_match:
            title = maybe_match.group(1).strip()
            word_count = len(title.split())
            if 2 <= word_count <= 8:
                print(f"✅ Found title after 'maybe/or': {title}")
                return title
        
        # Split into lines and process
        lines = [l.strip() for l in text.splitlines() if l.strip()]

        # Analysis text patterns to skip
        analysis_patterns = [
            "analysis", "here is", "ticket title", "subject:", "explanation", 
            "based on", "the title is", "we need", "user wants", "analysisuser",
            "wants a ticket", "needs a ticket", "requires a ticket",
            "return only", "only return", "title:", "subject:",
            "to generate", "we need to", "so maybe", "the issue:"
        ]

        for line in lines:
            lower = line.lower()
            
            # Skip explanation / analysis lines
            if any(pattern in lower for pattern in analysis_patterns):
                continue
            
            # Check if line starts with analysis words (like "Analysisuser Wants")
            words = line.split()
            if len(words) > 0:
                first_word_lower = words[0].lower()
                # Skip if first word contains "analysis" or starts with "to"
                if "analysis" in first_word_lower or first_word_lower == "to":
                    continue
                # Skip if pattern is "Wants A Ticket Title" or similar
                if len(words) > 1 and words[1].lower() in ["wants", "needs", "requires", "generate"]:
                    if len(words) > 2 and words[2].lower() in ["a", "an", "the", "ticket", "title"]:
                        continue

            word_count = len(line.split())
            # Accept 2-8 words for title (more flexible)
            if 2 <= word_count <= 8:
                return line

        # If no line found, try to extract from the whole text
        # Look for meaningful phrases after colons or in quotes
        colon_match = re.search(r':\s*["\']?([A-Z][^:."]{10,50})["\']?', text)
        if colon_match:
            potential_title = colon_match.group(1).strip()
            word_count = len(potential_title.split())
            if 2 <= word_count <= 8 and not any(kw in potential_title.lower() for kw in ["analysis", "we need", "generate"]):
                print(f"✅ Found title after colon: {potential_title}")
                return potential_title
        
        # Last resort: Remove analysis patterns and get first meaningful phrase
        cleaned = sanitize_oss_output(text)
        words = cleaned.split()
        # Filter out analysis words
        meaningful_words = [w for w in words if w.lower() not in [
            "analysis", "wants", "needs", "requires", "a", "an", "the", 
            "ticket", "title", "subject", "here", "is", "based", "on",
            "to", "generate", "maybe", "so", "but", "we", "must", "need"
        ]]
        
        if len(meaningful_words) >= 2:
            return " ".join(meaningful_words[:6])  # Max 6 words
        
        return ""

    # -------------------------------
    # LLM attempt
    # -------------------------------
    try:
        system_prompt = """You are a ticket title generator. Generate ONLY a short technical title.

CRITICAL RULES:
- Output ONLY the title text, nothing else
- NO explanations, NO analysis, NO prefixes like "Title:" or "Subject:"
- NO phrases like "user wants" or "we need"
- 3 to 6 words maximum
- Technical issue description only
- No urgency words (urgent, immediate, asap)
- No location names
- No person names
- No greetings or signatures

EXAMPLES:
Input: "Printer not responding in office and needs immediate attention"
Output: Printer not responding

Input: "Outlook emails are not syncing since morning"
Output: Outlook email not syncing

Input: "VPN fails to connect on laptop"
Output: VPN connection failing

Remember: Output ONLY the title text, no other words."""

        user_prompt = f"Email content:\n{issue_summary[:400]}"

        response = llm_client.chat.completions.create(
            model="gpt_oss_20b",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.0
        )

        raw_output = response.choices[0].message.content.strip()
        print(f"🔍 Raw LLM output: {raw_output[:200]}")

        # Handle structured output format: <|channel|>analysis<|message|>...<|channel|>final<|message|>TITLE
        # First, try to extract final channel if present
        final_channel_match = re.search(
            r"<\|channel\|>final<\|message\|>(.*?)(?:<\|channel\|>|$)",
            raw_output,
            re.DOTALL | re.IGNORECASE
        )
        if final_channel_match:
            raw_output = final_channel_match.group(1).strip()
            print(f"🔍 Extracted from final channel: {raw_output[:200]}")
        else:
            # If no final channel, try to extract from analysis channel (might contain quoted title)
            analysis_match = re.search(
                r"<\|channel\|>analysis<\|message\|>(.*?)(?:<\|channel\|>|$)",
                raw_output,
                re.DOTALL | re.IGNORECASE
            )
            if analysis_match:
                raw_output = analysis_match.group(1).strip()
                print(f"🔍 Extracted from analysis channel: {raw_output[:200]}")

        # Remove remaining model tags if present
        raw_output = re.sub(r"<\|[^|]+\|>", "", raw_output).strip()
        
        # Try extraction BEFORE sanitization (to preserve quoted titles)
        llm_title = extract_title_line(raw_output)
        
        # If extraction failed, sanitize and try again
        if not llm_title:
            raw_output = sanitize_oss_output(raw_output)
            print(f"🔍 After sanitization: {raw_output[:200]}")
            llm_title = extract_title_line(raw_output)
        
        print(f"🔍 Extracted title: {llm_title}")

        if llm_title:
            # Normalize spacing
            llm_title = re.sub(r"\s+", " ", llm_title).strip()

            # Final validation: check for analysis patterns
            lower_title = llm_title.lower()
            analysis_keywords = [
                "analysis", "wants a ticket", "needs a ticket", "user wants",
                "analysisuser", "the title is", "here is", "subject:"
            ]
            
            if any(keyword in lower_title for keyword in analysis_keywords):
                print(f"⚠️ Title contains analysis text, using fallback")
                return build_fallback_title(issue_summary)

            # Enforce 50 char limit
            if len(llm_title) > 50:
                llm_title = " ".join(llm_title.split()[:6])

            # Sentence case
            words = llm_title.split()
            if len(words) > 0:
                llm_title = " ".join(
                    [words[0].capitalize()] + [w.lower() for w in words[1:]]
                )
            else:
                print(f"⚠️ Empty title after processing, using fallback")
                return build_fallback_title(issue_summary)

            # Final length check
            if len(llm_title.strip()) < 3:
                print(f"⚠️ Title too short, using fallback")
                return build_fallback_title(issue_summary)

            print(f"✅ Final title: {llm_title}")
            return llm_title

        # If LLM response unusable → fallback
        print("⚠️ LLM title invalid, using fallback")
        return build_fallback_title(issue_summary)

    except Exception as e:
        print(f"⚠️ Error generating ticket title with LLM: {e}")
        return build_fallback_title(issue_summary)

def clean_email_content_with_llm(email_content: str) -> str:
    """
    Clean email content using LLM to extract ONLY the NEWEST reply content.
    Removes signatures, reply chains, and extracts only the latest message.
    """
    if not email_content or len(email_content.strip()) < 10:
        return email_content
    
    try:
        system_prompt = (
            "Extract ONLY the newest reply message from an email thread. "
            "Return ONLY the text that the sender wrote in THIS email, nothing else. "
            "Do NOT include any analysis, explanation, or reasoning. "
            "Do NOT include quoted messages, original messages, signatures, or disclaimers. "
            "If you see email headers like 'From:', 'Sent:', 'To:', 'Subject:', stop there - that's the start of quoted content. "
            "Return ONLY the actual message text. If no new content exists, return exactly: 'No new content'"
        )
        
        user_prompt = f"""Extract ONLY the newest message from this email. Return ONLY the message text, no analysis:

{email_content[:2000]}"""
        
        response = llm_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.1,
            max_tokens=300
        )
        
        cleaned_content = response.choices[0].message.content.strip()
        
        # Handle LLM's structured output format with analysis channels
        # Pattern: <|channel|>analysis<|message|>...<|channel|>final<|message|>ACTUAL_CONTENT
        
        # First, try to extract final message from structured format
        final_match = re.search(
            r"<\|channel\|>final<\|message\|>(.*?)(?:<\|channel\|>|$)",
            cleaned_content,
            re.DOTALL
        )
        if final_match:
            cleaned_content = final_match.group(1).strip()
        
        # Also try alternative format
        if not cleaned_content or "<|channel|>" in cleaned_content:
            alt_match = re.search(
                r"<\|start\|>assistant<\|channel\|>final<\|message\|>(.*)",
                cleaned_content,
                re.DOTALL
            )
            if alt_match:
                cleaned_content = alt_match.group(1).strip()
        
        # Remove all special tokens if still present
        cleaned_content = re.sub(r"<\|[^|]+\|>", "", cleaned_content).strip()
        
        # Check if response contains analysis/thinking content
        analysis_indicators = [
            "we need to", "the user says", "we must", "let's parse", "the block starts",
            "analysis", "extract", "we should", "the instruction", "we have a",
            "the email text is", "we need to identify", "let's parse:", "the conversation shows"
        ]
        
        has_analysis = any(indicator in cleaned_content.lower()[:200] for indicator in analysis_indicators)
        
        # If content looks like analysis, try to extract actual message
        if has_analysis:
            print(f"⚠️ LLM returned analysis content, attempting to extract actual message...")
            
            # Try to find content in code blocks or quotes
            code_block_match = re.search(r'```[^`]*```', cleaned_content, re.DOTALL)
            if code_block_match:
                cleaned_content = code_block_match.group(0).replace('```', '').strip()
            
            # Try to find quoted content
            quote_match = re.search(r'["\']([^"\']{20,})["\']', cleaned_content)
            if quote_match:
                cleaned_content = quote_match.group(1).strip()
            
            # Try to find content after ":" that looks like actual message
            colon_match = re.search(r':\s*([A-Z][^:]{20,})', cleaned_content)
            if colon_match:
                cleaned_content = colon_match.group(1).strip()
            
            # If still contains analysis, use fallback
            if any(indicator in cleaned_content.lower()[:100] for indicator in analysis_indicators):
                print(f"⚠️ Still contains analysis, using fallback extraction")
                return extract_simple_newest_content(email_content)
        
        # Final validation - if content is too long or contains analysis keywords, use fallback
        if (cleaned_content.lower() in ["no new content", "no content", "none", ""] or
            len(cleaned_content) > 1000 or 
            any(indicator in cleaned_content.lower()[:100] for indicator in analysis_indicators)):
            print(f"⚠️ LLM content validation failed, using fallback extraction")
            return extract_simple_newest_content(email_content)
        
        return cleaned_content
        
    except Exception as e:
        print(f"⚠️ Error cleaning email content with LLM: {e}")
        # Fallback to simple extraction
        return extract_simple_newest_content(email_content)


def extract_simple_newest_content(email_content: str) -> str:
    """
    Simple fallback extraction: Get content before first email separator.
    This extracts the newest reply without LLM.
    """
    if not email_content:
        return ""
    
    # Remove HTML tags if present
    text = re.sub(r"<[^>]+>", "", email_content)
    text = html.unescape(text)
    
    # Split by common separators
    separators = [
        "-----Original Message-----",
        "From:",
        "Sent:",
        "To:",
        "Subject:",
        "Date:",
        "On "  # "On [date] [person] wrote:"
    ]
    
    # Find the first separator
    first_separator_pos = len(text)
    for sep in separators:
        pos = text.find(sep)
        if pos != -1 and pos < first_separator_pos:
            first_separator_pos = pos
    
    # Extract content before first separator
    newest_content = text[:first_separator_pos].strip()
    
    # Clean up: remove empty lines at start/end
    lines = [line.strip() for line in newest_content.splitlines() if line.strip()]
    
    # Remove signature patterns (lines with titles, emails, etc.)
    filtered_lines = []
    signature_detected = False
    
    for line in lines:
        # Detect signature start patterns
        if (line.lower().endswith("manager") or 
            line.lower().endswith("director") or
            ("office" in line.lower() and "manager" in line.lower()) or
            ("dental" in line.lower() and len(line) < 50)):
            signature_detected = True
        
        # Stop at signature
        if signature_detected:
            break
            
        # Skip lines that look like email addresses or signatures
        if "@" in line and ("com" in line.lower() or "net" in line.lower() or "org" in line.lower()):
            if len(line) > 50:  # Likely signature
                signature_detected = True
                break
            continue
        
        filtered_lines.append(line)
    
    result = "\n".join(filtered_lines).strip()
    
    # If result is too short or empty, try to get more content
    if len(result) < 10:
        # Maybe separator wasn't found, try to get first meaningful paragraph
        paragraphs = text.split('\n\n')
        for para in paragraphs:
            para = para.strip()
            if len(para) > 20 and not para.lower().startswith(('from:', 'sent:', 'to:', 'subject:', 'date:')):
                result = para
                break
    
    # Return first 1000 chars if too long (don't truncate too aggressively)
    if len(result) > 1000:
        return result[:1000]
    
    return result if result else (email_content[:500] if email_content else "")




def extract_main_content_from_html(html_body: str, fallback_preview: str = "") -> str:
    """
    Extract ONLY the NEWEST reply content from an HTML email body.
    Stops at common reply/forward separators to get only the latest message.
    """
    if not html_body:
        return fallback_preview or ""

    try:
        # Remove HTML tags
        text = re.sub(r"<[^>]+>", "", html_body)
        # Decode HTML entities
        text = html.unescape(text)

        # Normalize newlines
        lines = [line.strip() for line in text.splitlines()]

        main_lines = []
        found_separator = False
        
        for line in lines:
            # Stop at common separators / quoted blocks (these indicate original message)
            lower = line.lower()
            if (
                line.startswith("-----Original Message")
                or "-----original message" in lower
                or line.startswith("________________")  # Outlook separators
                or lower.startswith("from: ")
                or lower.startswith("sent: ")
                or lower.startswith("subject: ")
                or lower.startswith("to: ")
                or lower.startswith("date: ")
                or "get outlook for" in lower
                or "get <https://aka.ms" in lower
                or line.startswith(">")  # Quoted lines often start with >
                or (line.startswith("On ") and ("wrote:" in lower or "said:" in lower))  # "On [date] [person] wrote:"
            ):
                found_separator = True
                break

            # Skip very noisy / empty lines at top
            if not line:
                # Preserve single blank line only if we already have some content
                if main_lines and main_lines[-1] != "":
                    main_lines.append("")
                continue

            main_lines.append(line)

        cleaned = "\n".join(main_lines).strip()
        
        # If we found a separator, we got the new content (good)
        # If no separator found but we have content, return it
        # If no content, use preview as fallback
        if not cleaned:
            return fallback_preview or text.strip()
        
        return cleaned
    except Exception:
        # Fallback: return preview or raw text
        return fallback_preview or html_body


def _process_emails_internal():
    """
    Internal function to process emails - can be called from scheduler or route.
    Process new emails from it.support@dental360grp.com and create tickets.
    Only processes emails received in the last 10 minutes.
    Uses conversationId to detect duplicates and add follow-ups as comments.
    """
    try:
        import os
        from flask import current_app
        from datetime import timedelta
        
        # Get email address to monitor
        email_address = os.getenv("MICROSOFT_EMAIL", "it.support@dental360grp.com")
        
        # Get access token
        token = get_graph_token()
        if not token:
            return {"error": "Failed to get Microsoft Graph access token", "status": "error"}
        
        # Calculate time 10 minutes ago (in UTC)
        ten_minutes_ago = datetime.utcnow() - timedelta(minutes=7000)
        # Format for Microsoft Graph API (ISO 8601 format)
        time_filter = ten_minutes_ago.strftime("%Y-%m-%dT%H:%M:%SZ")
        
        # Build the API URL to get unread emails
        base_url = f"{GRAPH_BASE_URL}/users/{email_address}/mailFolders/inbox/messages"
        
        # Query parameters - get unread emails from last 10 minutes only
        params = {
            '$top': 50,  # Process up to 50 emails at a time
            '$filter': f'receivedDateTime ge {time_filter}',  # Only emails from last 10 minutes
            '$orderby': 'receivedDateTime desc',  # Most recent first
            '$select': 'id,subject,from,toRecipients,receivedDateTime,isRead,bodyPreview,body,hasAttachments,conversationId'
        }
        
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        
        # Make the API request
        response = requests.get(base_url, headers=headers, params=params, timeout=30)
        
        if response.status_code != 200:
            return {
                "error": "Failed to fetch emails",
                "status": "error",
                "status_code": response.status_code,
                "details": response.text
            }
        
        data = response.json()
        emails = data.get('value', [])
        
        processed_count = 0
        tickets_created = 0
        comments_added = 0
        skipped_count = 0
        
        # System email patterns to skip (prevent infinite loops from ticket notification emails)
        system_email_patterns = [
            "Dental360 New Ticket Assigned:",
            "Dental360 Ticket",
            "SUPPORT 360",
            "Ticket Assigned",
            "Ticket Updated",
            "Ticket Followed Update",
            "Category Updated",
            "Contact Form Category Updated"
        ]
        
        for email in emails:
            try:
                email_id = email.get("id")
                conversation_id = email.get("conversationId")
                subject = email.get("subject", "")
                sender_email = email.get("from", {}).get("emailAddress", {}).get("address") if email.get("from") else None
                sender_name = email.get("from", {}).get("emailAddress", {}).get("name") if email.get("from") else None
                
                # Extract email received time to use as ticket created_at
                received_datetime_str = email.get("receivedDateTime")
                email_received_time = None
                if received_datetime_str:
                    try:
                        # Parse ISO 8601 format from Microsoft Graph API (e.g., "2024-01-15T10:30:00Z")
                        # Replace 'Z' with '+00:00' for fromisoformat compatibility
                        iso_str = received_datetime_str.replace('Z', '+00:00')
                        # Handle microseconds if present
                        if '.' in iso_str and '+' in iso_str:
                            # Format: 2024-01-15T10:30:00.123456+00:00
                            email_received_time = datetime.fromisoformat(iso_str)
                        elif '+' in iso_str:
                            # Format: 2024-01-15T10:30:00+00:00
                            email_received_time = datetime.fromisoformat(iso_str)
                        else:
                            # Try strptime as fallback
                            email_received_time = datetime.strptime(received_datetime_str, "%Y-%m-%dT%H:%M:%SZ")
                        # Convert to UTC naive datetime (since database stores UTC)
                        if email_received_time.tzinfo:
                            email_received_time = email_received_time.replace(tzinfo=None)
                    except Exception as e:
                        print(f"⚠️ Error parsing receivedDateTime '{received_datetime_str}': {e}")
                        # Fallback to current time if parsing fails
                        email_received_time = datetime.utcnow()
                else:
                    # Fallback to current time if receivedDateTime is missing
                    email_received_time = datetime.utcnow()
                
                # CRITICAL: Skip if already processed (check by email_id first - most important check)
                # Use database query with lock to prevent race conditions
                existing_log = EmailProcessedLog.query.filter_by(email_id=email_id).first()
                if existing_log:
                    print(f"⏭️ Email {email_id} already processed, skipping...")
                    skipped_count += 1
                    continue
                
                # Skip system-generated notification emails (prevent infinite loops)
                if subject and any(pattern in subject for pattern in system_email_patterns):
                    print(f"⏭️ Skipping system notification email: {subject}")
                    # Try to log it as processed - if duplicate, skip
                    try:
                        email_log = EmailProcessedLog(
                            email_id=email_id,
                            conversation_id=conversation_id,
                            ticket_id=None,
                            sender_email=sender_email,
                            user_id=None,
                            email_subject=subject,
                            is_followup=False
                        )
                        db.session.add(email_log)
                        db.session.commit()
                    except Exception as e:
                        # Already exists (race condition), skip
                        db.session.rollback()
                        print(f"⏭️ System email {email_id} already logged, skipping...")
                    skipped_count += 1
                    continue
                
                # CRITICAL: Reserve email_id IMMEDIATELY to prevent race conditions
                # Try to create a placeholder log entry - if it fails, email is already being processed
                placeholder_log = None
                try:
                    placeholder_log = EmailProcessedLog(
                        email_id=email_id,
                        conversation_id=conversation_id,
                        ticket_id=None,  # Will be updated after ticket creation
                        sender_email=sender_email,
                        user_id=None,  # Will be updated after user lookup
                        email_subject=subject,
                        is_followup=False
                    )
                    db.session.add(placeholder_log)
                    db.session.commit()
                    print(f"🔒 Reserved email_id {email_id} for processing")
                except Exception as e:
                    # Unique constraint violation or other DB error - email already being processed
                    db.session.rollback()
                    # Check if it's a unique constraint violation
                    import sqlalchemy
                    if isinstance(e, sqlalchemy.exc.IntegrityError) or "unique" in str(e).lower() or "duplicate" in str(e).lower():
                        print(f"⏭️ Email {email_id} already being processed (unique constraint violation), skipping...")
                    else:
                        print(f"⏭️ Email {email_id} reservation failed: {e}, skipping...")
                    skipped_count += 1
                    continue
                
                # CRITICAL: Check conversation_id EARLY to prevent duplicate tickets
                # This must be checked BEFORE processing content to avoid race conditions
                existing_conversation = None
                if conversation_id:
                    # Check if ANY email in this conversation already has a ticket
                    existing_conversation = (
                        EmailProcessedLog.query
                        .filter_by(conversation_id=conversation_id)
                        .with_for_update()   # 🔒 LOCK ROW
                        .filter(EmailProcessedLog.ticket_id.isnot(None))
                        .first()
                    )

                    
                    if existing_conversation:
                        print(f"🔍 Found existing conversation {conversation_id} with ticket #{existing_conversation.ticket_id}")
                
                # Get user_id from sender email
                user_id = None
                if sender_email:
                    user_id = get_user_id_by_email(sender_email)
                    if not user_id:
                        print(f"⚠️ User not found for email: {sender_email}")
                
                # Extract email content - preserve raw content for comments
                raw_body = email.get("body", {}).get("content") if email.get("body") else None
                content_type = email.get("body", {}).get("contentType") if email.get("body") else None
                body_preview = email.get("bodyPreview", "")
                
                # Get raw email content for comments (preserve everything)
                # PRIORITIZE raw_body (full content) over body_preview (truncated preview)
                if content_type and content_type.lower() == "html" and raw_body:
                    # For HTML emails, extract text from full body
                    from html import unescape
                    import re
                    # Remove HTML tags but keep the text
                    text_content = re.sub(r'<[^>]+>', ' ', raw_body)
                    text_content = unescape(text_content)
                    # Use full extracted HTML text (not truncated preview)
                    raw_email_content = text_content.strip() if text_content.strip() else (body_preview or "")
                else:
                    # For plain text emails, use full raw_body, fallback to preview
                    raw_email_content = (raw_body or body_preview or "").strip()
                
                # Extract main content for analysis (this can be cleaned)
                if content_type and content_type.lower() == "html":
                    initial_content = extract_main_content_from_html(raw_body or "", body_preview)
                else:
                    initial_content = (raw_body or body_preview or "").strip()
                
                # Clean email content using LLM (with fallback to simple extraction) - only for analysis
                print(f"🧹 Cleaning email content with LLM...")
                main_content = clean_email_content_with_llm(initial_content)
                
                # Validate extracted content - if it looks like analysis or is empty, use fallback
                if (not main_content or 
                    len(main_content.strip()) < 5 or
                    "analysis" in main_content.lower()[:100] or
                    "the conversation shows" in main_content.lower()[:100] or
                    "we need to" in main_content.lower()[:100] or
                    main_content.startswith("s any") or  # Common analysis fragment
                    main_content.startswith("the block")):
                    print(f"⚠️ LLM returned invalid content, using simple extraction...")
                    main_content = extract_simple_newest_content(initial_content)
                
                print(f"✅ Email content cleaned: {len(main_content)} characters")
                
                # If conversation exists and has a ticket, add as comment (follow-up)
                if existing_conversation and existing_conversation.ticket_id:
                    # Follow-up: Add as comment to existing ticket
                    # Note: placeholder_log already exists from earlier reservation
                    
                    ticket = Ticket.query.get(existing_conversation.ticket_id)
                    if ticket:
                        # Add full email content as comment (use raw content, not processed)
                        comment_text = f"📧 Email Follow-up from {sender_name or sender_email}\n\n{raw_email_content}"
                        
                        # Check if this exact comment already exists (prevent duplicates)
                        existing_comment = TicketComment.query.filter_by(
                            ticket_id=ticket.id,
                            user_id=user_id,
                            comment=comment_text
                        ).first()
                        
                        if existing_comment:
                            print(f"⏭️ Comment already exists for email {email_id}, skipping...")
                            # Update placeholder log with ticket info
                            placeholder_log.ticket_id = ticket.id
                            placeholder_log.user_id = user_id
                            placeholder_log.is_followup = True
                            db.session.commit()
                            skipped_count += 1
                            continue
                        
                        comment = TicketComment(
                            ticket_id=ticket.id,
                            user_id=user_id,
                            comment=comment_text
                        )
                        db.session.add(comment)
                        
                        # Update placeholder log with ticket_id, user_id, and is_followup flag
                        placeholder_log.ticket_id = ticket.id
                        placeholder_log.user_id = user_id
                        placeholder_log.is_followup = True
                        db.session.commit()
                        
                        comments_added += 1
                        processed_count += 1
                        print(f"✅ Added comment to ticket #{ticket.id} from email {email_id}")
                    else:
                        print(f"⚠️ Ticket {existing_conversation.ticket_id} not found for conversation {conversation_id}")
                else:
                    # New conversation: Create new ticket
                    # CRITICAL: Double-check conversation_id wasn't missed (race condition protection)
                    if conversation_id:
                        # Re-check conversation_id one more time before creating ticket
                        final_conversation_check = EmailProcessedLog.query.filter_by(
                            conversation_id=conversation_id
                        ).filter(
                            EmailProcessedLog.ticket_id.isnot(None)
                        ).first()
                        
                        if final_conversation_check:
                            print(f"⚠️ Conversation {conversation_id} already has ticket #{final_conversation_check.ticket_id} (race condition), adding as comment instead...")
                            # Add as comment instead of creating new ticket
                            ticket = Ticket.query.get(final_conversation_check.ticket_id)
                            if ticket:
                                comment_text = f"📧 Email Follow-up from {sender_name or sender_email}\n\n{raw_email_content}"
                                
                                # Check if comment already exists
                                existing_comment = TicketComment.query.filter_by(
                                    ticket_id=ticket.id,
                                    user_id=user_id,
                                    comment=comment_text
                                ).first()
                                
                                if not existing_comment:
                                    comment = TicketComment(
                                        ticket_id=ticket.id,
                                        user_id=user_id,
                                        comment=comment_text
                                    )
                                    db.session.add(comment)
                                    
                                    # Update placeholder log with ticket info
                                    placeholder_log.ticket_id = ticket.id
                                    placeholder_log.user_id = user_id
                                    placeholder_log.is_followup = True
                                    db.session.commit()
                                    
                                    comments_added += 1
                                    processed_count += 1
                                    print(f"✅ Added comment to ticket #{ticket.id} from email {email_id}")
                                else:
                                    print(f"⏭️ Comment already exists, skipping...")
                                    # Still log email as processed
                                    email_log = EmailProcessedLog(
                                        email_id=email_id,
                                        conversation_id=conversation_id,
                                        ticket_id=ticket.id,
                                        sender_email=sender_email,
                                        user_id=user_id,
                                        email_subject=subject,
                                        is_followup=True
                                    )
                                    # Update placeholder log
                                    placeholder_log.ticket_id = ticket.id
                                    placeholder_log.user_id = user_id
                                    placeholder_log.is_followup = True
                                    db.session.commit()
                                    skipped_count += 1
                            continue
                    
                    # Note: placeholder_log already exists from earlier reservation
                    # No need to check again - the unique constraint prevents duplicates
                    
                    # Use IT category for all email tickets
                    category_id = None
                    matched_category = None
                    
                    # Find IT category
                    it_category = Category.query.filter(
                        Category.name.ilike("IT")
                    ).first()
                    if it_category:
                        category_id = it_category.id
                        matched_category = it_category
                        print(f"✅ Using IT category for email ticket")
                    
                    # FINAL CHECK: One more time check conversation_id before creating ticket
                    # This is critical to prevent duplicates when multiple emails arrive simultaneously
                    if conversation_id:
                        last_check = EmailProcessedLog.query.filter_by(
                            conversation_id=conversation_id
                        ).filter(
                            EmailProcessedLog.ticket_id.isnot(None)
                        ).first()
                        
                        if last_check:
                            print(f"🚨 CRITICAL: Conversation {conversation_id} found ticket #{last_check.ticket_id} at last moment, aborting ticket creation!")
                            # Add as comment instead
                            ticket = Ticket.query.get(last_check.ticket_id)
                            if ticket:
                                comment_text = f"📧 Email Follow-up from {sender_name or sender_email}\n\n{raw_email_content}"
                                comment = TicketComment(
                                    ticket_id=ticket.id,
                                    user_id=user_id,
                                    comment=comment_text
                                )
                                db.session.add(comment)
                                
                                # Update placeholder log with ticket info
                                placeholder_log.ticket_id = ticket.id
                                placeholder_log.user_id = user_id
                                placeholder_log.is_followup = True
                                db.session.commit()
                                
                                comments_added += 1
                                processed_count += 1
                                print(f"✅ Added comment to ticket #{ticket.id} from email {email_id}")
                                continue
                    
                    # Create ticket (only if all checks passed)
                    print(f"📝 Creating new ticket for conversation_id: {conversation_id or 'None'}")
                    
                    # Step 1: Analyze email content to extract the main issue for ticket message
                    # Use main_content (cleaned) if it's substantial, otherwise use initial_content
                    content_for_analysis = main_content if len(main_content.strip()) > 50 else initial_content
                    print(f"🔍 Analyzing email content to extract main issue (using {len(content_for_analysis)} chars)...")
                    analyzed_issue = analyze_email_issue_with_llm(content_for_analysis)
                    print(f"✅ Analyzed issue: {analyzed_issue[:100]}...")
                    
                    # Step 2: Generate a short, clear title from the analyzed issue
                    print(f"🔍 Generating ticket title from analyzed issue...")
                    generated_title = generate_ticket_title_with_llm(analyzed_issue, sender_name, sender_email)
                    print(f"✅ Generated title: {generated_title}")
                    
                    # Create ticket with LLM-generated title and analyzed issue as details
                    ticket = Ticket(
                        clinic_id=None,  # Can be set later if needed
                        title=generated_title,
                        details=analyzed_issue or "(no content)",  # Use analyzed issue as ticket message
                        category_id=category_id,
                        status="Pending",
                        priority="low",
                        due_date=None,
                        user_id=user_id,  # Sender as creator
                        location_id=None,
                        created_at=email_received_time  # Use exact email received time
                    )
                    db.session.add(ticket)
                    db.session.commit()
                    
                    print(f"✅ Created ticket #{ticket.id} for conversation_id: {conversation_id}")
                    
                    # Step 2: Add full email content as a comment (use raw content, not processed)
                    print(f"💬 Adding full email content as comment...")
                    full_email_comment = f"📧 Email from {sender_name or sender_email}\n\n{raw_email_content}"
                    comment = TicketComment(
                        ticket_id=ticket.id,
                        user_id=user_id,
                        comment=full_email_comment
                    )
                    db.session.add(comment)
                    db.session.commit()
                    print(f"✅ Added full email content as comment to ticket #{ticket.id}")
                    
                    # Auto-assign if category has assignee
                    if matched_category and matched_category.assignee_id:
                        assignment = TicketAssignment(
                            ticket_id=ticket.id,
                            assign_to=matched_category.assignee_id,
                            assign_by=None  # System-generated
                        )
                        db.session.add(assignment)
                        db.session.commit()
                        
                        assignee_info = get_user_info_by_id(matched_category.assignee_id)
                        if assignee_info:
                            send_assign_email(ticket, assignee_info, {"username": "System"})
                            create_notification(
                                ticket_id=ticket.id,
                                receiver_id=matched_category.assignee_id,
                                sender_id=None,
                                notification_type="assign",
                                message=f"Auto-assigned from email: {subject}"
                            )
                    
                    # Update placeholder log with ticket_id and user_id IMMEDIATELY after ticket creation
                    # This must happen before processing next email to prevent duplicates
                    placeholder_log.ticket_id = ticket.id
                    placeholder_log.user_id = user_id
                    db.session.commit()
                    
                    print(f"✅ Updated email log {email_id} → ticket #{ticket.id} for conversation {conversation_id}")
                    
                    tickets_created += 1
                    processed_count += 1
                    print(f"✅ Created ticket #{ticket.id} from email {email_id}")
                
            except Exception as e:
                print(f"❌ Error processing email {email.get('id', 'unknown')}: {e}")
                db.session.rollback()
                # Try to clean up placeholder_log if it exists
                try:
                    if 'placeholder_log' in locals() and placeholder_log:
                        # Check if placeholder_log still exists (might have been committed)
                        existing_placeholder = EmailProcessedLog.query.filter_by(email_id=email.get("id")).first()
                        if existing_placeholder and not existing_placeholder.ticket_id:
                            # Only delete if no ticket was created
                            db.session.delete(existing_placeholder)
                            db.session.commit()
                            print(f"🧹 Cleaned up placeholder log for failed email {email.get('id')}")
                except Exception as cleanup_error:
                    print(f"⚠️ Error cleaning up placeholder log: {cleanup_error}")
                    db.session.rollback()
                continue
        
        return {
            "status": "success",
            "message": f"Processed {processed_count} emails",
            "tickets_created": tickets_created,
            "comments_added": comments_added,
            "skipped": skipped_count,
            "total_emails_fetched": len(emails),
            "time_range": f"Last 10 minutes (from {time_filter})"
        }
        
    except Exception as e:
        db.session.rollback()
        print(f"❌ Error processing emails: {e}")
        return {"error": str(e), "status": "error"}


@ticket_bp.route("/process_emails", methods=["POST"])
# @require_api_key
def process_emails():
    """
    API endpoint to manually trigger email processing.
    Calls the internal processing function and returns JSON response.
    """
    result = _process_emails_internal()
    
    # If result is a dict (from internal function), convert to JSON response
    if isinstance(result, dict):
        if result.get("status") == "error":
            return jsonify(result), 500
        return jsonify(result), 200
    
    # If it's already a Flask response, return it
    return result


@ticket_bp.route("/read_emails", methods=["GET"])
# @require_api_key
# @validate_token
def read_emails():
    """
    Read emails from the inbox without processing them.
    Returns the last 50 emails with their details.
    
    Query parameters:
    - limit: Number of emails to fetch (default: 50, max: 100)
    - hours: Number of hours to look back (default: 24)
    
    Returns list of emails with their details.
    """
    try:
        import os
        from datetime import timedelta
        
        # Get query parameters
        limit = request.args.get("limit", 50, type=int)
        hours = request.args.get("hours", 2400, type=int)
        
        # Validate and cap limit
        if limit < 1:
            limit = 50
        if limit > 100:
            limit = 100
        
        # Get email address to monitor
        email_address = os.getenv("MICROSOFT_EMAIL", "it.support@dental360grp.com")
        
        # Get access token
        token = get_graph_token()
        if not token:
            return jsonify({
                "error": "Failed to get Microsoft Graph access token",
                "status": "error"
            }), 500
        
        # Calculate time threshold
        time_threshold = datetime.utcnow() - timedelta(hours=hours)
        time_filter = time_threshold.strftime("%Y-%m-%dT%H:%M:%SZ")
        
        # Build the API URL to get emails
        base_url = f"{GRAPH_BASE_URL}/users/{email_address}/mailFolders/inbox/messages"
        
        # Query parameters - get last N emails
        params = {
            '$top': limit,  # Limit to last N emails
            '$filter': f'receivedDateTime ge {time_filter}',  # Only emails from last N hours
            '$orderby': 'receivedDateTime desc',  # Most recent first
            '$select': 'id,subject,from,toRecipients,receivedDateTime,isRead,bodyPreview,body,hasAttachments,conversationId'
        }
        
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        
        # Make the API request
        response = requests.get(base_url, headers=headers, params=params, timeout=30)
        
        if response.status_code != 200:
            return jsonify({
                "error": "Failed to fetch emails",
                "status": "error",
                "status_code": response.status_code,
                "details": response.text
            }), 500
        
        data = response.json()
        emails = data.get('value', [])
        
        # Format emails for response
        formatted_emails = []
        for email in emails:
            email_id = email.get("id")
            conversation_id = email.get("conversationId")
            subject = email.get("subject", "")
            sender_email = email.get("from", {}).get("emailAddress", {}).get("address") if email.get("from") else None
            sender_name = email.get("from", {}).get("emailAddress", {}).get("name") if email.get("from") else None
            received_datetime = email.get("receivedDateTime")
            is_read = email.get("isRead", False)
            body_preview = email.get("bodyPreview", "")
            has_attachments = email.get("hasAttachments", False)
            
            # Check if email was already processed
            processed_log = EmailProcessedLog.query.filter_by(email_id=email_id).first()
            is_processed = processed_log is not None
            ticket_id = processed_log.ticket_id if processed_log else None
            
            formatted_emails.append({
                "email_id": email_id,
                "conversation_id": conversation_id,
                "subject": subject,
                "sender_email": sender_email,
                "sender_name": sender_name,
                "received_datetime": received_datetime,
                "is_read": is_read,
                "body_preview": body_preview[:200] if body_preview else "",  # Limit preview length
                "has_attachments": has_attachments,
                "is_processed": is_processed,
                "ticket_id": ticket_id,
                "processed_at": processed_log.processed_at.isoformat() if processed_log and processed_log.processed_at else None
            })
        
        return jsonify({
            "status": "success",
            "message": f"Fetched {len(formatted_emails)} emails",
            "total_emails": len(formatted_emails),
            "limit": limit,
            "hours": hours,
            "time_threshold": time_filter,
            "emails": formatted_emails
        }), 200
        
    except Exception as e:
        print(f"❌ Error reading emails: {e}")
        return jsonify({
            "status": "error",
            "error": str(e)
        }), 500


@ticket_bp.route("/check_missing_tickets", methods=["GET"])
@require_api_key
def check_missing_tickets():
    """
    API endpoint to check for emails that should have tickets but don't.
    Returns emails that were processed but no ticket was created.
    
    Query parameters:
    - hours: Number of hours to look back (default: 24)
    - include_system: Include system notification emails (default: False)
    """
    try:
        from datetime import timedelta
        
        # Get query parameters
        hours = request.args.get("hours", 24, type=int)
        include_system = request.args.get("include_system", "false").lower() == "true"
        
        # Calculate time threshold
        time_threshold = datetime.utcnow() - timedelta(hours=hours)
        
        # System email patterns to identify notification emails
        system_email_patterns = [
            "Dental360 New Ticket Assigned:",
            "Dental360 Ticket",
            "SUPPORT 360",
            "Ticket Assigned",
            "Ticket Updated",
            "Ticket Followed Update",
            "Category Updated",
            "Contact Form Category Updated"
        ]
        
        # Query emails that were processed but have no ticket_id
        # Exclude follow-ups (they don't need tickets) and optionally system emails
        query = EmailProcessedLog.query.filter(
            EmailProcessedLog.ticket_id.is_(None),
            EmailProcessedLog.processed_at >= time_threshold
        )
        
        # Exclude follow-ups (they're comments, not new tickets)
        query = query.filter(EmailProcessedLog.is_followup == False)
        
        missing_tickets = query.order_by(EmailProcessedLog.processed_at.desc()).all()
        
        result = []
        for log in missing_tickets:
            # Skip system emails unless explicitly included
            if not include_system and log.email_subject:
                is_system_email = any(pattern in log.email_subject for pattern in system_email_patterns)
                if is_system_email:
                    continue
            
            # Check if this email's conversation has a ticket (might be a follow-up that wasn't marked)
            has_conversation_ticket = False
            if log.conversation_id:
                conversation_ticket = EmailProcessedLog.query.filter(
                    EmailProcessedLog.conversation_id == log.conversation_id,
                    EmailProcessedLog.ticket_id.isnot(None)
                ).first()
                if conversation_ticket:
                    has_conversation_ticket = True
            
            result.append({
                "email_id": log.email_id,
                "conversation_id": log.conversation_id,
                "sender_email": log.sender_email,
                "email_subject": log.email_subject,
                "user_id": log.user_id,
                "processed_at": log.processed_at.isoformat() if log.processed_at else None,
                "is_followup": log.is_followup,
                "has_conversation_ticket": has_conversation_ticket,
                "conversation_ticket_id": conversation_ticket.ticket_id if has_conversation_ticket else None,
                "reason": "followup_in_conversation" if has_conversation_ticket else "no_ticket_created"
            })
        
        return jsonify({
            "status": "success",
            "total_missing": len(result),
            "time_range_hours": hours,
            "time_threshold": time_threshold.isoformat(),
            "missing_tickets": result,
            "summary": {
                "total_processed": EmailProcessedLog.query.filter(
                    EmailProcessedLog.processed_at >= time_threshold
                ).count(),
                "with_tickets": EmailProcessedLog.query.filter(
                    EmailProcessedLog.ticket_id.isnot(None),
                    EmailProcessedLog.processed_at >= time_threshold
                ).count(),
                "missing_tickets": len(result),
                "followups": EmailProcessedLog.query.filter(
                    EmailProcessedLog.is_followup == True,
                    EmailProcessedLog.processed_at >= time_threshold
                ).count()
            }
        }), 200
        
    except Exception as e:
        print(f"❌ Error checking missing tickets: {e}")
        return jsonify({
            "status": "error",
            "error": str(e)
        }), 500


@ticket_bp.route("/reprocess_email/<email_id>", methods=["POST"])
@require_api_key
def reprocess_email(email_id):
    """
    Manually reprocess a specific email by email_id.
    Useful for retrying emails that failed to create tickets.
    
    This will:
    1. Check if email was already processed
    2. If ticket exists, skip
    3. If no ticket, attempt to create one
    """
    try:
        import os
        from flask import current_app
        
        # Get email address to monitor
        email_address = os.getenv("MICROSOFT_EMAIL", "it.support@dental360grp.com")
        
        # Get access token
        token = get_graph_token()
        if not token:
            return jsonify({"error": "Failed to get Microsoft Graph access token"}), 500
        
        # Check if email was already processed
        existing_log = EmailProcessedLog.query.filter_by(email_id=email_id).first()
        if existing_log and existing_log.ticket_id:
            return jsonify({
                "status": "already_processed",
                "message": f"Email already processed with ticket_id: {existing_log.ticket_id}",
                "ticket_id": existing_log.ticket_id,
                "email_log": {
                    "email_id": existing_log.email_id,
                    "conversation_id": existing_log.conversation_id,
                    "sender_email": existing_log.sender_email,
                    "email_subject": existing_log.email_subject,
                    "processed_at": existing_log.processed_at.isoformat() if existing_log.processed_at else None
                }
            }), 200
        
        # Fetch email from Microsoft Graph API
        base_url = f"{GRAPH_BASE_URL}/users/{email_address}/messages/{email_id}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        
        response = requests.get(
            base_url,
            headers=headers,
            params={'$select': 'id,subject,from,toRecipients,receivedDateTime,isRead,bodyPreview,body,hasAttachments,conversationId'},
            timeout=30
        )
        
        if response.status_code != 200:
            return jsonify({
                "error": "Failed to fetch email from Microsoft Graph",
                "status_code": response.status_code,
                "details": response.text
            }), 500
        
        email = response.json()
        
        # Process this single email using the same logic as _process_emails_internal
        # We'll create a simplified version here
        email_id_from_api = email.get("id")
        conversation_id = email.get("conversationId")
        subject = email.get("subject", "")
        sender_email = email.get("from", {}).get("emailAddress", {}).get("address") if email.get("from") else None
        sender_name = email.get("from", {}).get("emailAddress", {}).get("name") if email.get("from") else None
        
        # Extract received time
        received_datetime_str = email.get("receivedDateTime")
        email_received_time = None
        if received_datetime_str:
            try:
                iso_str = received_datetime_str.replace('Z', '+00:00')
                if '.' in iso_str and '+' in iso_str:
                    email_received_time = datetime.fromisoformat(iso_str)
                elif '+' in iso_str:
                    email_received_time = datetime.fromisoformat(iso_str)
                else:
                    email_received_time = datetime.strptime(received_datetime_str, "%Y-%m-%dT%H:%M:%SZ")
                if email_received_time.tzinfo:
                    email_received_time = email_received_time.replace(tzinfo=None)
            except Exception as e:
                print(f"⚠️ Error parsing receivedDateTime: {e}")
                email_received_time = datetime.utcnow()
        else:
            email_received_time = datetime.utcnow()
        
        # Check if conversation already has a ticket
        if conversation_id:
            existing_conversation = EmailProcessedLog.query.filter_by(
                conversation_id=conversation_id
            ).filter(
                EmailProcessedLog.ticket_id.isnot(None)
            ).first()
            
            if existing_conversation:
                # Add as comment instead
                ticket = Ticket.query.get(existing_conversation.ticket_id)
                if ticket:
                    user_id = get_user_id_by_email(sender_email) if sender_email else None
                    raw_body = email.get("body", {}).get("content") if email.get("body") else None
                    content_type = email.get("body", {}).get("contentType") if email.get("body") else None
                    body_preview = email.get("bodyPreview", "")
                    
                    # Get raw email content for comments (preserve everything)
                    # PRIORITIZE raw_body (full content) over body_preview (truncated preview)
                    if content_type and content_type.lower() == "html" and raw_body:
                        from html import unescape
                        import re
                        text_content = re.sub(r'<[^>]+>', ' ', raw_body)
                        text_content = unescape(text_content)
                        raw_email_content = text_content.strip() if text_content.strip() else (body_preview or "")
                    else:
                        raw_email_content = (raw_body or body_preview or "").strip()
                    
                    comment_text = f"📧 Email Follow-up from {sender_name or sender_email}\n\n{raw_email_content}"
                    
                    comment = TicketComment(
                        ticket_id=ticket.id,
                        user_id=user_id,
                        comment=comment_text
                    )
                    db.session.add(comment)
                    
                    # Update or create log
                    if existing_log:
                        existing_log.ticket_id = ticket.id
                        existing_log.is_followup = True
                    else:
                        email_log = EmailProcessedLog(
                            email_id=email_id_from_api,
                            conversation_id=conversation_id,
                            ticket_id=ticket.id,
                            sender_email=sender_email,
                            user_id=user_id,
                            email_subject=subject,
                            is_followup=True
                        )
                        db.session.add(email_log)
                    
                    db.session.commit()
                    
                    return jsonify({
                        "status": "success",
                        "message": "Added as comment to existing ticket",
                        "ticket_id": ticket.id,
                        "action": "comment_added"
                    }), 200
        
        # Create new ticket
        user_id = get_user_id_by_email(sender_email) if sender_email else None
        
        raw_body = email.get("body", {}).get("content") if email.get("body") else None
        content_type = email.get("body", {}).get("contentType") if email.get("body") else None
        body_preview = email.get("bodyPreview", "")
        
        # Get raw email content for comments (preserve everything)
        # PRIORITIZE raw_body (full content) over body_preview (truncated preview)
        if content_type and content_type.lower() == "html" and raw_body:
            from html import unescape
            import re
            text_content = re.sub(r'<[^>]+>', ' ', raw_body)
            text_content = unescape(text_content)
            raw_email_content = text_content.strip() if text_content.strip() else (body_preview or "")
        else:
            raw_email_content = (raw_body or body_preview or "").strip()
        
        if content_type and content_type.lower() == "html":
            initial_content = extract_main_content_from_html(raw_body or "", body_preview)
        else:
            initial_content = (raw_body or body_preview or "").strip()
        
        main_content = clean_email_content_with_llm(initial_content)
        
        # Step 1: Analyze email content to extract the main issue for ticket message
        print(f"🔍 Analyzing email content to extract main issue...")
        analyzed_issue = analyze_email_issue_with_llm(initial_content)
        print(f"✅ Analyzed issue: {analyzed_issue[:100]}...")
        
        # Step 2: Generate a short, clear title from the analyzed issue
        print(f"🔍 Generating ticket title from analyzed issue...")
        generated_title = generate_ticket_title_with_llm(main_content, sender_name, sender_email)
        print(f"✅ Generated title: {generated_title}")
        
        # Find IT category
        category_id = None
        matched_category = None
        it_category = Category.query.filter(Category.name.ilike("IT")).first()
        if it_category:
            category_id = it_category.id
            matched_category = it_category
        
        ticket = Ticket(
            clinic_id=None,
            title=generated_title,
            details=analyzed_issue or "(no content)",  # Use analyzed issue as ticket message
            category_id=category_id,
            status="Pending",
            priority="low",
            due_date=None,
            user_id=user_id,
            location_id=None,
            created_at=email_received_time
        )
        db.session.add(ticket)
        db.session.commit()
        
        # Add full email content as a comment (use raw content, not processed)
        print(f"💬 Adding full email content as comment...")
        full_email_comment = f"📧 Email from {sender_name or sender_email}\n\n{raw_email_content}"
        comment = TicketComment(
            ticket_id=ticket.id,
            user_id=user_id,
            comment=full_email_comment
        )
        db.session.add(comment)
        db.session.commit()
        print(f"✅ Added full email content as comment to ticket #{ticket.id}")
        
        # Auto-assign if category has assignee
        if matched_category and matched_category.assignee_id:
            assignment = TicketAssignment(
                ticket_id=ticket.id,
                assign_to=matched_category.assignee_id,
                assign_by=None
            )
            db.session.add(assignment)
            db.session.commit()
            
            assignee_info = get_user_info_by_id(matched_category.assignee_id)
            if assignee_info:
                send_assign_email(ticket, assignee_info, {"username": "System"})
                create_notification(
                    ticket_id=ticket.id,
                    receiver_id=matched_category.assignee_id,
                    sender_id=None,
                    notification_type="assign",
                    message=f"Auto-assigned from email: {subject}"
                )
        
        # Update or create log
        if existing_log:
            existing_log.ticket_id = ticket.id
            existing_log.is_followup = False
        else:
            email_log = EmailProcessedLog(
                email_id=email_id_from_api,
                conversation_id=conversation_id,
                ticket_id=ticket.id,
                sender_email=sender_email,
                user_id=user_id,
                email_subject=subject,
                is_followup=False
            )
            db.session.add(email_log)
        
        db.session.commit()
        
        return jsonify({
            "status": "success",
            "message": "Ticket created successfully",
            "ticket_id": ticket.id,
            "action": "ticket_created"
        }), 200
        
    except Exception as e:
        db.session.rollback()
        print(f"❌ Error reprocessing email {email_id}: {e}")
        return jsonify({
            "status": "error",
            "error": str(e)
        }), 500


@ticket_bp.route("/assign_locations", methods=["POST"])
@require_api_key
@validate_token
def assign_ticket_locations():
    """
    Assign one or more locations to a ticket.
    Saves location_ids and ticket_id in ticket_assign_locations table.
    
    Request body (JSON):
    {
        "ticket_id": 123,
        "location_ids": [1, 2, 3],  # Array of location IDs
        "user_id": 456,  # Optional: User ID who assigned the locations
        "replace": false  # Optional: If true, replaces all existing locations with new ones
    }
    
    Behavior:
    - If replace=false (default): Adds new locations, skips duplicates
    - If replace=true: Removes all existing locations and assigns only the new ones
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({"error": "Request body is required"}), 400
        
        ticket_id = data.get("ticket_id")
        location_ids = data.get("location_ids")
        created_by = data.get("user_id")
        replace = data.get("replace", False)  # Default to False (add mode)
        
        # Validate required fields
        if not ticket_id:
            return jsonify({"error": "ticket_id is required"}), 400
        
        if not location_ids:
            return jsonify({"error": "location_ids is required"}), 400
        
        if not isinstance(location_ids, list):
            return jsonify({"error": "location_ids must be an array"}), 400
        
        if len(location_ids) == 0 and not replace:
            return jsonify({"error": "location_ids cannot be empty"}), 400
        
        # Validate ticket exists
        ticket = Ticket.query.get(ticket_id)
        if not ticket:
            return jsonify({"error": f"Ticket with id {ticket_id} not found"}), 404
        
        # Remove duplicates from location_ids
        location_ids = list(set(location_ids))
        
        # Get existing assignments
        existing_assignments = TicketAssignLocation.query.filter_by(ticket_id=ticket_id).all()
        existing_location_ids = {assign.location_id for assign in existing_assignments}
        
        removed_locations = []
        created_assignments = []
        
        # If replace mode: remove all existing locations first
        if replace:
            for assignment in existing_assignments:
                removed_locations.append(assignment.location_id)
                db.session.delete(assignment)
            
            # If location_ids is empty in replace mode, just remove all
            if len(location_ids) == 0:
                db.session.commit()
                return jsonify({
                    "status": "success",
                    "message": "All locations removed from ticket",
                    "ticket_id": ticket_id,
                    "removed_locations": removed_locations,
                    "all_locations": []
                }), 200
            
            # Add all new locations (no need to check duplicates since we removed all)
            for location_id in location_ids:
                assignment = TicketAssignLocation(
                    ticket_id=ticket_id,
                    location_id=location_id,
                    created_by=created_by
                )
                db.session.add(assignment)
                created_assignments.append(location_id)
        
        else:
            # Add mode: only add new locations, skip duplicates
            new_location_ids = [loc_id for loc_id in location_ids if loc_id not in existing_location_ids]
            
            if not new_location_ids:
                return jsonify({
                    "status": "success",
                    "message": "All locations are already assigned to this ticket",
                    "ticket_id": ticket_id,
                    "existing_locations": list(existing_location_ids),
                    "skipped": location_ids
                }), 200
            
            # Create new location assignments
            for location_id in new_location_ids:
                assignment = TicketAssignLocation(
                    ticket_id=ticket_id,
                    location_id=location_id,
                    created_by=created_by
                )
                db.session.add(assignment)
                created_assignments.append(location_id)
        
        db.session.commit()
        
        # Get all assigned locations for this ticket
        all_assignments = TicketAssignLocation.query.filter_by(ticket_id=ticket_id).all()
        all_location_ids = [assign.location_id for assign in all_assignments]
        
        response_data = {
            "status": "success",
            "ticket_id": ticket_id,
            "all_locations": all_location_ids
        }
        
        if replace:
            response_data["message"] = f"Replaced all locations. Added {len(created_assignments)} new location(s)"
            response_data["removed_locations"] = removed_locations
            response_data["new_locations"] = created_assignments
        else:
            response_data["message"] = f"Successfully assigned {len(created_assignments)} location(s) to ticket"
            response_data["new_locations"] = created_assignments
            response_data["skipped"] = [loc_id for loc_id in location_ids if loc_id in existing_location_ids]
        
        return jsonify(response_data), 200
        
    except Exception as e:
        db.session.rollback()
        print(f"❌ Error assigning locations to ticket: {e}")
        return jsonify({
            "status": "error",
            "error": str(e)
        }), 500


@ticket_bp.route("/ticket/<int:ticket_id>/locations", methods=["GET"])
@require_api_key
@validate_token
def get_ticket_locations(ticket_id):
    """
    Get all locations assigned to a specific ticket with location details from auth system.
    
    Returns:
    {
        "ticket_id": 123,
        "locations": [
            {
                "id": 1,  # TicketAssignLocation id
                "location_id": 1,
                "created_at": "2024-01-15T10:30:00",
                "created_by": 456,
                "location_details": {
                    "id": 1,
                    "location_name": "Bucktown",
                    "address": "2500 W North Ave",
                    "city": "Chicago",
                    "state": "IL",
                    ...
                }
            },
            ...
        ],
        "total": 2
    }
    """
    try:
        import os
        
        # Validate ticket exists
        ticket = Ticket.query.get(ticket_id)
        if not ticket:
            return jsonify({"error": f"Ticket with id {ticket_id} not found"}), 404
        
        # Get all location assignments for this ticket
        assignments = TicketAssignLocation.query.filter_by(ticket_id=ticket_id).order_by(
            TicketAssignLocation.created_at.desc()
        ).all()
        
        if not assignments:
            return jsonify({
                "ticket_id": ticket_id,
                "locations": [],
                "total": 0
            }), 200
        
        # Extract location_ids to fetch from auth system
        location_ids = [assign.location_id for assign in assignments]
        
        # Fetch location details from auth system
        AUTH_SYSTEM_URL = os.getenv("AUTH_SYSTEM_URL", "https://api.dental360grp.com/api")
        clinic_id = ticket.clinic_id or 1  # Default to 1 if clinic_id is None
        
        location_details_map = {}
        try:
            internal_api_url = f"{AUTH_SYSTEM_URL}/clinic_locations/get_all/{clinic_id}"
            print(f"Fetching locations from: {internal_api_url}")
            
            internal_response = requests.get(internal_api_url, timeout=10)
            
            if internal_response.status_code == 200:
                internal_data = internal_response.json()
                all_locations = internal_data.get("locations", [])
                
                # Create a map of location_id -> location details
                for loc in all_locations:
                    loc_id = loc.get("id")
                    if loc_id in location_ids:
                        location_details_map[loc_id] = loc
                
                print(f"✅ Found {len(location_details_map)} matching locations out of {len(all_locations)} total")
            else:
                print(f"⚠️ Failed to fetch locations from auth system: {internal_response.status_code}")
        except Exception as e:
            print(f"⚠️ Error fetching location details from auth system: {e}")
            # Continue without location details
        
        # Build response with location details
        locations = []
        for assign in assignments:
            location_data = {
                "id": assign.id,
                "location_id": assign.location_id,
                "created_at": assign.created_at.isoformat() if assign.created_at else None,
                "created_by": assign.created_by
            }
            
            # Add location details if available
            if assign.location_id in location_details_map:
                location_data["location_details"] = location_details_map[assign.location_id]
            else:
                location_data["location_details"] = None
            
            locations.append(location_data)
        
        return jsonify({
            "ticket_id": ticket_id,
            "locations": locations,
            "total": len(locations)
        }), 200
        
    except Exception as e:
        print(f"❌ Error getting ticket locations: {e}")
        return jsonify({
            "status": "error",
            "error": str(e)
        }), 500


@ticket_bp.route("/ticket/<int:ticket_id>/locations/<int:location_id>", methods=["DELETE"])
@require_api_key
@validate_token
def remove_ticket_location(ticket_id, location_id):
    """
    Remove a location assignment from a ticket.
    
    Deletes the specific location assignment from ticket_assign_locations table.
    """
    try:
        # Validate ticket exists
        ticket = Ticket.query.get(ticket_id)
        if not ticket:
            return jsonify({"error": f"Ticket with id {ticket_id} not found"}), 404
        
        # Find the assignment
        assignment = TicketAssignLocation.query.filter_by(
            ticket_id=ticket_id,
            location_id=location_id
        ).first()
        
        if not assignment:
            return jsonify({
                "error": f"Location {location_id} is not assigned to ticket {ticket_id}"
            }), 404
        
        db.session.delete(assignment)
        db.session.commit()
        
        return jsonify({
            "status": "success",
            "message": f"Location {location_id} removed from ticket {ticket_id}",
            "ticket_id": ticket_id,
            "location_id": location_id
        }), 200
        
    except Exception as e:
        db.session.rollback()
        print(f"❌ Error removing location from ticket: {e}")
        return jsonify({
            "status": "error",
            "error": str(e)
        }), 500


@ticket_bp.route("/ticket/<int:ticket_id>/followers", methods=["POST"])
@require_api_key
@validate_token
def add_ticket_followers(ticket_id):
    """
    Add one or more followers to a ticket.
    Uses TicketFollowUp table to track followers.
    
    Request body (JSON):
    {
        "user_ids": [12, 15, 20],  # Array of user IDs to add as followers
        "added_by": 456,  # Optional: User ID who added the followers
        "note": "Optional note about why these users are following"  # Optional
    }
    
    Returns list of successfully added followers.
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({"error": "Request body is required"}), 400
        
        user_ids = data.get("user_ids")
        added_by = data.get("user_id")  # Support both field names
        note = data.get("note", "Added as follow-up user")
        
        # Validate required fields
        if not user_ids:
            return jsonify({"error": "user_ids is required"}), 400
        
        if not isinstance(user_ids, list):
            return jsonify({"error": "user_ids must be an array"}), 400
        
        if len(user_ids) == 0:
            return jsonify({"error": "user_ids cannot be empty"}), 400
        
        # Validate ticket exists
        ticket = Ticket.query.get(ticket_id)
        if not ticket:
            return jsonify({"error": f"Ticket with id {ticket_id} not found"}), 404
        
        # Remove duplicates from user_ids
        user_ids = list(set(user_ids))
        
        # Get existing followers to avoid duplicates
        existing_followups = TicketFollowUp.query.filter_by(ticket_id=ticket_id).all()
        existing_user_ids = {fu.user_id for fu in existing_followups}
        
        # Filter out users that are already followers
        new_user_ids = [uid for uid in user_ids if uid not in existing_user_ids]
        
        if not new_user_ids:
            return jsonify({
                "status": "success",
                "message": "All users are already following this ticket",
                "ticket_id": ticket_id,
                "existing_followers": list(existing_user_ids),
                "skipped": user_ids
            }), 200
        
        # Get updater info for notifications
        updater_info = get_user_info_by_id(added_by) if added_by else {"username": "System"}
        
        # Create new follow-up entries
        added_followers = []
        skipped_followers = []
        
        for user_id in user_ids:
            if user_id in existing_user_ids:
                skipped_followers.append(user_id)
                continue
            
            # Skip if user is trying to add themselves (unless explicitly allowed)
            if user_id == added_by and added_by:
                # Allow self-follow but log it
                pass
            
            followup = TicketFollowUp(
                ticket_id=ticket_id,
                user_id=user_id,
                note=note,
                created_at=datetime.utcnow()
            )
            db.session.add(followup)
            added_followers.append(user_id)
        
        db.session.commit()
        
        # Send notifications and emails to relevant users
        assignment = TicketAssignment.query.filter_by(ticket_id=ticket_id).first()
        
        # Collect all recipients (new followers + assignees)
        recipients = set(added_followers)
        if assignment:
            if assignment.assign_by:
                recipients.add(assignment.assign_by)
            if assignment.assign_to:
                recipients.add(assignment.assign_to)
        
        # Send notifications to new followers and notify assignees
        for user_id in added_followers:
            user_info = get_user_info_by_id(user_id)
            if user_info:
                # Notify the new follower
                create_notification(
                    ticket_id=ticket_id,
                    receiver_id=user_id,
                    sender_id=added_by,
                    notification_type="followup",
                    message=f"You are now following ticket #{ticket_id}"
                )
        
        # Notify assignees about new followers
        for recipient_id in recipients:
            if recipient_id in added_followers:
                continue  # Already notified above
            
            user_info = get_user_info_by_id(recipient_id)
            if user_info:
                follower_names = []
                for fid in added_followers:
                    finfo = get_user_info_by_id(fid)
                    if finfo:
                        follower_names.append(finfo.get("username", f"User {fid}"))
                
                follower_list = ", ".join(follower_names)
                send_update_ticket_email(
                    ticket,
                    user_info,
                    updater_info,
                    [("followup", "", f"{follower_list} started following this ticket")]
                )
                create_notification(
                    ticket_id=ticket_id,
                    receiver_id=recipient_id,
                    sender_id=added_by,
                    notification_type="followup",
                    message=f"{follower_list} has been added as a follow-up user"
                )
        
        # Get all followers for this ticket (including existing ones)
        all_followups = TicketFollowUp.query.filter_by(ticket_id=ticket_id).all()
        all_follower_ids = [fu.user_id for fu in all_followups]
        
        return jsonify({
            "status": "success",
            "message": f"Successfully added {len(added_followers)} follower(s) to ticket",
            "ticket_id": ticket_id,
            "added_followers": added_followers,
            "all_followers": all_follower_ids,
            "skipped": skipped_followers
        }), 200
        
    except Exception as e:
        db.session.rollback()
        print(f"❌ Error adding followers to ticket: {e}")
        return jsonify({
            "status": "error",
            "error": str(e)
        }), 500


@ticket_bp.route("/ticket/<int:ticket_id>/followers", methods=["GET"])
@require_api_key
@validate_token
def get_ticket_followers(ticket_id):
    """
    Get all followers for a specific ticket.
    
    Returns:
    {
        "ticket_id": 123,
        "followers": [
            {
                "id": 1,
                "user_id": 12,
                "username": "john_doe",
                "note": "Added as follow-up user",
                "created_at": "2024-01-15T10:30:00",
                "followup_date": "2024-01-15T10:30:00"
            },
            ...
        ],
        "total": 2
    }
    """
    try:
        # Validate ticket exists
        ticket = Ticket.query.get(ticket_id)
        if not ticket:
            return jsonify({"error": f"Ticket with id {ticket_id} not found"}), 404
        
        # Get all followers for this ticket
        followups = TicketFollowUp.query.filter_by(ticket_id=ticket_id).order_by(
            TicketFollowUp.created_at.desc()
        ).all()
        
        followers = []
        for fu in followups:
            user_info = get_user_info_by_id(fu.user_id) if fu.user_id else None
            followers.append({
                "id": fu.id,
                "user_id": fu.user_id,
                "username": user_info.get("username") if user_info else None,
                "note": fu.note,
                "created_at": fu.created_at.isoformat() if fu.created_at else None,
                "followup_date": fu.followup_date.isoformat() if fu.followup_date else None
            })
        
        return jsonify({
            "ticket_id": ticket_id,
            "followers": followers,
            "total": len(followers)
        }), 200
        
    except Exception as e:
        print(f"❌ Error getting ticket followers: {e}")
        return jsonify({
            "status": "error",
            "error": str(e)
        }), 500


@ticket_bp.route("/ticket/<int:ticket_id>/followers/<int:user_id>", methods=["DELETE"])
@require_api_key
@validate_token
def remove_ticket_follower(ticket_id, user_id):
    """
    Remove a follower from a ticket.
    
    Deletes the specific follower entry from TicketFollowUp table.
    """
    try:
        # Validate ticket exists
        ticket = Ticket.query.get(ticket_id)
        if not ticket:
            return jsonify({"error": f"Ticket with id {ticket_id} not found"}), 404
        
        # Find the follow-up entry
        followup = TicketFollowUp.query.filter_by(
            ticket_id=ticket_id,
            user_id=user_id
        ).first()
        
        if not followup:
            return jsonify({
                "error": f"User {user_id} is not following ticket {ticket_id}"
            }), 404
        
        db.session.delete(followup)
        db.session.commit()
        
        return jsonify({
            "status": "success",
            "message": f"User {user_id} removed from ticket {ticket_id} followers",
            "ticket_id": ticket_id,
            "user_id": user_id
        }), 200
        
    except Exception as e:
        db.session.rollback()
        print(f"❌ Error removing follower from ticket: {e}")
        return jsonify({
            "status": "error",
            "error": str(e)
        }), 500

