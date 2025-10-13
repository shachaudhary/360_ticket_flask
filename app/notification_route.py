from flask import Blueprint, request, jsonify
from app import db
from app.model import Ticket, TicketNotification, FormEmailLog
from app.utils.helper_function import get_user_info_by_id
from app.dashboard_routes import require_api_key, validate_token
from datetime import datetime




notification_bp = Blueprint("notifications", __name__, url_prefix="notifications")

# ───────────────────────────────
# Create Notification function
# ───────────────────────────────

def create_notification(ticket_id, receiver_id, sender_id, notification_type, message=None):
    """Create a new ticket notification"""
    notif = TicketNotification(
        ticket_id=ticket_id,
        receiver_id=receiver_id,
        sender_id=sender_id,
        notification_type=notification_type,
        message=message
    )
    db.session.add(notif)
    db.session.commit()
    return notif

# ───────────────────────────────
# Get Notifications for a User
# ───────────────────────────────


@notification_bp.route("/notifications", methods=["GET"])
@require_api_key
@validate_token
def get_notifications():
    receiver_id = request.args.get("user_id", type=int)
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 10, type=int)

    if not receiver_id:
        return jsonify({"error": "user_id is required"}), 400

    receiver_info = get_user_info_by_id(receiver_id)
    if not receiver_info:
        return jsonify({"error": "Invalid user"}), 404

    combined = []

    # ────────────── Ticket Notifications ──────────────
    tickets = TicketNotification.query.filter_by(receiver_id=receiver_id).all()
    for n in tickets:
        ticket = Ticket.query.get(n.ticket_id)
        sender_info = get_user_info_by_id(n.sender_id) if n.sender_id else None
        combined.append({
            "id": n.id,
            "source": "ticket",
            "title": ticket.title if ticket else None,
            "message": n.message,
            "notification_type": n.notification_type,
            "created_at": n.created_at,
            "sender_info": sender_info,
            "receiver_info": receiver_info
        })

    # ────────────── Form Email Logs (Fetch FormType from API) ──────────────
    forms = FormEmailLog.query.filter_by(receiver_id=receiver_id).all()
    for f in forms:
        sender_info = get_user_info_by_id(f.sender_id) if f.sender_id else None
        form_type_data = None

        # 🔹 Fetch FormType details from AUTH API
        try:
            resp = requests.get(f"{AUTH_API_BASE}/{f.form_type_id}", timeout=8)
            if resp.status_code == 200:
                api_data = resp.json()
                form_type_data = {
                    "id": api_data.get("id"),
                    "name": api_data.get("name"),
                    "display_name": api_data.get("display_name"),
                    "description": api_data.get("description"),
                    "assigned_users": api_data.get("users", [])
                }
            else:
                print(f"⚠️ Failed to fetch form_type {f.form_type_id}: {resp.status_code}")
        except Exception as e:
            print(f"⚠️ Error fetching form_type from AUTH API: {e}")

        combined.append({
            "id": f.id,
            "source": "form",
            "form_type": form_type_data,
            "email_type": f.email_type,
            "message": f.message,
            "status": f.status,
            "created_at": f.created_at,
            "sender_info": sender_info,
            "receiver_info": receiver_info
        })

    # ────────────── Sort & Paginate ──────────────
    combined.sort(key=lambda x: x["created_at"], reverse=True)
    total = len(combined)
    start = (page - 1) * per_page
    end = start + per_page
    paginated = combined[start:end]

    return jsonify({
        "notifications": paginated,
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total": total,
            "pages": (total + per_page - 1) // per_page
        }
    }), 200



# ───────────────────────────────
# Delete Single Notification (User only)
# ───────────────────────────────
@notification_bp.route("/notifications/<int:notification_id>", methods=["DELETE"])
@require_api_key
@validate_token
def delete_notification(notification_id):
    user_id = request.args.get("user_id", type=int)
    if not user_id:
        return jsonify({"error": "user_id is required"}), 400

    # Get user role
    user_info = get_user_info_by_id(user_id)
    if not user_info:
        return jsonify({"error": "Invalid user"}), 404
    role = user_info.get("role", "").lower()

    notif = TicketNotification.query.get(notification_id)
    if not notif:
        return jsonify({"error": "Notification not found"}), 404

    # 🔒 Only owner OR admin/superadmin can delete
    if role not in ["admin", "superadmin"] and notif.user_id != user_id:
        return jsonify({"error": "Not authorized to delete this notification"}), 403

    db.session.delete(notif)
    db.session.commit()

    return jsonify({"success": True, "message": f"Notification {notification_id} deleted"}), 200



# ───────────────────────────────
# Clear All Notifications (User only)
# ───────────────────────────────
@notification_bp.route("/notifications/clear", methods=["DELETE"])
@require_api_key
@validate_token
def clear_notifications():
    user_id = request.args.get("user_id", type=int)
    if not user_id:
        return jsonify({"error": "user_id is required"}), 400

    # ✅ Sirf user ke apne notifications clear honge
    deleted = TicketNotification.query.filter_by(user_id=user_id).delete()
    db.session.commit()

    return jsonify({
        "success": True,
        "deleted_count": deleted,
        "message": f"{deleted} notifications deleted for user {user_id}"
    }), 200
