import os, uuid, mimetypes, botocore, boto3, requests
from datetime import datetime
from flask import Blueprint, request, jsonify
from app import db
import aiohttp
from aiohttp import BasicAuth
import asyncio, sys, threading
from sqlalchemy import or_, and_
import re
import html

from app.model import Ticket, TicketAssignment, TicketFile, TicketTag, TicketComment, Category, TicketFollowUp, \
    TicketStatusLog, TicketAssignmentLog, ContactFormTicketLink, EmailProcessedLog
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
        query = query.filter(Ticket.status.ilike(f"%{status}%"))
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
            u_info = get_user_info_by_id(f.user_id)
            followups.append({
                "id": f.id,
                "note": f.note,
                "user_id": f.user_id,
                "username": u_info["username"] if u_info else None,
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
            "status_logs": status_logs
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
@require_api_key
@validate_token
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
        u_info = get_user_info_by_id(f.user_id)
        followups.append({
            "id": f.id,
            "note": f.note,
            "user_id": f.user_id,
            "username": u_info["username"] if u_info else None,
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
        "created_by": created_by,
        "assignees": assignees,
        "assignment_logs": assignment_logs,   # :white_check_mark: NEW
        "files": files,
        "tags": tags,
        "comments": comments,
        "followups": followups,
        "category": category,
        "status_logs": status_logs,
        "contact_form_info": contact_form_info  # :white_check_mark: Patient contact information from contact form
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
        query = query.filter(Ticket.status.ilike(f"%{status}%"))
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
                
                # Skip if already processed (check by email_id first - most important check)
                existing_log = EmailProcessedLog.query.filter_by(email_id=email_id).first()
                if existing_log:
                    print(f"⏭️ Email {email_id} already processed, skipping...")
                    skipped_count += 1
                    continue
                
                # Skip system-generated notification emails (prevent infinite loops)
                if subject and any(pattern in subject for pattern in system_email_patterns):
                    print(f"⏭️ Skipping system notification email: {subject}")
                    # Still log it as processed to avoid reprocessing
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
                    skipped_count += 1
                    continue
                
                # CRITICAL: Check conversation_id EARLY to prevent duplicate tickets
                # This must be checked BEFORE processing content to avoid race conditions
                existing_conversation = None
                if conversation_id:
                    # Check if ANY email in this conversation already has a ticket
                    existing_conversation = EmailProcessedLog.query.filter_by(
                        conversation_id=conversation_id
                    ).filter(
                        EmailProcessedLog.ticket_id.isnot(None)  # Only get entries with tickets
                    ).order_by(EmailProcessedLog.processed_at.desc()).first()  # Get most recent
                    
                    if existing_conversation:
                        print(f"🔍 Found existing conversation {conversation_id} with ticket #{existing_conversation.ticket_id}")
                
                # Get user_id from sender email
                user_id = None
                if sender_email:
                    user_id = get_user_id_by_email(sender_email)
                    if not user_id:
                        print(f"⚠️ User not found for email: {sender_email}")
                
                # Extract email content
                raw_body = email.get("body", {}).get("content") if email.get("body") else None
                content_type = email.get("body", {}).get("contentType") if email.get("body") else None
                body_preview = email.get("bodyPreview", "")
                
                # Extract main content (plain text) from HTML or use preview
                if content_type and content_type.lower() == "html":
                    initial_content = extract_main_content_from_html(raw_body or "", body_preview)
                else:
                    initial_content = (raw_body or body_preview or "").strip()
                
                # Clean email content using LLM (with fallback to simple extraction)
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
                    # Double-check this email wasn't already processed (race condition protection)
                    existing_email_check = EmailProcessedLog.query.filter_by(email_id=email_id).first()
                    if existing_email_check:
                        print(f"⏭️ Email {email_id} already processed (race condition), skipping...")
                        skipped_count += 1
                        continue
                    
                    ticket = Ticket.query.get(existing_conversation.ticket_id)
                    if ticket:
                        comment_text = f"📧 Email Follow-up from {sender_name or sender_email}\n\n{main_content}"
                        
                        # Check if this exact comment already exists (prevent duplicates)
                        existing_comment = TicketComment.query.filter_by(
                            ticket_id=ticket.id,
                            user_id=user_id,
                            comment=comment_text
                        ).first()
                        
                        if existing_comment:
                            print(f"⏭️ Comment already exists for email {email_id}, skipping...")
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
                            db.session.add(email_log)
                            db.session.commit()
                            skipped_count += 1
                            continue
                        
                        comment = TicketComment(
                            ticket_id=ticket.id,
                            user_id=user_id,
                            comment=comment_text
                        )
                        db.session.add(comment)
                        
                        # Log email as processed BEFORE commit to prevent race conditions
                        email_log = EmailProcessedLog(
                            email_id=email_id,
                            conversation_id=conversation_id,
                            ticket_id=ticket.id,
                            sender_email=sender_email,
                            user_id=user_id,
                            email_subject=subject,
                            is_followup=True
                        )
                        db.session.add(email_log)
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
                                comment_text = f"📧 Email Follow-up from {sender_name or sender_email}\n\n{main_content}"
                                
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
                                    
                                    # Log email as processed
                                    email_log = EmailProcessedLog(
                                        email_id=email_id,
                                        conversation_id=conversation_id,
                                        ticket_id=ticket.id,
                                        sender_email=sender_email,
                                        user_id=user_id,
                                        email_subject=subject,
                                        is_followup=True
                                    )
                                    db.session.add(email_log)
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
                                    db.session.add(email_log)
                                    db.session.commit()
                                    skipped_count += 1
                            continue
                    
                    # Double-check this email wasn't already processed (race condition protection)
                    existing_email_check = EmailProcessedLog.query.filter_by(email_id=email_id).first()
                    if existing_email_check:
                        print(f"⏭️ Email {email_id} already processed (race condition), skipping...")
                        skipped_count += 1
                        continue
                    
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
                                comment_text = f"📧 Email Follow-up from {sender_name or sender_email}\n\n{main_content}"
                                comment = TicketComment(
                                    ticket_id=ticket.id,
                                    user_id=user_id,
                                    comment=comment_text
                                )
                                db.session.add(comment)
                                
                                email_log = EmailProcessedLog(
                                    email_id=email_id,
                                    conversation_id=conversation_id,
                                    ticket_id=ticket.id,
                                    sender_email=sender_email,
                                    user_id=user_id,
                                    email_subject=subject,
                                    is_followup=True
                                )
                                db.session.add(email_log)
                                db.session.commit()
                                
                                comments_added += 1
                                processed_count += 1
                                print(f"✅ Added comment to ticket #{ticket.id} from email {email_id}")
                                continue
                    
                    # Create ticket (only if all checks passed)
                    print(f"📝 Creating new ticket for conversation_id: {conversation_id or 'None'}")
                    ticket = Ticket(
                        clinic_id=None,  # Can be set later if needed
                        title=subject or "Email from " + (sender_name or sender_email or "Unknown"),
                        details=main_content or "(no content)",
                        category_id=category_id,
                        status="Pending",
                        priority="low",
                        due_date=None,
                        user_id=user_id,  # Sender as creator
                        location_id=None,
                    )
                    db.session.add(ticket)
                    db.session.commit()
                    
                    print(f"✅ Created ticket #{ticket.id} for conversation_id: {conversation_id}")
                    
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
                    
                    # Log email as processed IMMEDIATELY after ticket creation
                    # This must happen before processing next email to prevent duplicates
                    email_log = EmailProcessedLog(
                        email_id=email_id,
                        conversation_id=conversation_id,
                        ticket_id=ticket.id,
                        sender_email=sender_email,
                        user_id=user_id,
                        email_subject=subject,
                        is_followup=False
                    )
                    db.session.add(email_log)
                    db.session.commit()
                    
                    print(f"✅ Logged email {email_id} → ticket #{ticket.id} for conversation {conversation_id}")
                    
                    tickets_created += 1
                    processed_count += 1
                    print(f"✅ Created ticket #{ticket.id} from email {email_id}")
                
            except Exception as e:
                print(f"❌ Error processing email {email.get('id', 'unknown')}: {e}")
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

