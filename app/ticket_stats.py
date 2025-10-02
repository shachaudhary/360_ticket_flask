from flask import Blueprint, jsonify, request
from sqlalchemy import func, and_
from datetime import datetime, timedelta, date
from app import db
from app.model import Ticket, Category, TicketFollowUp
from app.dashboard_routes import require_api_key, validate_token

stats_bp = Blueprint("stats", __name__)

TIMEFRAME_PRESETS = {
    "today":       0,
    "yesterday":   1,
    "last_7_days": 7,
    "last_14_days":14,
    "last_30_days":30,
    "last_60_days":60,
    "last_90_days":90,
    "last_1_year":365,
}

@stats_bp.route("/tickets/stats", methods=["GET"])
@validate_token
@require_api_key
def get_ticket_stats():
    # 🔹 Query params
    timeframe = request.args.get("timeframe")
    clinic_id = request.args.get("clinic_id", type=int)

    # 🔹 Base query
    query = Ticket.query

    # 🔹 Filter by clinic_id
    if clinic_id:
        query = query.filter(Ticket.clinic_id == clinic_id)

    # 🔹 Filter by timeframe
    if timeframe in TIMEFRAME_PRESETS:
        days = TIMEFRAME_PRESETS[timeframe]
        if timeframe == "today":
            start_date = date.today()
            query = query.filter(func.date(Ticket.created_at) == start_date)
        elif timeframe == "yesterday":
            yest = date.today() - timedelta(days=1)
            query = query.filter(func.date(Ticket.created_at) == yest)
        else:
            start_date = datetime.utcnow() - timedelta(days=days)
            query = query.filter(Ticket.created_at >= start_date)

    # 1️⃣ Total tickets
    total_tickets = query.count() or 0

    # 2️⃣ Status wise count
    status_counts = (
        query.with_entities(Ticket.status, func.count(Ticket.id))
        .group_by(Ticket.status)
        .all()
    )
    raw_status = {status: count for status, count in status_counts}
    status_data = {
        "Completed": raw_status.get("completed", 0),
        "In Progress": raw_status.get("in_progress", 0),
        "Pending": raw_status.get("Pending", 0)
    }

    # 3️⃣ Priority wise count
    priority_counts = (
        query.with_entities(Ticket.priority, func.count(Ticket.id))
        .group_by(Ticket.priority)
        .all()
    )
    raw_priority = {priority: count for priority, count in priority_counts if priority}
    priorities = ["High", "Urgent", "Low"]
    priority_data = {p: raw_priority.get(p, 0) for p in priorities}

    # 4️⃣ Category wise count
    category_counts = (
        query.join(Category, Ticket.category_id == Category.id)
        .with_entities(Category.name, func.count(Ticket.id))
        .group_by(Category.name)
        .all()
    )
    raw_category = {cat: count for cat, count in category_counts}
    all_categories = [c.name for c in Category.query.all()]
    category_data = {c: raw_category.get(c, 0) for c in all_categories}

    # 5️⃣ Completed tickets in last 30 days (with filters applied)
    last_30_days = datetime.utcnow() - timedelta(days=30)
    completed_last_30 = query.filter(
        Ticket.status == "completed",
        Ticket.completed_at >= last_30_days
    ).count() or 0

    # 6️⃣ Total followups (apply clinic_id filter if passed)
    followup_query = TicketFollowUp.query
    if clinic_id:
        followup_query = followup_query.join(Ticket).filter(Ticket.clinic_id == clinic_id)
    followup_counts = followup_query.count() or 0

    # 7️⃣ Daily tickets count (last 7 days)
    today = date.today()
    raw_daily = dict(
        query.with_entities(func.date(Ticket.created_at), func.count(Ticket.id))
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

    # 8️⃣ Overdue tickets
    overdue_tickets = query.filter(
        and_(
            Ticket.status != "completed",
            Ticket.due_date < today
        )
    ).all()
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
        "by_priority": priority_data,
        "by_category": category_data,
        "completed_last_30_days": completed_last_30,
        "total_followups": followup_counts,
        "daily_ticket_stats": daily_stats,
        "overdue_tickets": overdue_list
    })
