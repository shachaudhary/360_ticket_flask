from flask import Blueprint, request, jsonify
from app import db
from app.model import Ticket, TicketNotification
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
# @validate_token
def get_notifications():
    receiver_id = request.args.get("user_id", type=int)  # 👈 param ab bhi user_id hi rahega
    page = request.args.get("page", 1, type=int)
    per_page = request.args.get("per_page", 10, type=int)

    if not receiver_id:
        return jsonify({"error": "user_id is required"}), 400

    # 🔹 Get receiver info
    receiver_info = get_user_info_by_id(receiver_id)
    if not receiver_info:
        return jsonify({"error": "Invalid user"}), 404


    # role = user_info.get("role", "").lower()

    # 🔹 Admin & Superadmin → all notifications (COMMENTED OUT)
    # if role in ["admin", "superadmin"]:
    #     query = TicketNotification.query
    # else:
    #     query = TicketNotification.query.filter_by(user_id=user_id)

    # 🔹 Ab sirf apne hi notifications show honge
    # 🔹 Sirf apni notifications
    query = TicketNotification.query.filter_by(receiver_id=receiver_id)

    # ✅ Pagination
    pagination = query.order_by(TicketNotification.created_at.desc()) \
        .paginate(page=page, per_page=per_page, error_out=False)

    notifications = pagination.items
    result = []

    for n in notifications:
        ticket = Ticket.query.get(n.ticket_id)

        sender_info = get_user_info_by_id(n.sender_id) if n.sender_id else None
        rec_info = get_user_info_by_id(n.receiver_id) if n.receiver_id else None

        result.append({
            "id": n.id,
            "ticket_id": n.ticket_id,
            "ticket_title": ticket.title if ticket else None,
            "notification_type": n.notification_type,
            "message": n.message,
            "created_at": n.created_at,
            "sender_info": sender_info,    # kisne bheja
            "receiver_info": rec_info      # kisko mila
        })

    return jsonify({
        # "receiver_info": receiver_info,   # logged-in user info
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
@require_api_key
# @validate_token
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
# @validate_token
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
