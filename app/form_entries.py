from flask import Blueprint, request, jsonify
from datetime import datetime
import os
import requests

from app import db
from app.model import (
    FormEntry,
    FormFieldValue,
    FormEmailRecipient,
    FormType,
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
@form_entries_blueprint.route("/form_entries/field_values/<int:form_entry_id>", methods=["PUT"])
def update_field_values(form_entry_id):
    """
    Update an existing FormEntry using form_type_id,
    update field values, and send email notifications
    to all assigned users fetched via the Auth backend API.
    """
    try:
        data = request.get_json() or {}

        form_type_id = data.get("form_type_id")
        submitted_by_id = data.get("submitted_by_id")
        clinic_id = data.get("clinic_id")
        location_id = data.get("location_id")
        field_values = data.get("field_values", [])

        if not field_values:
            return jsonify({"error": "No field values provided"}), 400

        # ✅ Fetch entry
        form_entry = FormEntry.query.get(form_entry_id)
        if not form_entry:
            return jsonify({"error": "Form entry not found"}), 404

        # ===========================================================
        # ✅ Validate FormType — only via external API
        # ===========================================================
        ft = None
        try:
            resp = requests.get(f"{AUTH_API_BASE}/{form_type_id or form_entry.form_type_id}", timeout=8)
            if resp.status_code == 200:
                ft = resp.json()
            else:
                print(f"⚠️ External API form_type fetch failed: {resp.status_code}")
        except Exception as e:
            print(f"⚠️ Error fetching form_type from API: {e}")

        if not ft:
            return jsonify({"error": "Invalid form_type_id"}), 404

        # ✅ Update base metadata
        form_entry.form_type_id = form_type_id or form_entry.form_type_id
        if submitted_by_id:
            form_entry.submitted_by_id = submitted_by_id
        if clinic_id:
            form_entry.clinic_id = clinic_id
        if location_id:
            form_entry.location_id = location_id
        db.session.commit()

        # ✅ Update or insert field values
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

        # ✅ Fetch assigned users from Auth backend API
        assigned_users = []
        try:
            url = f"{AUTH_API_BASE}/{form_entry.form_type_id}"
            resp = requests.get(url, timeout=8)
            if resp.status_code == 200:
                data = resp.json()
                assigned_users = data.get("users", [])
        except Exception as api_err:
            print(f"❌ Error fetching assigned users: {api_err}")

        # ✅ Compose update email
        ft_name = ft.get("name", "Form")
        subject = f"{ft_name.replace('_', ' ').title()} Form Updated"
        body_lines = [
            f"The <b>{ft_name.replace('_', ' ').title()}</b> form (ID: {form_entry.id}) has been updated.",
            "<br>Please review the changes in your Dental360 dashboard."
        ]
        body_html = generate_email_template(subject, body_lines)

        # ✅ Send emails to assigned users
        email_status = []
        for user in assigned_users:
            email = user.get("email")
            if not email:
                continue

            sent = send_email(email, subject, body_html)
            status = "sent" if sent else "failed"

            # ✅ Log email
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
            "message": "Form entry updated successfully and notifications sent.",
            "form_entry_id": form_entry.id,
            "form_type_id": form_entry.form_type_id,
            "form_type_name": ft_name,
            "email_status": email_status
        }), 200

    except Exception as e:
        db.session.rollback()
        print(f"❌ Error in update_field_values: {e}")
        return jsonify({"error": str(e)}), 500

# =====================================
# 🔵 GET ALL
# =====================================
# @form_entries_blueprint.route("/form_entries", methods=["GET"])
# def get_all_form_entries():
#     """
#     Fetch paginated FormEntry records with optional filters:
#     - clinic_id
#     - location_id
#     - form_type_id
#     - form_type (by name)
#     - submitted_by_id
#     Includes FormType info, field values, submitter, and assigned users from Auth backend.
#     """
#     try:
#         # --- Query params ---
#         clinic_id = request.args.get("clinic_id", type=int)
#         location_id = request.args.get("location_id", type=int)
#         form_type = request.args.get("form_type")               # string name
#         form_type_id = request.args.get("form_type_id", type=int)
#         submitted_by_id = request.args.get("submitted_by_id", type=int)
#         page = request.args.get("page", default=1, type=int)
#         per_page = request.args.get("per_page", default=10, type=int)

#         # --- Base query ---
#         query = (
#             db.session.query(FormEntry, FormType)
#             .select_from(FormEntry)
#             .join(FormType, FormEntry.form_type_id == FormType.id)
#         )

#         # --- Filters ---
#         if clinic_id:
#             query = query.filter(FormEntry.clinic_id == clinic_id)
#         if location_id:
#             query = query.filter(FormEntry.location_id == location_id)
#         if form_type_id:
#             query = query.filter(FormEntry.form_type_id == form_type_id)
#         if form_type:
#             query = query.filter(FormType.name.ilike(f"%{form_type}%"))
#         if submitted_by_id:
#             query = query.filter(FormEntry.submitted_by_id == submitted_by_id)

#         # --- Count total before pagination ---
#         total_count = query.count()

#         # --- Pagination ---
#         query = query.order_by(FormEntry.created_at.desc())
#         paginated = query.limit(per_page).offset((page - 1) * per_page).all()


#         results = []
#         for entry, ft in paginated:
#             # ✅ Field values
#             field_values = FormFieldValue.query.filter_by(form_entry_id=entry.id).all()
#             values_list = [
#                 {"field_name": fv.field_name, "field_value": fv.field_value}
#                 for fv in field_values
#             ]

#             # ✅ Submitter info
#             submitted_user = get_user_info_by_id(entry.submitted_by_id) if entry.submitted_by_id else None

#             # ✅ Assigned users (via Auth API)
#             assigned_users = []
#             try:
#                 resp = requests.get(f"{AUTH_API_BASE}/{entry.form_type_id}", timeout=8)
#                 if resp.status_code == 200:
#                     api_data = resp.json()
#                     assigned_users = api_data.get("users", [])
#             except Exception as e:
#                 print(f"⚠️ Error fetching assigned users for form_type_id={entry.form_type_id}: {e}")

#             results.append({
#                 "id": entry.id,
#                 "form_type_id": entry.form_type_id,
#                 "form_type_name": ft.name,
#                 "form_type_description": ft.description,
#                 "assigned_users": assigned_users,
#                 "submitted_by": submitted_user,
#                 "clinic_id": entry.clinic_id,
#                 "location_id": entry.location_id,
#                 "created_at": entry.created_at.isoformat() if entry.created_at else None,
#                 "updated_at": entry.updated_at.isoformat() if entry.updated_at else None,
#                 "field_values": values_list
#             })

#         return jsonify({
#             "page": page,
#             "per_page": per_page,
#             "total": total_count,
#             "total_pages": (total_count + per_page - 1) // per_page,
#             "form_entries": results
#         }), 200

#     except Exception as e:
#         print(f"❌ Error in get_all_form_entries: {e}")
#         return jsonify({"error": str(e)}), 500

# # =====================================
# # 🔵 GET BY ID
# # =====================================
# @form_entries_blueprint.route("/form_entries/<int:form_entry_id>", methods=["GET"])
# def get_form_entry_by_id(form_entry_id):
#     """
#     Fetch a single FormEntry by ID with:
#     - FormType info (name, description)
#     - Field values
#     - Submitter user info
#     - Assigned users (from Auth backend)
#     """
#     try:
#         # ✅ Join FormEntry & FormType
#         row = (
#             db.session.query(FormEntry, FormType)
#             .select_from(FormEntry)
#             .join(FormType, FormEntry.form_type_id == FormType.id)
#             .filter(FormEntry.id == form_entry_id)
#             .first()
#         )

#         if not row:
#             return jsonify({"error": "Form entry not found"}), 404

#         entry, ft = row

#         # ✅ Field values
#         field_values = FormFieldValue.query.filter_by(form_entry_id=entry.id).all()
#         values_list = [
#             {"field_name": fv.field_name, "field_value": fv.field_value}
#             for fv in field_values
#         ]

#         # ✅ Submitter info
#         submitted_user = get_user_info_by_id(entry.submitted_by_id) if entry.submitted_by_id else None

#         # ✅ Assigned users (via Auth API)
#         assigned_users = []
#         try:
#             resp = requests.get(f"{AUTH_API_BASE}/{entry.form_type_id}", timeout=8)
#             if resp.status_code == 200:
#                 api_data = resp.json()
#                 assigned_users = api_data.get("users", [])
#         except Exception as e:
#             print(f"⚠️ Error fetching assigned users for form_type_id={entry.form_type_id}: {e}")

#         # ✅ Build response
#         return jsonify({
#             "id": entry.id,
#             "form_type_id": entry.form_type_id,
#             "form_type_name": ft.name,
#             "form_type_description": ft.description,
#             "assigned_users": assigned_users,
#             "submitted_by": submitted_user,
#             "clinic_id": entry.clinic_id,
#             "location_id": entry.location_id,
#             "created_at": entry.created_at.isoformat() if entry.created_at else None,
#             "updated_at": entry.updated_at.isoformat() if entry.updated_at else None,
#             "field_values": values_list
#         }), 200

#     except Exception as e:
#         print(f"❌ Error in get_form_entry_by_id: {e}")
#         return jsonify({"error": str(e)}), 500



@form_entries_blueprint.route("/form_entries/by_form_type/<int:form_type_id>", methods=["GET"])
def get_form_entries_by_form_type(form_type_id):
    """
    Fetch all FormEntry records for a given form_type_id.
    Includes:
    - submitter info
    - assigned users (from Auth backend API)
    - form type details (from Auth backend)
    """
    try:
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

        # ✅ Fetch all FormEntries for this form type
        form_entries = (
            FormEntry.query.filter_by(form_type_id=form_type_id)
            .order_by(FormEntry.created_at.desc())
            .all()
        )

        # ✅ Prepare clean response (no field values)
        results = []
        for entry in form_entries:
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
            "total_entries": len(results),
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


# =====================================
# 🔴 DELETE
# =====================================
# @form_entries_blueprint.route("/form_entries/<int:entry_id>", methods=["DELETE"])
# def delete_form_entry(entry_id):
#     try:
#         entry = FormEntry.query.get(entry_id)
#         if not entry:
#             return jsonify({"error": "Form entry not found"}), 404

#         FormFieldValue.query.filter_by(form_entry_id=entry.id).delete()
#         FormAssignment.query.filter_by(form_entry_id=entry.id).delete()
#         db.session.delete(entry)
#         db.session.commit()

#         return jsonify({"message": "Form entry deleted successfully"}), 200

#     except Exception as e:
#         db.session.rollback()
#         return jsonify({"error": str(e)}), 500


# @form_entries_blueprint.route("/form_email_recipients", methods=["POST"])
# def add_form_email_recipient():
#     try:
#         data = request.get_json() or {}
#         email = data.get("email")
#         form_type = data.get("form_type")

#         if not email or not form_type:
#             return jsonify({"error": "email and form_type are required"}), 400

#         new_recipient = FormEmailRecipient(email=email, form_type=form_type)
#         db.session.add(new_recipient)
#         db.session.commit()

#         return jsonify({
#             "message": "Email recipient added successfully.",
#             "recipient_id": new_recipient.id
#         }), 201

#     except Exception as e:
#         db.session.rollback()
#         print(f"❌ Error in add_form_email_recipient: {e}")
#         return jsonify({"error": str(e)}), 500

# @form_entries_blueprint.route("/form_email_recipients/<int:recipient_id>", methods=["PUT"])
# def update_form_email_recipient(recipient_id):
#     try:
#         data = request.get_json() or {}
#         recipient = FormEmailRecipient.query.get(recipient_id)

#         if not recipient:
#             return jsonify({"error": "Recipient not found"}), 404

#         recipient.email = data.get("email", recipient.email)
#         recipient.form_type = data.get("form_type", recipient.form_type)
#         db.session.commit()

#         return jsonify({
#             "message": "Email recipient updated successfully.",
#             "recipient_id": recipient.id
#         }), 200

#     except Exception as e:
#         db.session.rollback()
#         print(f"❌ Error in update_form_email_recipient: {e}")
#         return jsonify({"error": str(e)}), 500

# @form_entries_blueprint.route("/form_email_recipients/<int:recipient_id>", methods=["DELETE"])
# def delete_form_email_recipient(recipient_id):
#     try:
#         recipient = FormEmailRecipient.query.get(recipient_id)
#         if not recipient:
#             return jsonify({"error": "Recipient not found"}), 404

#         db.session.delete(recipient)
#         db.session.commit()

#         return jsonify({"message": "Email recipient deleted successfully."}), 200

#     except Exception as e:
#         db.session.rollback()
#         print(f"❌ Error in delete_form_email_recipient: {e}")
#         return jsonify({"error": str(e)}), 500

# @form_entries_blueprint.route("/form_email_recipients", methods=["GET"])
# def get_all_form_email_recipients():
#     try:
#         form_type = request.args.get("form_type")
#         query = FormEmailRecipient.query
#         if form_type:
#             query = query.filter_by(form_type=form_type)

#         recipients = query.order_by(FormEmailRecipient.created_at.desc()).all()
#         return jsonify([
#             {
#                 "id": r.id,
#                 "email": r.email,
#                 "form_type": r.form_type,
#                 "created_at": r.created_at.isoformat()
#             }
#             for r in recipients
#         ]), 200

#     except Exception as e:
#         print(f"❌ Error in get_all_form_email_recipients: {e}")
#         return jsonify({"error": str(e)}), 500

