import os
from datetime import datetime
from flask import Blueprint, request, jsonify
from app import db
from sqlalchemy import or_, and_

from app.model import (
    Project, ProjectTicket, ProjectTag, ProjectAssignment,
    Ticket, TicketAssignment, TicketFile, TicketTag, TicketComment,
    Category, TicketFollowUp, TicketStatusLog
)
from app.utils.helper_function import upload_to_s3, get_user_info_by_id
from app.utils.email_templete import send_project_assignment_email, send_project_update_email, send_project_ticket_created_email
from app.notification_route import create_notification
from app.dashboard_routes import require_api_key, validate_token

# ─── Blueprint ─────────────────────────────────────────────
project_bp = Blueprint("projects", __name__, url_prefix="/api/projects")


# ─────────────────────────────────────────────
# Create Project
@project_bp.route("/project", methods=["POST"])
@require_api_key
@validate_token
def create_project():
    data = request.get_json()
    
    if not data.get("name"):
        return jsonify({"error": "Project name is required"}), 400
    
    # Parse due_date if provided
    due_date = None
    if data.get("due_date"):
        try:
            due_date = datetime.strptime(data["due_date"], "%Y-%m-%d").date()
        except ValueError:
            return jsonify({"error": "Invalid due_date format. Use YYYY-MM-DD"}), 400
    
    # Create project
    project = Project(
        name=data.get("name"),
        description=data.get("description"),
        status=data.get("status", "Active"),
        priority=data.get("priority", "Low"),
        due_date=due_date,
        color=data.get("color", "#ef4444"),
        created_by=data.get("created_by")
    )
    
    db.session.add(project)
    db.session.commit()
    
    # Add tags if provided
    tags = []
    if data.get("tags"):
        if isinstance(data["tags"], list):
            for tag_name in data["tags"]:
                if tag_name:
                    tag = ProjectTag(project_id=project.id, tag_name=str(tag_name).strip())
                    db.session.add(tag)
                    tags.append(tag_name)
        db.session.commit()
    
    # Add team members if provided
    team_members = []
    if data.get("team_member_ids"):
        if isinstance(data["team_member_ids"], list):
            for user_id in data["team_member_ids"]:
                if user_id:
                    assignment = ProjectAssignment(
                        project_id=project.id,
                        user_id=user_id,
                        assigned_by=data.get("created_by")
                    )
                    db.session.add(assignment)
                    team_members.append(user_id)
        db.session.commit()
    
    # Get created_by info
    created_by_info = get_user_info_by_id(project.created_by) if project.created_by else None
    
    # Get team member info
    team_member_info = []
    for user_id in team_members:
        user_info = get_user_info_by_id(user_id)
        if user_info:
            team_member_info.append({
                "user_id": user_id,
                "username": user_info.get("username"),
                "email": user_info.get("email")
            })
    
    return jsonify({
        "success": True,
        "message": "Project created successfully",
        "project": {
            "id": project.id,
            "name": project.name,
            "description": project.description,
            "status": project.status,
            "priority": project.priority,
            "due_date": project.due_date.isoformat() if project.due_date else None,
            "color": project.color,
            "created_by": created_by_info,
            "created_at": project.created_at.isoformat(),
            "tags": tags,
            "team_members": team_member_info
        }
    }), 201


# ─────────────────────────────────────────────
# Get All Projects (with Filters)
@project_bp.route("/projects", methods=["GET"])
@require_api_key
@validate_token
def get_projects():
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 10, type=int)
    
    # Filters
    status = request.args.get("status")
    priority = request.args.get("priority")
    created_by = request.args.get("created_by", type=int)
    assigned_to = request.args.get("assigned_to", type=int)  # Team member filter
    tag = request.args.get("tag")
    search = request.args.get("search", "").strip()
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")
    
    query = Project.query
    
    if status:
        query = query.filter(Project.status.ilike(f"%{status}%"))
    if priority:
        query = query.filter(Project.priority.ilike(f"%{priority}%"))
    if start_date:
        query = query.filter(Project.created_at >= start_date)
    if end_date:
        query = query.filter(Project.created_at <= end_date)
    if search:
        query = query.filter(or_(
            Project.name.ilike(f"%{search}%"),
            Project.description.ilike(f"%{search}%")
        ))
    if created_by:
        query = query.filter(Project.created_by == created_by)
    if assigned_to:
        # Filter projects where user is assigned as team member
        project_ids = [a.project_id for a in ProjectAssignment.query.filter_by(user_id=assigned_to).all()]
        query = query.filter(Project.id.in_(project_ids))
    if tag:
        # Filter projects by tag
        project_ids = [t.project_id for t in ProjectTag.query.filter(ProjectTag.tag_name.ilike(f"%{tag}%")).all()]
        query = query.filter(Project.id.in_(project_ids))
    
    query = query.order_by(Project.created_at.desc())
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    projects = pagination.items
    
    result = []
    for p in projects:
        created_by_info = get_user_info_by_id(p.created_by) if p.created_by else None
        
        # Get tags
        tags = [tag.tag_name for tag in ProjectTag.query.filter_by(project_id=p.id).all()]
        
        # Get team members
        assignments = ProjectAssignment.query.filter_by(project_id=p.id).all()
        team_members = []
        for a in assignments:
            user_info = get_user_info_by_id(a.user_id) if a.user_id else None
            team_members.append({
                "user_id": a.user_id,
                "username": user_info.get("username") if user_info else None,
                "email": user_info.get("email") if user_info else None,
                "assigned_at": a.assigned_at.isoformat() if a.assigned_at else None
            })
        
        # Get ticket count and status counts
        project_ticket_ids = [pt.ticket_id for pt in ProjectTicket.query.filter_by(project_id=p.id).all()]
        ticket_count = len(project_ticket_ids)
        
        # Get ticket status counts
        ticket_status_counts = {}
        if project_ticket_ids:
            tickets = Ticket.query.filter(Ticket.id.in_(project_ticket_ids)).all()
            for ticket in tickets:
                status = ticket.status or "Unknown"
                ticket_status_counts[status] = ticket_status_counts.get(status, 0) + 1
        
        result.append({
            "id": p.id,
            "name": p.name,
            "description": p.description,
            "status": p.status,
            "priority": p.priority,
            "due_date": p.due_date.isoformat() if p.due_date else None,
            "color": p.color,
            "created_by": created_by_info,
            "created_at": p.created_at.isoformat(),
            "updated_at": p.updated_at.isoformat() if p.updated_at else None,
            "tags": tags,
            "team_members": team_members,
            "ticket_count": ticket_count,
            "ticket_status_counts": ticket_status_counts
        })
    
    return jsonify({
        "projects": result,
        "pagination": {
            "page": pagination.page,
            "per_page": pagination.per_page,
            "total": pagination.total,
            "pages": pagination.pages
        }
    })


# ─────────────────────────────────────────────
# Get Project by ID
@project_bp.route("/project/<int:project_id>", methods=["GET"])
@require_api_key
@validate_token
def get_project(project_id):
    project = Project.query.get(project_id)
    if not project:
        return jsonify({"error": "Project not found"}), 404
    
    created_by_info = get_user_info_by_id(project.created_by) if project.created_by else None
    
    # Get tags
    tags = []
    for tag in ProjectTag.query.filter_by(project_id=project.id).all():
        tags.append({
            "id": tag.id,
            "tag_name": tag.tag_name,
            "created_at": tag.created_at.isoformat()
        })
    
    # Get team members
    assignments = ProjectAssignment.query.filter_by(project_id=project.id).all()
    team_members = []
    for a in assignments:
        user_info = get_user_info_by_id(a.user_id) if a.user_id else None
        assigned_by_info = get_user_info_by_id(a.assigned_by) if a.assigned_by else None
        team_members.append({
            "user_id": a.user_id,
            "username": user_info.get("username") if user_info else None,
            "email": user_info.get("email") if user_info else None,
            "assigned_by": a.assigned_by,
            "assigned_by_username": assigned_by_info.get("username") if assigned_by_info else None,
            "assigned_at": a.assigned_at.isoformat() if a.assigned_at else None
        })
    
    # Get tickets linked to this project
    project_tickets = ProjectTicket.query.filter_by(project_id=project.id).all()
    tickets = []
    for pt in project_tickets:
        ticket = Ticket.query.get(pt.ticket_id)
        if ticket:
            created_by_ticket = get_user_info_by_id(ticket.user_id) if ticket.user_id else None
            category = None
            if ticket.category_id:
                cat = Category.query.get(ticket.category_id)
                if cat:
                    category = {"id": cat.id, "name": cat.name}
            
            tickets.append({
                "id": ticket.id,
                "title": ticket.title,
                "details": ticket.details,
                "status": ticket.status,
                "priority": ticket.priority,
                "created_at": ticket.created_at.isoformat(),
                "created_by": created_by_ticket,
                "category": category
            })
    
    return jsonify({
        "id": project.id,
        "name": project.name,
        "description": project.description,
        "status": project.status,
        "priority": project.priority,
        "due_date": project.due_date.isoformat() if project.due_date else None,
        "color": project.color,
        "created_by": created_by_info,
        "created_at": project.created_at.isoformat(),
        "updated_at": project.updated_at.isoformat() if project.updated_at else None,
        "tags": tags,
        "team_members": team_members,
        "tickets": tickets,
        "ticket_count": len(tickets)
    })


# ─────────────────────────────────────────────
# Update Project
@project_bp.route("/project/<int:project_id>", methods=["PATCH"])
@require_api_key
@validate_token
def update_project(project_id):
    project = Project.query.get(project_id)
    if not project:
        return jsonify({"error": "Project not found"}), 404
    
    data = request.get_json()
    updated_fields = []
    changes = []
    
    if "name" in data:
        old_name = project.name
        project.name = data["name"]
        updated_fields.append("name")
        changes.append(("name", old_name, project.name))
    if "description" in data:
        old_description = project.description or "Not set"
        project.description = data["description"]
        updated_fields.append("description")
        changes.append(("description", old_description, project.description or "Not set"))
    if "status" in data:
        old_status = project.status
        project.status = data["status"]
        updated_fields.append("status")
        changes.append(("status", old_status, project.status))
    if "priority" in data:
        old_priority = project.priority
        project.priority = data["priority"]
        updated_fields.append("priority")
        changes.append(("priority", old_priority, project.priority))
    if "due_date" in data:
        old_due_date = project.due_date.strftime("%Y-%m-%d") if project.due_date else "Not set"
        if data["due_date"]:
            try:
                project.due_date = datetime.strptime(data["due_date"], "%Y-%m-%d").date()
                new_due_date = project.due_date.strftime("%Y-%m-%d")
            except ValueError:
                return jsonify({"error": "Invalid due_date format. Use YYYY-MM-DD"}), 400
        else:
            project.due_date = None
            new_due_date = "Not set"
        updated_fields.append("due_date")
        changes.append(("due_date", old_due_date, new_due_date))
    if "color" in data:
        old_color = project.color
        project.color = data["color"]
        updated_fields.append("color")
        changes.append(("color", old_color, project.color))
    
    project.updated_at = datetime.utcnow()
    db.session.commit()
    
    # Send email notifications to all team members about the update
    if updated_fields and changes:
        team_assignments = ProjectAssignment.query.filter_by(project_id=project.id).all()
        updater_info = get_user_info_by_id(data.get("updated_by")) if data.get("updated_by") else None
        
        # Notify all team members
        for assignment in team_assignments:
            user_info = get_user_info_by_id(assignment.user_id)
            if user_info:
                send_project_update_email(project, user_info, updater_info, changes)
                # Create notification
                create_notification(
                    ticket_id=None,
                    receiver_id=assignment.user_id,
                    sender_id=data.get("updated_by"),
                    notification_type="project_update",
                    message=f"Project '{project.name}' has been updated"
                )
    
    return jsonify({
        "success": True,
        "message": "Project updated successfully",
        "updated_fields": updated_fields,
        "project": {
            "id": project.id,
            "name": project.name,
            "description": project.description,
            "status": project.status,
            "priority": project.priority,
            "due_date": project.due_date.isoformat() if project.due_date else None,
            "color": project.color,
            "updated_at": project.updated_at.isoformat()
        }
    })


# ─────────────────────────────────────────────
# Delete Project
@project_bp.route("/project/<int:project_id>", methods=["DELETE"])
@require_api_key
@validate_token
def delete_project(project_id):
    project = Project.query.get(project_id)
    if not project:
        return jsonify({"error": "Project not found"}), 404
    
    # Delete related records
    ProjectTag.query.filter_by(project_id=project.id).delete()
    ProjectAssignment.query.filter_by(project_id=project.id).delete()
    ProjectTicket.query.filter_by(project_id=project.id).delete()
    
    db.session.delete(project)
    db.session.commit()
    
    return jsonify({
        "success": True,
        "message": "Project deleted successfully"
    })


# ─────────────────────────────────────────────
# Create Ticket for Project
@project_bp.route("/project/<int:project_id>/ticket", methods=["POST"])
@require_api_key
@validate_token
def create_project_ticket(project_id):
    project = Project.query.get(project_id)
    if not project:
        return jsonify({"error": "Project not found"}), 404
    
    data = request.form
    
    # Parse due_date if provided
    due_date = None
    if data.get("due_date"):
        try:
            due_date = datetime.strptime(data["due_date"], "%Y-%m-%d").date()
        except ValueError:
            return jsonify({"error": "Invalid due_date format. Use YYYY-MM-DD"}), 400
    
    # Create ticket
    ticket = Ticket(
        clinic_id=data.get("clinic_id"),
        location_id=data.get("location_id"),
        user_id=data.get("user_id"),
        title=data.get("title"),
        details=data.get("details"),
        category_id=data.get("category_id"),
        status=data.get("status", "Pending"),
        priority=data.get("priority", "Low"),
        due_date=due_date
    )
    
    db.session.add(ticket)
    db.session.commit()
    
    # Link ticket to project
    project_ticket = ProjectTicket(
        project_id=project_id,
        ticket_id=ticket.id
    )
    db.session.add(project_ticket)
    
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
    
    # Handle tags
    tags = []
    if data.get("tags"):
        tag_list = data["tags"].split(",") if isinstance(data["tags"], str) else data["tags"]
        for tag_name in tag_list:
            if tag_name.strip():
                tag = TicketTag(ticket_id=ticket.id, tag_name=tag_name.strip())
                db.session.add(tag)
                tags.append(tag_name.strip())
    
    # Auto-assign based on category
    if ticket.category_id:
        category = Category.query.get(ticket.category_id)
        if category and category.assignee_id:
            assignment = TicketAssignment(
                ticket_id=ticket.id,
                assign_to=category.assignee_id,
                assign_by=ticket.user_id
            )
            db.session.add(assignment)
    
    db.session.commit()
    
    # Send email notifications to all project team members about the new ticket
    team_assignments = ProjectAssignment.query.filter_by(project_id=project_id).all()
    for assignment in team_assignments:
        user_info = get_user_info_by_id(assignment.user_id)
        if user_info:
            send_project_ticket_created_email(project, ticket, user_info)
            # Create notification
            create_notification(
                ticket_id=ticket.id,
                receiver_id=assignment.user_id,
                sender_id=ticket.user_id,
                notification_type="project_ticket",
                message=f"New ticket created in project: {project.name}"
            )
    
    return jsonify({
        "success": True,
        "message": "Ticket created and linked to project successfully",
        "ticket": {
            "id": ticket.id,
            "title": ticket.title,
            "details": ticket.details,
            "status": ticket.status,
            "priority": ticket.priority,
            "project_id": project_id,
            "files": uploaded_files,
            "tags": tags
        }
    }), 201


# ─────────────────────────────────────────────
# Get Tickets for Project
@project_bp.route("/project/<int:project_id>/tickets", methods=["GET"])
@require_api_key
@validate_token
def get_project_tickets(project_id):
    project = Project.query.get(project_id)
    if not project:
        return jsonify({"error": "Project not found"}), 404
    
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 10, type=int)
    
    # Filters
    status = request.args.get("status")
    category_id = request.args.get("category_id", type=int)
    priority = request.args.get("priority")
    search = request.args.get("search", "").strip()
    
    # Get ticket IDs for this project
    project_ticket_ids = [pt.ticket_id for pt in ProjectTicket.query.filter_by(project_id=project_id).all()]
    
    if not project_ticket_ids:
        return jsonify({
            "tickets": [],
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": 0,
                "pages": 0
            }
        })
    
    query = Ticket.query.filter(Ticket.id.in_(project_ticket_ids))
    
    if status:
        query = query.filter(Ticket.status.ilike(f"%{status}%"))
    if category_id:
        query = query.filter(Ticket.category_id == category_id)
    if priority:
        query = query.filter(Ticket.priority.ilike(f"%{priority}%"))
    if search:
        query = query.filter(or_(
            Ticket.title.ilike(f"%{search}%"),
            Ticket.details.ilike(f"%{search}%")
        ))
    
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
                "assign_by_username": assign_by_info.get("username") if assign_by_info else None,
                "assign_to": a.assign_to,
                "assign_to_username": assign_to_info.get("username") if assign_to_info else None,
                "assigned_at": a.assigned_at.isoformat() if a.assigned_at else None
            })
        
        files = [{"name": f.file_name, "url": f.file_url}
                 for f in TicketFile.query.filter_by(ticket_id=t.id).all()]
        tags = [tag.tag_name for tag in TicketTag.query.filter_by(ticket_id=t.id).all()]
        
        category = None
        if t.category_id:
            cat = Category.query.get(t.category_id)
            if cat:
                category = {"id": cat.id, "name": cat.name, "is_active": cat.is_active}
        
        result.append({
            "id": t.id,
            "title": t.title,
            "details": t.details,
            "priority": t.priority,
            "status": t.status,
            "due_date": t.due_date.isoformat() if t.due_date else None,
            "created_at": t.created_at.isoformat(),
            "created_by": created_by_info,
            "assignees": assignees,
            "files": files,
            "tags": tags,
            "category": category
        })
    
    return jsonify({
        "project_id": project_id,
        "tickets": result,
        "pagination": {
            "page": pagination.page,
            "per_page": pagination.per_page,
            "total": pagination.total,
            "pages": pagination.pages
        }
    })


# ─────────────────────────────────────────────
# Assign Team Members to Project
@project_bp.route("/project/<int:project_id>/assign", methods=["POST"])
@require_api_key
@validate_token
def assign_project_team_members(project_id):
    project = Project.query.get(project_id)
    if not project:
        return jsonify({"error": "Project not found"}), 404
    
    data = request.get_json()
    user_ids = data.get("user_ids", [])
    assigned_by = data.get("assigned_by")
    
    if not isinstance(user_ids, list):
        return jsonify({"error": "user_ids must be an array"}), 400
    
    if data.get("replace", False):
        # Remove existing assignments
        ProjectAssignment.query.filter_by(project_id=project_id).delete()
        db.session.commit()
    
    added_members = []
    for user_id in user_ids:
        # Check if already assigned
        existing = ProjectAssignment.query.filter_by(project_id=project_id, user_id=user_id).first()
        if not existing:
            assignment = ProjectAssignment(
                project_id=project_id,
                user_id=user_id,
                assigned_by=assigned_by
            )
            db.session.add(assignment)
            added_members.append(user_id)
    
    db.session.commit()
    
    # Get user info for added members and send email notifications
    team_member_info = []
    assigner_info = get_user_info_by_id(assigned_by) if assigned_by else None
    
    for user_id in added_members:
        user_info = get_user_info_by_id(user_id)
        if user_info:
            team_member_info.append({
                "user_id": user_id,
                "username": user_info.get("username"),
                "email": user_info.get("email")
            })
            # Send assignment email
            send_project_assignment_email(project, user_info, assigner_info)
            # Create notification
            create_notification(
                ticket_id=None,  # No ticket for project assignment
                receiver_id=user_id,
                sender_id=assigned_by,
                notification_type="project_assignment",
                message=f"You have been assigned to project: {project.name}"
            )
    
    return jsonify({
        "success": True,
        "message": f"Assigned {len(added_members)} team member(s) to project",
        "team_members": team_member_info
    })


# ─────────────────────────────────────────────
# Remove Team Member from Project
@project_bp.route("/project/<int:project_id>/assign/<int:user_id>", methods=["DELETE"])
@require_api_key
@validate_token
def remove_project_team_member(project_id, user_id):
    project = Project.query.get(project_id)
    if not project:
        return jsonify({"error": "Project not found"}), 404
    
    assignment = ProjectAssignment.query.filter_by(project_id=project_id, user_id=user_id).first()
    if not assignment:
        return jsonify({"error": "Team member not assigned to this project"}), 404
    
    db.session.delete(assignment)
    db.session.commit()
    
    return jsonify({
        "success": True,
        "message": "Team member removed from project"
    })


# ─────────────────────────────────────────────
# Add Tags to Project
@project_bp.route("/project/<int:project_id>/tags", methods=["POST"])
@require_api_key
@validate_token
def add_project_tags(project_id):
    project = Project.query.get(project_id)
    if not project:
        return jsonify({"error": "Project not found"}), 404
    
    data = request.get_json()
    tags = data.get("tags", [])
    
    if not isinstance(tags, list):
        return jsonify({"error": "tags must be an array"}), 400
    
    if data.get("replace", False):
        # Remove existing tags
        ProjectTag.query.filter_by(project_id=project_id).delete()
        db.session.commit()
    
    added_tags = []
    for tag_name in tags:
        if tag_name and tag_name.strip():
            tag_name_clean = str(tag_name).strip()
            # Check if tag already exists
            existing = ProjectTag.query.filter_by(project_id=project_id, tag_name=tag_name_clean).first()
            if not existing:
                tag = ProjectTag(project_id=project_id, tag_name=tag_name_clean)
                db.session.add(tag)
                added_tags.append(tag_name_clean)
    
    db.session.commit()
    
    return jsonify({
        "success": True,
        "message": f"Added {len(added_tags)} tag(s) to project",
        "tags": added_tags
    })


# ─────────────────────────────────────────────
# Remove Tag from Project
@project_bp.route("/project/<int:project_id>/tags/<int:tag_id>", methods=["DELETE"])
@require_api_key
@validate_token
def remove_project_tag(project_id, tag_id):
    project = Project.query.get(project_id)
    if not project:
        return jsonify({"error": "Project not found"}), 404
    
    tag = ProjectTag.query.filter_by(project_id=project_id, id=tag_id).first()
    if not tag:
        return jsonify({"error": "Tag not found"}), 404
    
    db.session.delete(tag)
    db.session.commit()
    
    return jsonify({
        "success": True,
        "message": "Tag removed from project"
    })


# ─────────────────────────────────────────────
# Link Existing Ticket to Project
@project_bp.route("/project/<int:project_id>/link_ticket", methods=["POST"])
@require_api_key
@validate_token
def link_ticket_to_project(project_id):
    project = Project.query.get(project_id)
    if not project:
        return jsonify({"error": "Project not found"}), 404
    
    data = request.get_json()
    ticket_id = data.get("ticket_id")
    
    if not ticket_id:
        return jsonify({"error": "ticket_id is required"}), 400
    
    ticket = Ticket.query.get(ticket_id)
    if not ticket:
        return jsonify({"error": "Ticket not found"}), 404
    
    # Check if already linked
    existing = ProjectTicket.query.filter_by(project_id=project_id, ticket_id=ticket_id).first()
    if existing:
        return jsonify({"error": "Ticket already linked to this project"}), 400
    
    project_ticket = ProjectTicket(
        project_id=project_id,
        ticket_id=ticket_id
    )
    db.session.add(project_ticket)
    db.session.commit()
    
    # Send email notifications to all project team members about the linked ticket
    team_assignments = ProjectAssignment.query.filter_by(project_id=project_id).all()
    for assignment in team_assignments:
        user_info = get_user_info_by_id(assignment.user_id)
        if user_info:
            send_project_ticket_created_email(project, ticket, user_info)
            # Create notification
            create_notification(
                ticket_id=ticket_id,
                receiver_id=assignment.user_id,
                sender_id=data.get("linked_by", ticket.user_id),
                notification_type="project_ticket",
                message=f"Ticket #{ticket_id} linked to project: {project.name}"
            )
    
    return jsonify({
        "success": True,
        "message": "Ticket linked to project successfully",
        "project_ticket": {
            "id": project_ticket.id,
            "project_id": project_id,
            "ticket_id": ticket_id
        }
    }), 201


# ─────────────────────────────────────────────
# Unlink Ticket from Project
@project_bp.route("/project/<int:project_id>/ticket/<int:ticket_id>", methods=["DELETE"])
@require_api_key
@validate_token
def unlink_project_ticket(project_id, ticket_id):
    project = Project.query.get(project_id)
    if not project:
        return jsonify({"error": "Project not found"}), 404
    
    project_ticket = ProjectTicket.query.filter_by(project_id=project_id, ticket_id=ticket_id).first()
    if not project_ticket:
        return jsonify({"error": "Ticket not linked to this project"}), 404
    
    db.session.delete(project_ticket)
    db.session.commit()
    
    return jsonify({
        "success": True,
        "message": "Ticket unlinked from project"
    })

