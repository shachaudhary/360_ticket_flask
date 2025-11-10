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
    subject = f"Dental360 Ticket #{ticket.id}  You Were Tagged"

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
<head>
    <meta charset="UTF-8" />
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, 'Open Sans', 'Helvetica Neue', sans-serif; background:#f4f6f8; color:#333; margin:0; padding:0;">
    <div style="width:100%; padding:20px; box-sizing:border-box;">
        <div style="width:600px; max-width:100%; background:#ffffff; border-radius:8px; box-shadow:0 4px 12px rgba(0,0,0,0.08); margin:0 auto; overflow:hidden;">
            <div style="background:#202336; padding:20px; text-align:center; color:#fff; font-size:24px; font-weight:bold; display:flex; align-items:center; justify-content:center; gap:10px;">
                SUPPORT 360
            </div>
            <div style="padding:30px;">
                <p style="line-height:1.6; margin-bottom:15px;">Hello <strong>{tagged_user['username']}</strong>,</p>
                <p style="line-height:1.6; margin-bottom:15px;">You've been tagged in a ticket that requires your attention:</p>

                <table cellpadding="0" cellspacing="0" style="width:100%; border-collapse:collapse; margin-top:20px; margin-bottom:25px; border:1px solid #e0e0e0; border-radius:6px; overflow:hidden;">
                    <tr style="background:#f9f9fb;">
                        <td width="30%" style="padding:12px 15px; text-align:left; border-bottom:1px solid #eee; font-size:14px;"><strong>Ticket ID:</strong></td>
                        <td style="padding:12px 15px; text-align:left; border-bottom:1px solid #eee; font-size:14px;">{ticket.id}</td>
                    </tr>
                    <tr>
                        <td style="padding:12px 15px; text-align:left; border-bottom:1px solid #eee; font-size:14px;"><strong>Title:</strong></td>
                        <td style="padding:12px 15px; text-align:left; border-bottom:1px solid #eee; font-size:14px;">{ticket.title}</td>
                    </tr>
                    <tr style="background:#f9f9fb;">
                        <td style="padding:12px 15px; text-align:left; border-bottom:1px solid #eee; font-size:14px;"><strong>Tagged By:</strong></td>
                        <td style="padding:12px 15px; text-align:left; border-bottom:1px solid #eee; font-size:14px;">{assigner_name}</td>
                    </tr>
                    <tr>
                        <td style="padding:12px 15px; text-align:left; border-bottom:1px solid #eee; font-size:14px;"><strong>Comment:</strong></td>
                        <td style="padding:12px 15px; text-align:left; border-bottom:1px solid #eee; font-size:14px;">{comment.comment if comment else "—"}</td>
                    </tr>
                </table>

                <p style="line-height:1.6; margin-bottom:15px; margin-top:25px;">You can log in to the Support 360 Portal to review and respond.</p>
                <p style="text-align:center;">
                    <a href="https://support.dental360grp.com" style="display:inline-block; background-color:#7A3EF5; color:#ffffff; padding:12px 25px; border-radius:6px; text-decoration:none; font-weight:bold; font-size:16px; margin-top:20px; transition:background-color 0.3s ease;">
                        Support 360 Portal
                    </a>
                </p>

                <p style="line-height:1.6; margin-bottom:15px; margin-top:30px;">Best Regards,<br><strong>The Support 360 Team</strong></p>
            </div>
            <div style="background:#202336; padding:20px; text-align:center; font-size:12px; color:#b0b0b0; line-height:1.8;">
                © {datetime.now().year} Support 360 by Dental360. All rights reserved.<br>
                3435 W. Irving Park Rd, Chicago, IL<br>
                <a href="https://support.dental360grp.com/unsubscribe" style="color:#b0b0b0; text-decoration:underline;">Unsubscribe</a>
            </div>
        </div>
    </div>
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
<head>
    <meta charset="UTF-8" />
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, 'Open Sans', 'Helvetica Neue', sans-serif; background:#f4f6f8; color:#333; margin:0; padding:0;">
    <div style="width:100%; padding:20px; box-sizing:border-box;">
        <div style="width:600px; max-width:100%; background:#ffffff; border-radius:8px; box-shadow:0 4px 12px rgba(0,0,0,0.08); margin:0 auto; overflow:hidden;">
            <div style="background:#202336; padding:20px; text-align:center; color:#fff; font-size:24px; font-weight:bold; display:flex; align-items:center; justify-content:center; gap:10px;">
                SUPPORT 360 - Ticket Assigned
            </div>
            <div style="padding:30px;">
                <p style="line-height:1.6; margin-bottom:15px;">Hello <strong>{assignee_info['username']}</strong>,</p>
                <p style="line-height:1.6; margin-bottom:15px;">A new ticket has been assigned to you:</p>

                <table cellpadding="0" cellspacing="0" style="width:100%; border-collapse:collapse; margin-top:20px; margin-bottom:25px; border:1px solid #e0e0e0; border-radius:6px; overflow:hidden;">
                    <tr style="background:#f9f9fb;">
                        <td width="30%" style="padding:12px 15px; text-align:left; border-bottom:1px solid #eee; font-size:14px;"><strong>Ticket ID:</strong></td>
                        <td style="padding:12px 15px; text-align:left; border-bottom:1px solid #eee; font-size:14px;">{ticket.id}</td>
                    </tr>
                    <tr>
                        <td style="padding:12px 15px; text-align:left; border-bottom:1px solid #eee; font-size:14px;"><strong>Title:</strong></td>
                        <td style="padding:12px 15px; text-align:left; border-bottom:1px solid #eee; font-size:14px;">{ticket.title}</td>
                    </tr>
                    <tr style="background:#f9f9fb;">
                        <td style="padding:12px 15px; text-align:left; border-bottom:1px solid #eee; font-size:14px;"><strong>Assigned By:</strong></td>
                        <td style="padding:12px 15px; text-align:left; border-bottom:1px solid #eee; font-size:14px;">{assigner_name}</td>
                    </tr>
                    <tr>
                        <td style="padding:12px 15px; text-align:left; border-bottom:1px solid #eee; font-size:14px;"><strong>Priority:</strong></td>
                        <td style="padding:12px 15px; text-align:left; border-bottom:1px solid #eee; font-size:14px;">{ticket.priority or "Not set"}</td>
                    </tr>
                </table>

                <p style="line-height:1.6; margin-bottom:15px; margin-top:25px;">You can log in to the Support 360 Portal to review and take action.</p>
                <p style="text-align:center;">
                    <a href="https://support.dental360grp.com" style="display:inline-block; background-color:#7A3EF5; color:#ffffff; padding:12px 25px; border-radius:6px; text-decoration:none; font-weight:bold; font-size:16px; margin-top:20px; transition:background-color 0.3s ease;">
                        Support 360 Portal
                    </a>
                </p>

                <p style="line-height:1.6; margin-bottom:15px; margin-top:30px;">Best Regards,<br><strong>The Support 360 Team</strong></p>
            </div>
            <div style="background:#202336; padding:20px; text-align:center; font-size:12px; color:#b0b0b0; line-height:1.8;">
                © {datetime.now().year} Support 360 by Dental360. All rights reserved.<br>
                3435 W. Irving Park Rd, Chicago, IL<br>
                <a href="https://support.dental360grp.com/unsubscribe" style="color:#b0b0b0; text-decoration:underline;">Unsubscribe</a>
            </div>
        </div>
    </div>
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
<head>
    <meta charset="UTF-8" />
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, 'Open Sans', 'Helvetica Neue', sans-serif; background:#f4f6f8; color:#333; margin:0; padding:0;">
    <div style="width:100%; padding:20px; box-sizing:border-box;">
        <div style="width:600px; max-width:100%; background:#ffffff; border-radius:8px; box-shadow:0 4px 12px rgba(0,0,0,0.08); margin:0 auto; overflow:hidden;">
            <div style="background:#202336; padding:20px; text-align:center; color:#fff; font-size:24px; font-weight:bold; display:flex; align-items:center; justify-content:center; gap:10px;">
                SUPPORT 360 - Ticket Update
            </div>
            <div style="padding:30px;">
                <p style="line-height:1.6; margin-bottom:15px;">Hello <strong>{user_info['username']}</strong>,</p>
                <p style="line-height:1.6; margin-bottom:15px;">Ticket <strong>#{ticket.id}</strong> (<em>{ticket.title}</em>) was {action_type} by {actor_name}.</p>

                <table cellpadding="0" cellspacing="0" style="width:100%; border-collapse:collapse; margin-top:20px; margin-bottom:25px; border:1px solid #e0e0e0; border-radius:6px; overflow:hidden;">
                    <tr style="background:#f9f9fb;">
                        <td width="30%" style="padding:12px 15px; text-align:left; border-bottom:1px solid #eee; font-size:14px;"><strong>Ticket ID:</strong></td>
                        <td style="padding:12px 15px; text-align:left; border-bottom:1px solid #eee; font-size:14px;">{ticket.id}</td>
                    </tr>
                    <tr>
                        <td style="padding:12px 15px; text-align:left; border-bottom:1px solid #eee; font-size:14px;"><strong>Title:</strong></td>
                        <td style="padding:12px 15px; text-align:left; border-bottom:1px solid #eee; font-size:14px;">{ticket.title}</td>
                    </tr>
                    <tr style="background:#f9f9fb;">
                        <td style="padding:12px 15px; text-align:left; border-bottom:1px solid #eee; font-size:14px;"><strong>Status:</strong></td>
                        <td style="padding:12px 15px; text-align:left; border-bottom:1px solid #eee; font-size:14px;">{ticket.status}</td>
                    </tr>
                    <tr>
                        <td style="padding:12px 15px; text-align:left; border-bottom:1px solid #eee; font-size:14px;"><strong>Priority:</strong></td>
                        <td style="padding:12px 15px; text-align:left; border-bottom:1px solid #eee; font-size:14px;">{ticket.priority or "Not set"}</td>
                    </tr>
                    <tr style="background:#f9f9fb;">
                        <td style="padding:12px 15px; text-align:left; border-bottom:1px solid #eee; font-size:14px;"><strong>Updated By:</strong></td>
                        <td style="padding:12px 15px; text-align:left; border-bottom:1px solid #eee; font-size:14px;">{actor_name}</td>
                    </tr>
                </table>

                <p style="line-height:1.6; margin-bottom:15px; margin-top:25px;">You can log in to the Support 360 Portal to view the update.</p>
                <p style="text-align:center;">
                    <a href="https://support.dental360grp.com" style="display:inline-block; background-color:#7A3EF5; color:#ffffff; padding:12px 25px; border-radius:6px; text-decoration:none; font-weight:bold; font-size:16px; margin-top:20px; transition:background-color 0.3s ease;">
                        Support 360 Portal
                    </a>
                </p>

                <p style="line-height:1.6; margin-bottom:15px; margin-top:30px;">Best Regards,<br><strong>The Support 360 Team</strong></p>
            </div>
            <div style="background:#202336; padding:20px; text-align:center; font-size:12px; color:#b0b0b0; line-height:1.8;">
                © {datetime.now().year} Support 360 by Dental360. All rights reserved.<br>
                3435 W. Irving Park Rd, Chicago, IL<br>
                <a href="https://support.dental360grp.com/unsubscribe" style="color:#b0b0b0; text-decoration:underline;">Unsubscribe</a>
            </div>
        </div>
    </div>
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
<head>
    <meta charset="UTF-8" />
</head>
<body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, 'Open Sans', 'Helvetica Neue', sans-serif; background:#f4f6f8; color:#333; margin:0; padding:0;">
    <div style="width:100%; padding:20px; box-sizing:border-box;">
        <div style="width:600px; max-width:100%; background:#ffffff; border-radius:8px; box-shadow:0 4px 12px rgba(0,0,0,0.08); margin:0 auto; overflow:hidden;">
            <div style="background:#202336; padding:20px; text-align:center; color:#fff; font-size:24px; font-weight:bold; display:flex; align-items:center; justify-content:center; gap:10px;">
                SUPPORT 360 - Ticket Followed Update
            </div>
            <div style="padding:30px;">
                <p style="line-height:1.6; margin-bottom:15px;">Hello <strong>{user_info['username']}</strong>,</p>
                <p style="line-height:1.6; margin-bottom:15px;">A ticket you follow (<strong>#{ticket.id}</strong> - <em>{ticket.title}</em>) has been updated by {updater_name}.</p>

                <table cellpadding="0" cellspacing="0" style="width:100%; border-collapse:collapse; margin-top:20px; margin-bottom:25px; border:1px solid #e0e0e0; border-radius:6px; overflow:hidden;">
                    <tr style="background:#f9f9fb;">
                        <td width="30%" style="padding:12px 15px; text-align:left; border-bottom:1px solid #eee; font-size:14px;"><strong>Ticket ID:</strong></td>
                        <td style="padding:12px 15px; text-align:left; border-bottom:1px solid #eee; font-size:14px;">{ticket.id}</td>
                    </tr>
                    <tr>
                        <td style="padding:12px 15px; text-align:left; border-bottom:1px solid #eee; font-size:14px;"><strong>Title:</strong></td>
                        <td style="padding:12px 15px; text-align:left; border-bottom:1px solid #eee; font-size:14px;">{ticket.title}</td>
                    </tr>
                    <tr style="background:#f9f9fb;">
                        <td style="padding:12px 15px; text-align:left; border-bottom:1px solid #eee; font-size:14px;"><strong>Updated By:</strong></td>
                        <td style="padding:12px 15px; text-align:left; border-bottom:1px solid #eee; font-size:14px;">{updater_name}</td>
                    </tr>
                </table>

                <p style="line-height:1.6; margin-bottom:15px; margin-top:25px;">You can log in to the Support 360 Portal to review and respond.</p>
                <p style="text-align:center;">
                    <a href="https://support.dental360grp.com" style="display:inline-block; background-color:#7A3EF5; color:#ffffff; padding:12px 25px; border-radius:6px; text-decoration:none; font-weight:bold; font-size:16px; margin-top:20px; transition:background-color 0.3s ease;">
                        Support 360 Portal
                    </a>
                </p>

                <p style="line-height:1.6; margin-bottom:15px; margin-top:30px;">Best Regards,<br><strong>The Support 360 Team</strong></p>
            </div>
            <div style="background:#202336; padding:20px; text-align:center; font-size:12px; color:#b0b0b0; line-height:1.8;">
                © {datetime.now().year} Support 360 by Dental360. All rights reserved.<br>
                3435 W. Irving Park Rd, Chicago, IL<br>
                <a href="https://support.dental360grp.com/unsubscribe" style="color:#b0b0b0; text-decoration:underline;">Unsubscribe</a>
            </div>
        </div>
    </div>
</body>
</html>
"""

    # ✅ Async send
    print(f"📧 Sending update email → {user_info['email']} | Ticket #{ticket.id}")
    threading.Thread(
        target=send_email,
        args=(user_info["email"], subject, body_html, body_text)
    ).start()

import threading


# ===========================
# ✅ EMAIL UTILITIES
# ===========================

def generate_email_template(title, body_lines):
    """
    Create a clean, minimal HTML card-style email.
    """
    date_str = datetime.utcnow().strftime("%B %d, %Y")

    body_html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
      <meta charset="UTF-8" />
      <title>{title}</title>
    </head>
    <body style="font-family: Arial, sans-serif; background-color: #f5f7fa; padding: 40px;">
      <div style="max-width: 600px; margin: 0 auto; background: #ffffff; border-radius: 10px;
                  box-shadow: 0 2px 8px rgba(0,0,0,0.08); overflow: hidden;">
        <div style="background: linear-gradient(135deg, #004AAD, #2BCB6B);
                    color: white; padding: 16px 24px; font-size: 18px; font-weight: bold;">
          {title}
        </div>

        <div style="padding: 24px; color: #333;">
          <p style="margin: 0 0 10px; color: #555;">{date_str}</p>
    """

    for line in body_lines:
        body_html += f"<p style='margin: 10px 0; line-height: 1.6; color: #444;'>{line}</p>"

    body_html += """
          <hr style="border: none; border-top: 1px solid #eaeaea; margin: 20px 0;" />
          <p style="font-size: 13px; color: #999;">This is an automated email from Dental360. 
          Please do not reply directly.</p>
        </div>
      </div>
    </body>
    </html>
    """
    return body_html

