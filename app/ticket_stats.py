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
    try:
        # 🔹 Query params
        timeframe = request.args.get("timeframe")
        clinic_id = request.args.get("clinic_id", type=int)
        category_id = request.args.get("category_id", type=int)
        start_date_param = request.args.get("start_date")
        end_date_param = request.args.get("end_date")

        # 🔹 Base query
        query = Ticket.query

        # 🔹 Filter by clinic_id
        if clinic_id:
            query = query.filter(Ticket.clinic_id == clinic_id)

        # 🔹 Filter by category_id
        if category_id:
            query = query.filter(Ticket.category_id == category_id)

        # 🔹 Filter by start_date and end_date (takes precedence over timeframe)
        if start_date_param or end_date_param:
            try:
                if start_date_param:
                    start_date_obj = datetime.strptime(start_date_param, "%Y-%m-%d").date()
                    query = query.filter(func.date(Ticket.created_at) >= start_date_obj)
                
                if end_date_param:
                    end_date_obj = datetime.strptime(end_date_param, "%Y-%m-%d").date()
                    query = query.filter(func.date(Ticket.created_at) <= end_date_obj)
            except ValueError as e:
                return jsonify({
                    "error": "Invalid date format",
                    "message": "Date must be in YYYY-MM-DD format",
                    "details": str(e)
                }), 400
        # 🔹 Filter by timeframe (only if start_date/end_date not provided)
        elif timeframe in TIMEFRAME_PRESETS:
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
        raw_status = {status: count for status, count in status_counts if status}
        # Normalize status values (case-insensitive exact matching)
        status_data = {
            "Completed": 0,
            "In Progress": 0,
            "Pending": 0
        }
        # Map actual status values to normalized keys using exact matching
        for status, count in raw_status.items():
            if not status:
                continue
            status_lower = status.lower().strip()
            # Exact matching to avoid false positives (e.g., "archived_not_completed")
            if status_lower == "completed":
                status_data["Completed"] += count
            elif status_lower in ["in progress", "in_progress"]:
                status_data["In Progress"] += count
            elif status_lower == "pending":
                status_data["Pending"] += count

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
        try:
            category_counts = (
                query.join(Category, Ticket.category_id == Category.id)
                .with_entities(Category.name, func.count(Ticket.id))
                .group_by(Category.name)
                .all()
            )
            raw_category = {cat: count for cat, count in category_counts if cat}
            all_categories = [c.name for c in Category.query.all() if c.name]
            category_data = {c: raw_category.get(c, 0) for c in all_categories}
        except Exception as e:
            # If join fails (e.g., no categories or tickets with null category_id), return empty
            category_data = {}

        # 5️⃣ Completed tickets in last 30 days (with filters applied)
        last_30_days = datetime.utcnow() - timedelta(days=30)
        completed_last_30 = query.filter(
            and_(
                func.lower(Ticket.status).in_(["completed"]),
                Ticket.completed_at >= last_30_days
            )
        ).count() or 0

        # 6️⃣ Average ticket resolution time (for completed tickets)
        completed_tickets = query.filter(
            and_(
                func.lower(Ticket.status).in_(["completed"]),
                Ticket.completed_at.isnot(None),
                Ticket.created_at.isnot(None)
            )
        ).all()
        
        avg_resolution_time_hours = 0
        if completed_tickets:
            # Calculate resolution time for each ticket (completed_at - created_at)
            resolution_times = [
                (t.completed_at - t.created_at).total_seconds()
                for t in completed_tickets
                if t.completed_at and t.created_at
            ]
            
            if resolution_times:
                total_seconds = sum(resolution_times)
                avg_seconds = total_seconds / len(resolution_times)
                avg_resolution_time_hours = round(avg_seconds / 3600, 2)

        # 7️⃣ Total followups (apply clinic_id, category_id and date filters if passed)
        try:
            followup_query = TicketFollowUp.query.join(Ticket)
            if clinic_id:
                followup_query = followup_query.filter(Ticket.clinic_id == clinic_id)
            
            if category_id:
                followup_query = followup_query.filter(Ticket.category_id == category_id)
            
            # Apply date filters to followups (based on ticket created_at)
            if start_date_param:
                try:
                    start_date_obj = datetime.strptime(start_date_param, "%Y-%m-%d").date()
                    followup_query = followup_query.filter(func.date(Ticket.created_at) >= start_date_obj)
                except ValueError:
                    pass
            if end_date_param:
                try:
                    end_date_obj = datetime.strptime(end_date_param, "%Y-%m-%d").date()
                    followup_query = followup_query.filter(func.date(Ticket.created_at) <= end_date_obj)
                except ValueError:
                    pass
            
            followup_counts = followup_query.count() or 0
        except Exception as e:
            followup_counts = 0

        # 8️⃣ Daily tickets count (respects clinic_id, category_id, timeframe, and date filters)
        today = date.today()
        # Create a fresh query for daily stats
        daily_query = Ticket.query
        
        # Apply clinic_id filter
        if clinic_id:
            daily_query = daily_query.filter(Ticket.clinic_id == clinic_id)
        
        # Apply category_id filter
        if category_id:
            daily_query = daily_query.filter(Ticket.category_id == category_id)
        
        # Determine date range for daily stats
        start_date_for_daily = today - timedelta(days=6)  # Default: last 7 days
        end_date_for_daily = today
        
        # Apply start_date and end_date filters (takes precedence)
        if start_date_param or end_date_param:
            try:
                if start_date_param:
                    start_date_obj = datetime.strptime(start_date_param, "%Y-%m-%d").date()
                    daily_query = daily_query.filter(func.date(Ticket.created_at) >= start_date_obj)
                    start_date_for_daily = start_date_obj
                
                if end_date_param:
                    end_date_obj = datetime.strptime(end_date_param, "%Y-%m-%d").date()
                    daily_query = daily_query.filter(func.date(Ticket.created_at) <= end_date_obj)
                    end_date_for_daily = end_date_obj
                elif start_date_param:
                    # If only start_date provided, show from start_date to today
                    end_date_for_daily = today
            except ValueError:
                pass  # Already handled above
        # Apply timeframe filter if it exists (only if start_date/end_date not provided)
        elif timeframe in TIMEFRAME_PRESETS:
            days = TIMEFRAME_PRESETS[timeframe]
            if timeframe == "today":
                # For today, only show today
                daily_query = daily_query.filter(func.date(Ticket.created_at) == today)
                start_date_for_daily = today
                end_date_for_daily = today
            elif timeframe == "yesterday":
                # For yesterday, only show yesterday
                yest = today - timedelta(days=1)
                daily_query = daily_query.filter(func.date(Ticket.created_at) == yest)
                start_date_for_daily = yest
                end_date_for_daily = yest
            else:
                # For last_7_days, last_14_days, etc.
                # Use date comparison to avoid timezone issues
                start_date_for_query = today - timedelta(days=days - 1)  # Include today
                daily_query = daily_query.filter(func.date(Ticket.created_at) >= start_date_for_query)
                start_date_for_daily = start_date_for_query
                end_date_for_daily = today
        
        # Ensure we're getting at least the date range we want to display
        daily_query = daily_query.filter(
            and_(
                func.date(Ticket.created_at) >= start_date_for_daily,
                func.date(Ticket.created_at) <= end_date_for_daily
            )
        )
        
        # Get daily counts grouped by date
        daily_results = daily_query.with_entities(
            func.date(Ticket.created_at),
            func.count(Ticket.id)
        ).group_by(func.date(Ticket.created_at)).all()
        
        # Convert to dict with date objects as keys
        raw_daily = {}
        for ticket_date, count in daily_results:
            if ticket_date:
                raw_daily[ticket_date] = count
        
        # Generate stats for the date range
        daily_stats = []
        current_date = start_date_for_daily
        while current_date <= end_date_for_daily:
            daily_stats.append({
                "date": str(current_date),
                "count": raw_daily.get(current_date, 0)
            })
            current_date += timedelta(days=1)

        # 9️⃣ Overdue tickets
        overdue_tickets = query.filter(
            and_(
                ~func.lower(Ticket.status).in_(["completed"]),
                Ticket.due_date.isnot(None),
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
            "avg_resolution_time_hours": avg_resolution_time_hours,
            "daily_ticket_stats": daily_stats,
            "overdue_tickets": overdue_list
        })
    except Exception as e:
        import traceback
        error_msg = str(e)
        traceback.print_exc()
        return jsonify({
            "error": "Failed to fetch ticket stats",
            "message": error_msg
        }), 500
