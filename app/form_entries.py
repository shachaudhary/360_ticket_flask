from flask import Blueprint, request, jsonify
from datetime import datetime
import os
import requests

from app import db
from app.model import (
    FormEntry,
    FormFieldValue,
    FormEmailRecipient,
    FormTypeUserNoti,
    FormEmailLog
)
from app.utils.email_templete import send_email, get_user_info_by_id, generate_email_template
from app.dashboard_routes import require_api_key, validate_token


# 🔹 Blueprint
form_entries_blueprint = Blueprint("form_entries", __name__)


MAILGUN_API_URL = os.getenv("MAILGUN_API_URL")
MAILGUN_API_KEY = os.getenv("MAILGUN_API_KEY")
AUTH_API_BASE = "https://api.dental360grp.com/api/form_types"

# ================================================================
# 🟢 CREATE FORM ENTRY + AUTO EMAIL TO MAPPED USERS
# ================================================================
@form_entries_blueprint.route("/form_entries/field_values", methods=["POST"])
def create_form_entry_with_field_values():
    """
    Create a new FormEntry using form_type_id,
    save field values, and send email notifications
    to all users assigned via the Auth backend API.
    """
    try:
        data = request.get_json() or {}

        form_type_id = data.get("form_type_id")
        submitted_by_id = data.get("submitted_by_id")
        clinic_id = data.get("clinic_id")
        location_id = data.get("location_id")
        field_values = data.get("field_values", [])

        if not form_type_id:
            return jsonify({"error": "form_type_id is required"}), 400
        if not field_values:
            return jsonify({"error": "No field values provided"}), 400

        # ===========================================================
        # ✅ Validate FormType — only via external API
        # ===========================================================
        ft = None
        try:
            resp = requests.get(AUTH_API_BASE, timeout=8)
            if resp.status_code == 200:
                ft = resp.json()
            else:
                print(f"⚠️ External API form_type fetch failed: {resp.status_code}")
        except Exception as e:
            print(f"⚠️ Error fetching form_type from API: {e}")

        if not ft:
            return jsonify({"error": "Invalid form_type_id"}), 404

        # ✅ Create FormEntry
        new_entry = FormEntry(
            form_type_id=form_type_id,
            submitted_by_id=submitted_by_id,
            clinic_id=clinic_id,
            location_id=location_id,
        )
        db.session.add(new_entry)
        db.session.commit()

        # ✅ Add field values
        for field in field_values:
            if field.get("field_name"):
                db.session.add(FormFieldValue(
                    form_entry_id=new_entry.id,
                    field_name=field["field_name"],
                    field_value=field.get("field_value", "")
                ))
        db.session.commit()

        # ✅ Fetch assigned users from Auth backend API
        assigned_users = []
        try:
            url = f"{AUTH_API_BASE}/{form_type_id}"
            resp = requests.get(url, timeout=8)
            if resp.status_code == 200:
                data = resp.json()
                assigned_users = data.get("users", [])
        except Exception as api_err:
            print(f"❌ Error fetching assigned users: {api_err}")

        # ✅ If no users found
        if not assigned_users:
            return jsonify({
                "message": "Form entry created successfully but no assigned users found.",
                "form_entry_id": new_entry.id
            }), 201

        # ✅ Prepare and send notification emails
        ft_name = ft.get("name", "Form")
        subject = f"New {ft_name.replace('_', ' ').title()} Form Submitted"
        body_lines = [
            f"A new <b>{ft_name.replace('_', ' ').title()}</b> form has been submitted on the Dental360 portal.",
            "<br>Please review the form in your Dental360 dashboard."
        ]
        body_html = generate_email_template(subject, body_lines)

        email_status = []
        for user in assigned_users:
            email = user.get("email")
            if not email:
                continue

            sent = send_email(email, subject, body_html)
            status = "sent" if sent else "failed"

            # ✅ Log email
            db.session.add(FormEmailLog(
                form_entry_id=new_entry.id,
                form_type_id=form_type_id,
                sender_id=submitted_by_id,
                email_type="form_submission",
                sender_email=email,
                receiver_id=user.get("id"),
                message=f"Form submitted for {ft_name}",
                status=status
            ))
            email_status.append({"email": email, "status": status})

        db.session.commit()

        return jsonify({
            "message": "Form entry created successfully and notifications sent.",
            "form_entry_id": new_entry.id,
            "form_type_id": form_type_id,
            "email_status": email_status
        }), 201

    except Exception as e:
        db.session.rollback()
        print(f"❌ Error in create_form_entry_with_field_values: {e}")
        return jsonify({"error": str(e)}), 500




# ================================================================
# 🟠 UPDATE FORM ENTRY + EMAIL NOTIFY MAPPED USERS
# ================================================================
@form_entries_blueprint.route("/form_entries/field_values/<int:form_entry_id>", methods=["PATCH"])
def patch_form_entry_field_values(form_entry_id):
    """
    Partially update a FormEntry using form_type_id (if provided),
    update only the provided fields, and send email notifications
    to all assigned users fetched via the Auth backend API.
    """
    try:
        data = request.get_json() or {}

        form_type_id = data.get("form_type_id")
        submitted_by_id = data.get("submitted_by_id")
        clinic_id = data.get("clinic_id")
        location_id = data.get("location_id")
        field_values = data.get("field_values", [])

        # ✅ Fetch entry
        form_entry = FormEntry.query.get(form_entry_id)
        if not form_entry:
            return jsonify({"error": "Form entry not found"}), 404

        # ===========================================================
        # ✅ Single external API call — fetch form type + assigned users
        # ===========================================================
        ft = None
        assigned_users = []
        try:
            resp = requests.get(f"{AUTH_API_BASE}/{form_type_id or form_entry.form_type_id}", timeout=8)
            if resp.status_code == 200:
                ft = resp.json()
                assigned_users = ft.get("users", [])  # ✅ use same response for both
            else:
                print(f"⚠️ External API form_type fetch failed: {resp.status_code}")
        except Exception as e:
            print(f"⚠️ Error fetching form_type from API: {e}")

        if not ft:
            return jsonify({"error": "Invalid form_type_id"}), 404

        # ✅ Update only provided fields
        if form_type_id:
            form_entry.form_type_id = form_type_id
        if submitted_by_id:
            form_entry.submitted_by_id = submitted_by_id
        if clinic_id:
            form_entry.clinic_id = clinic_id
        if location_id:
            form_entry.location_id = location_id

        db.session.commit()

        # ✅ Update or add field values (if provided)
        if field_values:
            for field in field_values:
                field_name = field.get("field_name")
                if not field_name:
                    continue

                existing_field = FormFieldValue.query.filter_by(
                    form_entry_id=form_entry.id, field_name=field_name
                ).first()

                if existing_field:
                    existing_field.field_value = field.get("field_value", "")
                else:
                    db.session.add(FormFieldValue(
                        form_entry_id=form_entry.id,
                        field_name=field_name,
                        field_value=field.get("field_value", "")
                    ))
            db.session.commit()

        # ✅ Compose update email
        ft_name = ft.get("name", "Form")
        subject = f"{ft_name.replace('_', ' ').title()} Form Updated"
        body_lines = [
            f"The <b>{ft_name.replace('_', ' ').title()}</b> form (ID: {form_entry.id}) has been updated.",
            "<br>Please review the changes in your Dental360 dashboard."
        ]
        body_html = generate_email_template(subject, body_lines)

        # ✅ Send notification emails
        email_status = []
        for user in assigned_users:
            email = user.get("email")
            if not email:
                continue

            sent = send_email(email, subject, body_html)
            status = "sent" if sent else "failed"

            db.session.add(FormEmailLog(
                form_entry_id=form_entry.id,
                form_type_id=form_entry.form_type_id,
                sender_id=submitted_by_id,
                email_type="form_update",
                sender_email=email,
                receiver_id=user.get("id"),
                message=f"Form update notification sent for {ft_name}",
                status=status
            ))
            email_status.append({"email": email, "status": status})

        db.session.commit()

        return jsonify({
            "message": "Form entry partially updated successfully and notifications sent.",
            "form_entry_id": form_entry.id,
            "form_type_id": form_entry.form_type_id,
            "form_type_name": ft_name,
            "email_status": email_status
        }), 200

    except Exception as e:
        db.session.rollback()
        print(f"❌ Error in patch_form_entry_field_values: {e}")
        return jsonify({"error": str(e)}), 500

@form_entries_blueprint.route("/form_entries/by_form_type/<int:form_type_id>", methods=["GET"])
def get_form_entries_by_form_type(form_type_id):
    """
    Fetch paginated FormEntry records for a given form_type_id.
    Includes:
    - submitter info
    - assigned users (from Auth backend API)
    - form type details (from Auth backend)
    - search filter by submitter name, email, or field value
    """
    try:
        # --- Query params ---
        page = request.args.get("page", default=1, type=int)
        per_page = request.args.get("per_page", default=10, type=int)
        search = request.args.get("search", type=str)

        # ✅ Fetch form type details & assigned users from Auth API
        ft = None
        assigned_users = []
        try:
            resp = requests.get(f"{AUTH_API_BASE}/{form_type_id}", timeout=8)
            if resp.status_code == 200:
                api_data = resp.json()
                ft = api_data
                assigned_users = api_data.get("users", [])
            else:
                print(f"⚠️ External API form_type fetch failed: {resp.status_code}")
        except Exception as e:
            print(f"⚠️ Error fetching form_type from API: {e}")

        if not ft:
            return jsonify({"error": "Invalid form_type_id"}), 404

        # ✅ Base query
        query = FormEntry.query.filter_by(form_type_id=form_type_id)

        # ✅ Apply search filter (by submitter or field)
        if search:
            search = f"%{search.lower()}%"
            # Collect IDs from submitter name/email matches
            matching_ids = []
            all_entries = query.all()
            for entry in all_entries:
                submitted_user = get_user_info_by_id(entry.submitted_by_id)
                if submitted_user:
                    username = submitted_user.get("username", "").lower()
                    email = submitted_user.get("email", "").lower()
                    if search.strip("%") in username or search.strip("%") in email:
                        matching_ids.append(entry.id)
                        continue

                # Also match inside field values (if text matches any field)
                field_values = FormFieldValue.query.filter_by(form_entry_id=entry.id).all()
                for fv in field_values:
                    if search.strip("%") in fv.field_value.lower():
                        matching_ids.append(entry.id)
                        break

            query = query.filter(FormEntry.id.in_(matching_ids)) if matching_ids else query.filter(False)

        # ✅ Count total before pagination
        total_count = query.count()

        # ✅ Pagination
        entries = (
            query.order_by(FormEntry.created_at.desc())
            .limit(per_page)
            .offset((page - 1) * per_page)
            .all()
        )

        # ✅ Build response
        results = []
        for entry in entries:
            submitted_user = (
                get_user_info_by_id(entry.submitted_by_id)
                if entry.submitted_by_id else None
            )

            results.append({
                "id": entry.id,
                "form_type_id": form_type_id,
                "form_type_name": ft.get("name"),
                "form_type_description": ft.get("description"),
                "assigned_users": assigned_users,
                "submitted_by": submitted_user,
                "clinic_id": entry.clinic_id,
                "location_id": entry.location_id,
                "created_at": entry.created_at.isoformat() if entry.created_at else None,
                "updated_at": entry.updated_at.isoformat() if entry.updated_at else None
            })

        return jsonify({
            "form_type_id": form_type_id,
            "form_type_name": ft.get("name"),
            "description": ft.get("description"),
            "page": page,
            "per_page": per_page,
            "total": total_count,
            "total_pages": (total_count + per_page - 1) // per_page,
            "entries": results
        }), 200

    except Exception as e:
        print(f"❌ Error in get_form_entries_by_form_type: {e}")
        return jsonify({"error": str(e)}), 500


@form_entries_blueprint.route("/form_entries/details/<int:form_entry_id>", methods=["GET"])
def get_form_entry_details(form_entry_id):
    """
    Fetch a specific FormEntry (by ID) with:
    - Field values from FormFieldValue
    - Submitter info
    - Assigned users (from Auth backend API)
    - FormType details (from Auth backend)
    """
    try:
        # ✅ Fetch FormEntry from DB
        form_entry = FormEntry.query.get(form_entry_id)
        if not form_entry:
            return jsonify({"error": "Form entry not found"}), 404

        form_type_id = form_entry.form_type_id

        # ✅ Fetch form type details & assigned users from Auth backend
        ft = None
        assigned_users = []
        try:
            resp = requests.get(f"{AUTH_API_BASE}/{form_type_id}", timeout=8)
            if resp.status_code == 200:
                api_data = resp.json()
                ft = api_data
                assigned_users = api_data.get("users", [])
            else:
                print(f"⚠️ Failed to fetch form type from Auth API: {resp.status_code}")
        except Exception as e:
            print(f"⚠️ Error fetching form type from API: {e}")

        if not ft:
            return jsonify({"error": "Invalid form_type_id"}), 404

        # ✅ Fetch all field values
        field_values = FormFieldValue.query.filter_by(form_entry_id=form_entry.id).all()
        values_list = [
            {"field_name": fv.field_name, "field_value": fv.field_value}
            for fv in field_values
        ]

        # ✅ Fetch submitter info
        submitted_user = (
            get_user_info_by_id(form_entry.submitted_by_id)
            if form_entry.submitted_by_id else None
        )

        # ✅ Build final response
        result = {
            "id": form_entry.id,
            "form_type_id": form_type_id,
            "form_type_name": ft.get("name"),
            "form_type_description": ft.get("description"),
            "assigned_users": assigned_users,
            "submitted_by": submitted_user,
            "clinic_id": form_entry.clinic_id,
            "location_id": form_entry.location_id,
            "created_at": form_entry.created_at.isoformat() if form_entry.created_at else None,
            "updated_at": form_entry.updated_at.isoformat() if form_entry.updated_at else None,
            "field_values": values_list
        }

        return jsonify(result), 200

    except Exception as e:
        print(f"❌ Error in get_form_entry_details: {e}")
        return jsonify({"error": str(e)}), 500

@form_entries_blueprint.route("/stats/form_entries_summary/<int:form_type_id>", methods=["GET"])
def get_form_type_details(form_type_id):
    """
    📊 Returns detailed stats for a specific form type:
      - Summary statistics (without unique_submitters)
      - Assigned users (with email)
      - Submission list (entries)
    """

    try:
        # ✅ Fetch form type details + assigned users from Auth API
        ft = None
        assigned_users = []
        try:
            resp = requests.get(f"{AUTH_API_BASE}/{form_type_id}", timeout=8)
            if resp.status_code == 200:
                api_data = resp.json()
                ft = api_data

                # include name + email of assigned users
                assigned_users = [
                    {
                        "id": user.get("id"),
                        "name": user.get("username"),
                        "email": user.get("email")
                    }
                    for user in api_data.get("users", [])
                ]
            else:
                print(f"⚠️ External API form_type fetch failed: {resp.status_code}")
        except Exception as e:
            print(f"⚠️ Error fetching form_type from API: {e}")

        if not ft:
            return jsonify({"error": "Invalid form_type_id"}), 404

        # ✅ Fetch form entries for this form type
        form_entries = (
            FormEntry.query.filter_by(form_type_id=form_type_id)
            .order_by(FormEntry.created_at.desc())
            .all()
        )

        total_entries = len(form_entries)
        latest_entry = max((e.created_at for e in form_entries if e.created_at), default=None)

        # 🧠 Prepare submission list
        submissions = []
        for entry in form_entries:
            submitted_user = (
                get_user_info_by_id(entry.submitted_by_id)
                if entry.submitted_by_id else None
            )

            submissions.append({
                "id": entry.id,
                "submitted_by": submitted_user,
                "clinic_id": entry.clinic_id,
                "location_id": entry.location_id,
                "created_at": entry.created_at.isoformat() if entry.created_at else None,
                "updated_at": entry.updated_at.isoformat() if entry.updated_at else None
            })

        # ✅ Final structured response
        return jsonify({
            "form_type_id": form_type_id,
            "form_type_name": ft.get("name"),
            "description": ft.get("description"),
            "assigned_users": assigned_users,       # 👥 includes email
            "stats": {                              # 📊 summary
                "total_entries": total_entries,
                "latest_entry_date": latest_entry.isoformat() if latest_entry else None
            },
            "submissions": submissions               # 📝 submission list
        }), 200

    except Exception as e:
        print(f"❌ Error in get_form_type_details: {e}")
        return jsonify({"error": str(e)}), 500
