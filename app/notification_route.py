from flask import Blueprint, request, jsonify
from app import db
from app.model import Ticket, TicketNotification
from app.utils.helper_function import get_user_info_by_id
from app.dashboard_routes import require_api_key, validate_token




notification_bp = Blueprint("notifications", __name__, url_prefix="notifications")

# ───────────────────────────────
# Create Notification function
# ───────────────────────────────

def create_notification(ticket_id, user_id, notif_type, message=None):
    """
    Create a new notification entry
    """
    notif = TicketNotification(
        ticket_id=ticket_id,
        user_id=user_id,
        notification_type=notif_type,
        message=message
    )
    db.session.add(notif)
    db.session.commit()
    return notif


# ───────────────────────────────
# Get Notifications for a User
# ───────────────────────────────


@notification_bp.route("/notifications", methods=["GET"])
def get_notifications():
    user_id = request.args.get("user_id", type=int)
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 10, type=int)

    if not user_id:
        return jsonify({"error": "user_id is required"}), 400

    # 🔹 Get user info (for role check)
    user_info = get_user_info_by_id(user_id)
    if not user_info:
        return jsonify({"error": "Invalid user"}), 404

    role = user_info.get("role", "").lower()

    # 🔹 Admin & Superadmin → all notifications
    if role in ["admin", "superadmin"]:
        query = TicketNotification.query
    else:
        query = TicketNotification.query.filter_by(user_id=user_id)

    # ✅ Pagination Query
    pagination = query.order_by(TicketNotification.created_at.desc()) \
        .paginate(page=page, per_page=per_page, error_out=False)

    notifications = pagination.items

    result = []
    for n in notifications:
        ticket = Ticket.query.get(n.ticket_id)
        result.append({
            "id": n.id,
            "ticket_id": n.ticket_id,
            "ticket_title": ticket.title if ticket else None,
            "notification_type": n.notification_type,
            "message": n.message,
            "created_at": n.created_at
        })

    return jsonify({
        "notifications": result,
        "pagination": {
            "page": pagination.page,
            "per_page": pagination.per_page,
            "total": pagination.total,
            "pages": pagination.pages
        }
    }), 200

# ───────────────────────────────
# Delete Single Notification (User only)
# ───────────────────────────────
@notification_bp.route("/notifications/<int:notification_id>", methods=["DELETE"])
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
