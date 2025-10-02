from flask import Blueprint, jsonify
from sqlalchemy import func, and_
from datetime import datetime, timedelta, date
from app import db
from app.model import Ticket, Category, TicketFollowUp
from app.dashboard_routes import require_api_key, validate_token

stats_bp = Blueprint("stats", __name__)

@stats_bp.route("/tickets/stats", methods=["GET"])
@require_api_key
@validate_token
def get_ticket_stats():
    # 1️⃣ Total tickets
    total_tickets = db.session.query(func.count(Ticket.id)).scalar() or 0

    # 2️⃣ Status wise count
    status_counts = db.session.query(
        Ticket.status, func.count(Ticket.id)
    ).group_by(Ticket.status).all()
    raw_status = {status: count for status, count in status_counts}

    # Ensure all statuses exist with 0 if missing
    status_data = {
        "Completed": raw_status.get("Completed", 0),
        "In Progress": raw_status.get("In Progress", 0),
        "Pending": raw_status.get("Pending", 0)
    }

    completed_count = status_data["Completed"]
    in_progress_count = status_data["In Progress"]
    pending_count = status_data["Pending"]

    # 3️⃣ Priority wise count
    priority_counts = db.session.query(
        Ticket.priority, func.count(Ticket.id)
    ).group_by(Ticket.priority).all()
    raw_priority = {priority: count for priority, count in priority_counts if priority}

    # Define standard priorities
    priorities = ["High", "Medium", "Low"]
    priority_data = {p: raw_priority.get(p, 0) for p in priorities}

    # 4️⃣ Category wise count
    category_counts = db.session.query(
        Category.name, func.count(Ticket.id)
    ).join(Category, Ticket.category_id == Category.id).group_by(Category.name).all()
    raw_category = {cat: count for cat, count in category_counts}

    # Ensure every category in DB schema appears
    all_categories = [c.name for c in Category.query.all()]
    category_data = {c: raw_category.get(c, 0) for c in all_categories}

    # 5️⃣ Completed tickets in last 30 days
    last_30_days = datetime.utcnow() - timedelta(days=30)
    completed_last_30 = db.session.query(func.count(Ticket.id)).filter(
        Ticket.status == "Completed",
        Ticket.completed_at >= last_30_days
    ).scalar() or 0

    # 6️⃣ Total followups
    followup_counts = db.session.query(
        func.count(TicketFollowUp.id)
    ).scalar() or 0

    # 7️⃣ Daily tickets count (last 7 days with zeros if missing)
    today = date.today()
    raw_daily = dict(
        db.session.query(
            func.date(Ticket.created_at).label("date"),
            func.count(Ticket.id).label("count")
        )
        .filter(Ticket.created_at >= today - timedelta(days=7))
        .group_by(func.date(Ticket.created_at))
        .all()
    )

    daily_stats = []
    for i in range(7):
        d = today - timedelta(days=i)
        daily_stats.append({
            "date": str(d),
            "count": raw_daily.get(d, 0)
        })
    daily_stats.reverse()

    # 8️⃣ Overdue tickets (not completed & past due_date)
    overdue_tickets = (
        Ticket.query.filter(
            and_(
                Ticket.status != "Completed",
                Ticket.due_date < today
            )
        ).all()
    )
    overdue_list = [
        {
            "id": t.id,
            "title": t.title,
            "status": t.status,
            "due_date": str(t.due_date),
            "created_at": str(t.created_at)
        }
        for t in overdue_tickets
    ]

    # ✅ Final merged response
    return jsonify({
        "total_tickets": total_tickets,
        "by_status": status_data,
        "completed_count": completed_count,
        "in_progress_count": in_progress_count,
        "pending_count": pending_count,
        "by_priority": priority_data,
        "by_category": category_data,
        "completed_last_30_days": completed_last_30,
        "total_followups": followup_counts,
        "daily_ticket_stats": daily_stats,
        "overdue_tickets": overdue_list
    })
