from datetime import datetime
import asyncio, sys, threading
from app.utils.helper_function import upload_to_s3, send_email, get_user_info_by_id

# ─── Windows Fix for asyncio ─────────────────────────────────────────────
if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# ─── Config ──────────────────────────────────────────────
def run_async_email(coro):
    """Run async function in a separate thread safely (Windows friendly)"""
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(coro)
    except Exception as e:
        print(f"❌ Email loop error: {e}")
    finally:
        if not loop.is_closed():
            loop.close()


def send_tag_email(ticket, tagged_user, assigner_info, comment=None):
    """Send email notification when a user is tagged in a ticket."""
    if not tagged_user or not tagged_user.get("email"):
        print("⚠️ Tagged user has no email, skipping notification")
        return

    assigner_name = assigner_info["username"] if assigner_info else "System"
    subject = f"Dental360 Ticket #{ticket.id} - You Were Tagged"

    # Plain text fallback
    body_text = (
        f"Hello {tagged_user['username']},\n\n"
        f"You were tagged in a ticket.\n\n"
        f"Ticket ID: {ticket.id}\n"
        f"Title: {ticket.title}\n"
        f"Tagged By: {assigner_name}\n"
        f"Comment: {comment.comment if comment else '-'}\n\n"
        f"Please log in to the Dental360 system to review and take action.\n\n"
        f"Best Regards,\nDental360 Support Team"
    )

    # HTML template
    body_html = f"""
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8" /></head>
<body style="font-family: Arial, sans-serif; background:#f4f6f8; color:#333;">
    <table width="100%" cellpadding="0" cellspacing="0" style="padding:20px;">
    <tr>
        <td align="center">
        <table width="600" style="background:#ffffff; border-radius:8px; box-shadow:0 2px 6px rgba(0,0,0,0.1);">
            <tr>
                <td style="background:#0d6efd; padding:20px; text-align:center; color:#fff; font-size:20px; font-weight:bold;">
                    Dental360 Support
                </td>
            </tr>
            <tr>
                <td style="padding:25px;">
                    <p>Hello <strong>{tagged_user['username']}</strong>,</p>
                    <p>You have been tagged in the following ticket:</p>
                    <table width="100%" cellpadding="8" cellspacing="0" style="border:1px solid #e0e0e0; border-radius:6px;">
                        <tr style="background:#f9f9f9;">
                            <td width="30%"><strong>Ticket ID</strong></td>
                            <td>{ticket.id}</td>
                        </tr>
                        <tr><td><strong>Title</strong></td><td>{ticket.title}</td></tr>
                        <tr style="background:#f9f9f9;">
                            <td><strong>Tagged By</strong></td>
                            <td>{assigner_name}</td>
                        </tr>
                        <tr><td><strong>Comment</strong></td><td>{comment.comment if comment else "—"}</td></tr>
                    </table>
                    <p style="margin:20px 0;">You can log in to the 
                    <a href="https://dental360grp.com" style="color:#0d6efd;">Dental360 portal</a> to review and respond.</p>
                    <p>Best Regards,<br><strong>Dental360 Support Team</strong></p>
                </td>
            </tr>
            <tr>
                <td style="background:#f4f6f8; padding:15px; text-align:center; font-size:12px; color:#888;">
                    © {datetime.now().year} Dental360. All rights reserved.<br>
                    3435 W. Irving Park Rd, Chicago, IL<br>
                    <a href="https://dental360grp.com/unsubscribe" style="color:#888;">Unsubscribe</a>
                </td>
            </tr>
        </table>
        </td>
    </tr>
    </table>
</body>
</html>
"""

    # Async send
    print(f"📧 Sending tag email → {tagged_user['email']} | Ticket #{ticket.id}")
    threading.Thread(
    target=send_email,
    args=(tagged_user["email"], subject, body_html, body_text)
).start()


def send_assign_email(ticket, assignee_info, assigner_info):
    """Send email notification when a ticket is assigned to a user."""
    if not assignee_info or not assignee_info.get("email"):
        print("⚠️ Assignee has no email, skipping notification")
        return

    assigner_name = assigner_info["username"] if assigner_info else "System"
    subject = f"Dental360 New Ticket Assigned: {ticket.title}"

    # Plain text fallback
    body_text = (
        f"Hello {assignee_info['username']},\n\n"
        f"A new ticket has been assigned to you.\n\n"
        f"Ticket ID: {ticket.id}\n"
        f"Title: {ticket.title}\n"
        f"Assigned By: {assigner_name}\n"
        f"Priority: {ticket.priority or 'Not set'}\n\n"
        f"Please log in to the Dental360 system to review and take action.\n\n"
        f"Best Regards,\nDental360 Support Team"
    )

    # HTML template (green header for assignment)
    body_html = f"""
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8" /></head>
<body style="font-family: Arial, sans-serif; background:#f4f6f8; color:#333;">
    <table width="100%" cellpadding="0" cellspacing="0" style="padding:20px;">
    <tr>
        <td align="center">
        <table width="600" style="background:#ffffff; border-radius:8px; box-shadow:0 2px 6px rgba(0,0,0,0.1);">
            <tr>
                <td style="background:#198754; padding:20px; text-align:center; color:#fff; font-size:20px; font-weight:bold;">
                    Dental360 Support
                </td>
            </tr>
            <tr>
                <td style="padding:25px;">
                    <p>Hello <strong>{assignee_info['username']}</strong>,</p>
                    <p>A new ticket has been assigned to you:</p>
                    <table width="100%" cellpadding="8" cellspacing="0" style="border:1px solid #e0e0e0; border-radius:6px;">
                        <tr style="background:#f9f9f9;">
                            <td width="30%"><strong>Ticket ID</strong></td>
                            <td>{ticket.id}</td>
                        </tr>
                        <tr><td><strong>Title</strong></td><td>{ticket.title}</td></tr>
                        <tr style="background:#f9f9f9;">
                            <td><strong>Assigned By</strong></td>
                            <td>{assigner_name}</td>
                        </tr>
                        <tr><td><strong>Priority</strong></td><td>{ticket.priority or "Not set"}</td></tr>
                    </table>
                    <p style="margin:20px 0;">You can log in to the 
                    <a href="https://dental360grp.com" style="color:#198754;">Dental360 portal</a> to review and take action.</p>
                    <p>Best Regards,<br><strong>Dental360 Support Team</strong></p>
                </td>
            </tr>
            <tr>
                <td style="background:#f4f6f8; padding:15px; text-align:center; font-size:12px; color:#888;">
                    © {datetime.now().year} Dental360. All rights reserved.<br>
                    3435 W. Irving Park Rd, Chicago, IL<br>
                    <a href="https://dental360grp.com/unsubscribe" style="color:#888;">Unsubscribe</a>
                </td>
            </tr>
        </table>
        </td>
    </tr>
    </table>
</body>
</html>
"""

    print(f"📧 Sending assignment email → {assignee_info['email']} | Ticket #{ticket.id}")
    threading.Thread(
        target=send_email,
        args=(assignee_info["email"], subject, body_html, body_text)
    ).start()


def send_follow_email(ticket, user_info, action_by=None, action_type="updated"):
    """Send email notification to follow-up users when a ticket is updated or changed."""

    if not user_info or not user_info.get("email"):
        print("⚠️ Follow-up user has no email, skipping notification")
        return

    actor_name = action_by["username"] if action_by else "System"
    subject = f"Dental360 Ticket #{ticket.id} {action_type.capitalize()}"

    # Plain text fallback
    body_text = (
        f"Hello {user_info['username']},\n\n"
        f"Ticket #{ticket.id} ('{ticket.title}') was {action_type} by {actor_name}.\n\n"
        f"Priority: {ticket.priority or 'Not set'}\n"
        f"Status: {ticket.status}\n\n"
        f"Please log in to Dental360 to review the update.\n\n"
        f"Best Regards,\nDental360 Support Team"
    )

    # HTML email (blue header for updates)
    body_html = f"""
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8" /></head>
<body style="font-family: Arial, sans-serif; background:#f4f6f8; color:#333;">
    <table width="100%" cellpadding="0" cellspacing="0" style="padding:20px;">
    <tr>
        <td align="center">
        <table width="600" style="background:#ffffff; border-radius:8px; box-shadow:0 2px 6px rgba(0,0,0,0.1);">
            <tr>
                <td style="background:#0d6efd; padding:20px; text-align:center; color:#fff; font-size:20px; font-weight:bold;">
                    Dental360 Support - Ticket Update
                </td>
            </tr>
            <tr>
                <td style="padding:25px;">
                    <p>Hello <strong>{user_info['username']}</strong>,</p>
                    <p>Ticket <strong>#{ticket.id}</strong> (<em>{ticket.title}</em>) was {action_type} by {actor_name}.</p>
                    <table width="100%" cellpadding="8" cellspacing="0" style="border:1px solid #e0e0e0; border-radius:6px;">
                        <tr style="background:#f9f9f9;">
                            <td width="30%"><strong>Ticket ID</strong></td>
                            <td>{ticket.id}</td>
                        </tr>
                        <tr><td><strong>Title</strong></td><td>{ticket.title}</td></tr>
                        <tr style="background:#f9f9f9;">
                            <td><strong>Status</strong></td>
                            <td>{ticket.status}</td>
                        </tr>
                        <tr><td><strong>Priority</strong></td><td>{ticket.priority or "Not set"}</td></tr>
                        <tr style="background:#f9f9f9;">
                            <td><strong>Updated By</strong></td>
                            <td>{actor_name}</td>
                        </tr>
                    </table>
                    <p style="margin:20px 0;">You can log in to the 
                    <a href="https://dental360grp.com" style="color:#0d6efd;">Dental360 portal</a> to view the update.</p>
                    <p>Best Regards,<br><strong>Dental360 Support Team</strong></p>
                </td>
            </tr>
            <tr>
                <td style="background:#f4f6f8; padding:15px; text-align:center; font-size:12px; color:#888;">
                    © {datetime.now().year} Dental360. All rights reserved.<br>
                    3435 W. Irving Park Rd, Chicago, IL<br>
                    <a href="https://dental360grp.com/unsubscribe" style="color:#888;">Unsubscribe</a>
                </td>
            </tr>
        </table>
        </td>
    </tr>
    </table>
</body>
</html>
"""

    print(f"📧 Sending follow-up email → {user_info['email']} | Ticket #{ticket.id} ({action_type})")
    threading.Thread(
        target=send_email,
        args=(user_info["email"], subject, body_html, body_text)
    ).start()




def send_update_ticket_email(ticket, user_info, updater_info, changes):
    """
    Send email notification when a ticket is updated.
    Changes = list of tuples like: [("status", "Pending", "Completed"), ("priority", "Low", "High")]
    """
    if not user_info or not user_info.get("email"):
        print("⚠️ No valid email for user, skipping update notification")
        return

    updater_name = updater_info["username"] if updater_info else "System"
    subject = f"Dental360 Ticket #{ticket.id} - Updated"

    # ✅ Changes ko readable bana do
    changes_text = "\n".join([f"{field}: {old}  {new}" for field, old, new in changes]) or "—"
    changes_html = "".join([
        f"<tr><td><strong>{field}</strong></td><td>{old}  {new}</td></tr>"
        for field, old, new in changes
    ]) or "<tr><td colspan='2'>—</td></tr>"

    # Plain text fallback
    body_text = (
        f"Dental360 Support\n\n"
        f"Hello {user_info['username']},\n\n"
        f"A ticket you follow has been updated:\n\n"
        f"Ticket ID: {ticket.id}\n"
        f"Title: {ticket.title}\n"
        f"Updated By: {updater_name}\n"
        f"{changes_text}\n\n"
        f"You can log in to the Dental360 portal to review the ticket.\n\n"
        f"Best Regards,\nDental360 Support Team"
    )

    # HTML template
    body_html = f"""
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8" /></head>
<body style="font-family: Arial, sans-serif; background:#f4f6f8; color:#333;">
    <table width="100%" cellpadding="0" cellspacing="0" style="padding:20px;">
    <tr>
        <td align="center">
        <table width="600" style="background:#ffffff; border-radius:8px; box-shadow:0 2px 6px rgba(0,0,0,0.1);">
            <tr>
                <td style="background:#0d6efd; padding:20px; text-align:center; color:#fff; font-size:20px; font-weight:bold;">
                    Dental360 Support
                </td>
            </tr>
            <tr>
                <td style="padding:25px;">
                    <p>Hello <strong>{user_info['username']}</strong>,</p>
                    <p>A ticket you follow has been updated:</p>
                    <table width="100%" cellpadding="8" cellspacing="0" style="border:1px solid #e0e0e0; border-radius:6px;">
                        <tr style="background:#f9f9f9;">
                            <td width="30%"><strong>Ticket ID</strong></td>
                            <td>{ticket.id}</td>
                        </tr>
                        <tr><td><strong>Title</strong></td><td>{ticket.title}</td></tr>
                        <tr style="background:#f9f9f9;">
                            <td><strong>Updated By</strong></td>
                            <td>{updater_name}</td>
                        </tr>
                        {changes_html}
                    </table>
                    <p style="margin:20px 0;">You can log in to the 
                    <a href="https://dental360grp.com" style="color:#0d6efd;">Dental360 portal</a> 
                    to review and respond.</p>
                    <p>Best Regards,<br><strong>Dental360 Support Team</strong></p>
                </td>
            </tr>
            <tr>
                <td style="background:#f4f6f8; padding:15px; text-align:center; font-size:12px; color:#888;">
                    © {datetime.now().year} Dental360. All rights reserved.<br>
                    3435 W. Irving Park Rd, Chicago, IL<br>
                    <a href="https://dental360grp.com/unsubscribe" style="color:#888;">Unsubscribe</a>
                </td>
            </tr>
        </table>
        </td>
    </tr>
    </table>
</body>
</html>
"""

    # ✅ Async send
    print(f"📧 Sending update email → {user_info['email']} | Ticket #{ticket.id}")
    threading.Thread(
        target=send_email,
        args=(user_info["email"], subject, body_html, body_text)
    ).start()

