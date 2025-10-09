from flask import Blueprint, request, jsonify
from datetime import datetime
from app import db
from app.model import FormEntry, FormFieldValue, FormEmailRecipient, FormType, FormTypeUserNoti, FormEmailLog
from app.utils.email_templete import send_email, get_user_info_by_id, generate_email_template
from app.dashboard_routes import require_api_key, validate_token


form_entries_blueprint = Blueprint("form_entries", __name__)

# =====================================
# 🟢 STEP 1 — Create Base FormEntry
# =====================================
# @form_entries_blueprint.route("/form_entries", methods=["POST"])
# def create_form_entry():
#     try:
#         data = request.get_json()

#         form_entry = FormEntry(
#             form_type=data.get("form_type"),
#             submitted_by_id=data.get("submitted_by_id"),
#             clinic_id=data.get("clinic_id"),
#             location_id=data.get("location_id"),
#         )
#         db.session.add(form_entry)
#         db.session.commit()

#         return jsonify({
#             "message": "Form entry created successfully",
#             "form_entry_id": form_entry.id
#         }), 201

#     except Exception as e:
#         db.session.rollback()
#         return jsonify({"error": str(e)}), 500

# # =====================================
# # 🟡 UPDATE
# # =====================================
# @form_entries_blueprint.route("/form_entries/<int:entry_id>", methods=["PUT"])
# def update_form_entry(entry_id):
#     try:
#         data = request.get_json()
#         entry = FormEntry.query.get(entry_id)
#         if not entry:
#             return jsonify({"error": "Form entry not found"}), 404

#         entry.form_type = data.get("form_type", entry.form_type)
#         entry.clinic_id = data.get("clinic_id", entry.clinic_id)
#         entry.location_id = data.get("location_id", entry.location_id)
#         entry.updated_at = datetime.utcnow()
#         db.session.commit()

#         return jsonify({"message": "Form entry updated successfully"}), 200

#     except Exception as e:
#         db.session.rollback()
#         return jsonify({"error": str(e)}), 500



# =====================================
# 🟢 STEP 2 — Assign User
# =====================================
# @form_entries_blueprint.route("/form_entries/assign/<int:form_entry_id>", methods=["POST"])
# def assign_form_entry(form_entry_id):
#     try:
#         data = request.get_json()

#         # ✅ Check if form entry exists
#         entry = FormEntry.query.get(form_entry_id)
#         if not entry:
#             return jsonify({"error": "Form entry not found"}), 404

#         # ✅ Add or update field values
#         field_values = data.get("field_values", [])
#         for field in field_values:
#             fv = FormFieldValue(
#                 form_entry_id=entry.id,
#                 field_name=field.get("field_name"),
#                 field_value=field.get("field_value"),
#             )
#             db.session.add(fv)
#         db.session.commit()

#         # ✅ Assign to user
#         assigned_to = data.get("assigned_to")  # user_id of the person being assigned
#         assigned_by = data.get("assigned_by")  # who assigned
#         if not assigned_to:
#             return jsonify({"error": "Missing assigned_to user_id"}), 400

#         # ✅ Fetch user info from API
#         user_info = get_user_info_by_id(assigned_to)
#         if not user_info:
#             return jsonify({"error": "Assigned user info not found"}), 404

#         # ✅ Save FormAssignment
#         assignment = FormAssignment(
#             form_entry_id=entry.id,
#             assigned_by=assigned_by,
#             assigned_to=assigned_to,
#             created_at=datetime.utcnow()
#         )
#         db.session.add(assignment)
#         db.session.commit()

#         return jsonify({
#             "message": "Form assigned successfully",
#             "form_entry_id": entry.id,
#             "assigned_user": user_info
#         }), 201

#     except Exception as e:
#         db.session.rollback()
#         print(f"❌ Error in assign_form_entry: {e}")
#         return jsonify({"error": str(e)}), 500


# =====================================
# 🟢 STEP 3 — Add Field Values + Send Email
# =====================================
import requests
from datetime import datetime
from flask import jsonify, request
import os
MAILGUN_API_URL = os.getenv("MAILGUN_API_URL")
MAILGUN_API_KEY = os.getenv("MAILGUN_API_KEY") 

# ==============================
# 🔹 Email Utilities
# ==============================




# ==============================
# 🔹 Main API Endpoint
# ==============================
# ================================================================
# 🟢 CREATE FORM ENTRY + AUTO EMAIL TO MAPPED USERS
# ================================================================
@form_entries_blueprint.route("/form_entries/field_values", methods=["POST"])
def create_form_entry_with_field_values():
    """
    Create a new FormEntry using form_type_id only,
    save field values, and send notification emails
    to all users mapped in FormTypeUserNoti.
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

        # ✅ Validate FormType
        ft = FormType.query.get(form_type_id)
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

        # ✅ Get users mapped to this form_type_id
        mapped_users = FormTypeUserNoti.query.filter_by(form_type_id=form_type_id).all()
        if not mapped_users:
            return jsonify({
                "message": "Form entry created successfully but no mapped users found.",
                "form_entry_id": new_entry.id
            }), 201

        # ✅ Compose email
        subject = f"New {ft.name.replace('_', ' ').title()} Form Submitted"
        body_lines = [
            f"A new <b>{ft.name.replace('_', ' ').title()}</b> form has been submitted on the Dental360 portal.",
            "<br>Please review the form in your Dental360 dashboard."
        ]
        body_html = generate_email_template(subject, body_lines)

        email_status = []
        for mapping in mapped_users:
            user_info = get_user_info_by_id(mapping.user_id)
            if not user_info or not user_info.get("email"):
                continue

            email = user_info["email"]
            sent = send_email(email, subject, body_html)
            status = "sent" if sent else "failed"

            # ✅ Log in FormEmailLog
            db.session.add(FormEmailLog(
                form_entry_id=new_entry.id,
                form_type_id=form_type_id,
                sender_id=submitted_by_id,
                email_type="form_submission",
                sender_email=user_info.get("email"),
                receiver_id=mapping.user_id,
                message=f"Form submitted for {ft.name}",
                status=status
            ))
            email_status.append({"email": email, "status": status})

        db.session.commit()

        return jsonify({
            "message": "Form entry created successfully and notifications sent.",
            "form_entry_id": new_entry.id,
            "form_type_id": form_type_id,
            # "recipients": [e["email"] for e in email_status],
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
    Update an existing FormEntry (using form_type_id only),
    update field values, and send email notifications
    to all mapped users in FormTypeUserNoti.
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

        # ✅ Resolve FormType
        ft = FormType.query.get(form_type_id or form_entry.form_type_id)
        if not ft:
            return jsonify({"error": "Invalid form_type_id"}), 404

        # ✅ Update metadata
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
                form_entry_id=form_entry.id,
                field_name=field_name
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

        # ✅ Fetch mapped users
        mapped_users = FormTypeUserNoti.query.filter_by(form_type_id=form_entry.form_type_id).all()

        # ✅ Compose update email
        subject = f"{ft.name.replace('_', ' ').title()} Form Updated"
        body_lines = [
            f"The <b>{ft.name.replace('_', ' ').title()}</b> form (ID: {form_entry.id}) has been updated.",
            "<br>Please review the changes in your Dental360 dashboard."
        ]
        body_html = generate_email_template(subject, body_lines)

        email_status = []
        for mapping in mapped_users:
            user_info = get_user_info_by_id(mapping.user_id)
            if not user_info or not user_info.get("email"):
                continue

            email = user_info["email"]
            sent = send_email(email, subject, body_html)
            status = "sent" if sent else "failed"

            # ✅ Log email
            db.session.add(FormEmailLog(
                form_entry_id=form_entry.id,
                form_type_id=form_entry.form_type_id,
                sender_id=submitted_by_id,
                email_type="form_update",
                sender_email=user_info.get("email"),
                receiver_id=mapping.user_id,
                message=f"Form update notification sent for {ft.name}",
                status=status
            ))
            email_status.append({"email": email, "status": status})

        db.session.commit()

        return jsonify({
            "message": "Form entry updated successfully and notifications sent.",
            "form_entry_id": form_entry.id,
            "form_type_id": form_entry.form_type_id,
            "form_type_name": ft.name,
            "recipients": [e["email"] for e in email_status],
            "email_status": email_status
        }), 200

    except Exception as e:
        db.session.rollback()
        print(f"❌ Error in update_field_values: {e}")
        return jsonify({"error": str(e)}), 500

# =====================================
# 🔵 GET ALL
# =====================================
@form_entries_blueprint.route("/form_entries", methods=["GET"])
def get_all_form_entries():
    """
    Fetch paginated FormEntry records with optional filters:
    - clinic_id
    - location_id
    - form_type_id
    - form_type (by name)
    - submitted_by_id
    Includes joined FormType info and all field values.
    """
    try:
        # --- Query params ---
        clinic_id = request.args.get("clinic_id", type=int)
        location_id = request.args.get("location_id", type=int)
        form_type = request.args.get("form_type")               # string name of form type
        form_type_id = request.args.get("form_type_id", type=int)
        submitted_by_id = request.args.get("submitted_by_id", type=int)
        page = request.args.get("page", default=1, type=int)
        per_page = request.args.get("per_page", default=10, type=int)

        # --- Base query ---
        query = (
            db.session.query(FormEntry, FormType)
            .select_from(FormEntry)
            .join(FormType, FormEntry.form_type_id == FormType.id)
        )

        # --- Filters ---
        if clinic_id:
            query = query.filter(FormEntry.clinic_id == clinic_id)
        if location_id:
            query = query.filter(FormEntry.location_id == location_id)
        if form_type_id:
            query = query.filter(FormEntry.form_type_id == form_type_id)
        if form_type:
            query = query.filter(FormType.name.ilike(f"%{form_type}%"))
        if submitted_by_id:
            query = query.filter(FormEntry.submitted_by_id == submitted_by_id)

        # --- Count total before pagination ---
        total_count = query.count()

        # --- Pagination ---
        query = query.order_by(FormEntry.created_at.desc())
        paginated = query.limit(per_page).offset((page - 1) * per_page).all()

        # --- Build response list ---
        results = []
        for entry, ft in paginated:
            # Get all field values for this entry
            field_values = FormFieldValue.query.filter_by(form_entry_id=entry.id).all()
            values_list = [
                {"field_name": fv.field_name, "field_value": fv.field_value}
                for fv in field_values
            ]

            # Get submitter info
            submitted_user = get_user_info_by_id(entry.submitted_by_id) if entry.submitted_by_id else None

            results.append({
                "id": entry.id,
                "form_type_id": entry.form_type_id,
                "form_type_name": ft.name,
                "form_type_description": ft.description,
                "submitted_by": submitted_user,
                "clinic_id": entry.clinic_id,
                "location_id": entry.location_id,
                "created_at": entry.created_at.isoformat() if entry.created_at else None,
                "updated_at": entry.updated_at.isoformat() if entry.updated_at else None,
                "field_values": values_list
            })

        # --- Return paginated result ---
        return jsonify({
            "page": page,
            "per_page": per_page,
            "total": total_count,
            "total_pages": (total_count + per_page - 1) // per_page,
            "form_entries": results
        }), 200

    except Exception as e:
        print(f"❌ Error in get_all_form_entries: {e}")
        return jsonify({"error": str(e)}), 500

# # =====================================
# # 🔵 GET BY ID
# # =====================================
@form_entries_blueprint.route("/form_entries/<int:form_entry_id>", methods=["GET"])
def get_form_entry_by_id(form_entry_id):
    """
    Fetch a single FormEntry by ID with:
    - Joined FormType info (name, description)
    - Field values
    - Email recipients
    - Submitter user info
    """
    try:
        # ✅ Manual join between FormEntry & FormType
        row = (
            db.session.query(FormEntry, FormType)
            .select_from(FormEntry)
            .join(FormType, FormEntry.form_type_id == FormType.id)
            .filter(FormEntry.id == form_entry_id)
            .first()
        )

        if not row:
            return jsonify({"error": "Form entry not found"}), 404

        entry, ft = row

        # ✅ Field values
        field_values = FormFieldValue.query.filter_by(form_entry_id=entry.id).all()
        values_list = [
            {"field_name": fv.field_name, "field_value": fv.field_value}
            for fv in field_values
        ]

        # ✅ Recipients
        # recipients = FormEmailRecipient.query.filter_by(form_type=ft.name).all()
        # recipient_emails = [r.email for r in recipients]

        # ✅ Submitter info
        submitted_user = get_user_info_by_id(entry.submitted_by_id) if entry.submitted_by_id else None

        # ✅ Build response
        return jsonify({
            "id": entry.id,
            "form_type_id": entry.form_type_id,
            "form_type_name": ft.name,
            "form_type_description": ft.description,
            "submitted_by": submitted_user,
            "clinic_id": entry.clinic_id,
            "location_id": entry.location_id,
            "created_at": entry.created_at.isoformat() if entry.created_at else None,
            "updated_at": entry.updated_at.isoformat() if entry.updated_at else None,
            "field_values": values_list,
            # "recipients": recipient_emails
        }), 200

    except Exception as e:
        print(f"❌ Error in get_form_entry_by_id: {e}")
        return jsonify({"error": str(e)}), 500

# =====================================
# 🔴 DELETE
# =====================================
@form_entries_blueprint.route("/form_entries/<int:entry_id>", methods=["DELETE"])
def delete_form_entry(entry_id):
    try:
        entry = FormEntry.query.get(entry_id)
        if not entry:
            return jsonify({"error": "Form entry not found"}), 404

        FormFieldValue.query.filter_by(form_entry_id=entry.id).delete()
        FormAssignment.query.filter_by(form_entry_id=entry.id).delete()
        db.session.delete(entry)
        db.session.commit()

        return jsonify({"message": "Form entry deleted successfully"}), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({"error": str(e)}), 500


@form_entries_blueprint.route("/form_email_recipients", methods=["POST"])
def add_form_email_recipient():
    try:
        data = request.get_json() or {}
        email = data.get("email")
        form_type = data.get("form_type")

        if not email or not form_type:
            return jsonify({"error": "email and form_type are required"}), 400

        new_recipient = FormEmailRecipient(email=email, form_type=form_type)
        db.session.add(new_recipient)
        db.session.commit()

        return jsonify({
            "message": "Email recipient added successfully.",
            "recipient_id": new_recipient.id
        }), 201

    except Exception as e:
        db.session.rollback()
        print(f"❌ Error in add_form_email_recipient: {e}")
        return jsonify({"error": str(e)}), 500

@form_entries_blueprint.route("/form_email_recipients/<int:recipient_id>", methods=["PUT"])
def update_form_email_recipient(recipient_id):
    try:
        data = request.get_json() or {}
        recipient = FormEmailRecipient.query.get(recipient_id)

        if not recipient:
            return jsonify({"error": "Recipient not found"}), 404

        recipient.email = data.get("email", recipient.email)
        recipient.form_type = data.get("form_type", recipient.form_type)
        db.session.commit()

        return jsonify({
            "message": "Email recipient updated successfully.",
            "recipient_id": recipient.id
        }), 200

    except Exception as e:
        db.session.rollback()
        print(f"❌ Error in update_form_email_recipient: {e}")
        return jsonify({"error": str(e)}), 500

@form_entries_blueprint.route("/form_email_recipients/<int:recipient_id>", methods=["DELETE"])
def delete_form_email_recipient(recipient_id):
    try:
        recipient = FormEmailRecipient.query.get(recipient_id)
        if not recipient:
            return jsonify({"error": "Recipient not found"}), 404

        db.session.delete(recipient)
        db.session.commit()

        return jsonify({"message": "Email recipient deleted successfully."}), 200

    except Exception as e:
        db.session.rollback()
        print(f"❌ Error in delete_form_email_recipient: {e}")
        return jsonify({"error": str(e)}), 500

@form_entries_blueprint.route("/form_email_recipients", methods=["GET"])
def get_all_form_email_recipients():
    try:
        form_type = request.args.get("form_type")
        query = FormEmailRecipient.query
        if form_type:
            query = query.filter_by(form_type=form_type)

        recipients = query.order_by(FormEmailRecipient.created_at.desc()).all()
        return jsonify([
            {
                "id": r.id,
                "email": r.email,
                "form_type": r.form_type,
                "created_at": r.created_at.isoformat()
            }
            for r in recipients
        ]), 200

    except Exception as e:
        print(f"❌ Error in get_all_form_email_recipients: {e}")
        return jsonify({"error": str(e)}), 500



# =====================================
# 🟢 CREATE FORM TYPE + USER MAP
# =====================================
@form_entries_blueprint.route("/form_types", methods=["POST"])
def create_form_type():
    """Create a new form type with optional user mappings."""
    try:
        data = request.get_json() or {}
        name = data.get("name")
        description = data.get("description")
        user_ids = data.get("user_ids", [])  # optional list of user IDs

        if not name:
            return jsonify({"error": "name is required"}), 400

        # Check for duplicates
        if FormType.query.filter_by(name=name).first():
            return jsonify({"error": "Form type already exists"}), 409

        # Create FormType
        form_type = FormType(name=name, description=description)
        db.session.add(form_type)
        db.session.commit()

        # Add user mappings
        for uid in user_ids:
            db.session.add(FormTypeUserNoti(form_type_id=form_type.id, user_id=uid))
        db.session.commit()

        return jsonify({
            "message": "Form type created successfully.",
            "id": form_type.id,
            "name": form_type.name,
            "description": form_type.description,
            "user_ids": user_ids
        }), 201

    except Exception as e:
        db.session.rollback()
        print(f"❌ Error in create_form_type: {e}")
        return jsonify({"error": str(e)}), 500



# =====================================
# 🔵 GET ALL FORM TYPES (with users)
# =====================================
@form_entries_blueprint.route("/form_types", methods=["GET"])
def get_all_form_types():
    """Fetch all form types with assigned users."""
    try:
        form_types = FormType.query.order_by(FormType.id.desc()).all()
        results = []

        for ft in form_types:
            mappings = FormTypeUserNoti.query.filter_by(form_type_id=ft.id).all()
            user_ids = [m.user_id for m in mappings]
            users = [get_user_info_by_id(uid) for uid in user_ids if uid]

            results.append({
                "id": ft.id,
                "name": ft.name,
                "description": ft.description,
                "created_at": ft.created_at.isoformat() if ft.created_at else None,
                "user_ids": user_ids,
                "users": users
            })

        return jsonify({
            "total": len(results),
            "form_types": results
        }), 200

    except Exception as e:
        print(f"❌ Error in get_all_form_types: {e}")
        return jsonify({"error": str(e)}), 500



# =====================================
# 🔵 GET FORM TYPE BY ID (with users)
# =====================================
@form_entries_blueprint.route("/form_types/<int:type_id>", methods=["GET"])
def get_form_type_by_id(type_id):
    """Fetch a single form type with assigned users."""
    try:
        ft = FormType.query.get(type_id)
        if not ft:
            return jsonify({"error": "Form type not found"}), 404

        mappings = FormTypeUserNoti.query.filter_by(form_type_id=ft.id).all()
        user_ids = [m.user_id for m in mappings]
        users = [get_user_info_by_id(uid) for uid in user_ids if uid]

        return jsonify({
            "id": ft.id,
            "name": ft.name,
            "description": ft.description,
            "created_at": ft.created_at.isoformat() if ft.created_at else None,
            "user_ids": user_ids,
            "users": users
        }), 200

    except Exception as e:
        print(f"❌ Error in get_form_type_by_id: {e}")
        return jsonify({"error": str(e)}), 500



# =====================================
# 🟠 UPDATE FORM TYPE + USERS
# =====================================
@form_entries_blueprint.route("/form_types/<int:type_id>", methods=["PUT"])
def update_form_type(type_id):
    """Update a form type and its user mappings."""
    try:
        data = request.get_json() or {}
        ft = FormType.query.get(type_id)
        if not ft:
            return jsonify({"error": "Form type not found"}), 404

        new_name = data.get("name")
        if new_name and new_name != ft.name:
            if FormType.query.filter_by(name=new_name).first():
                return jsonify({"error": "Form type name already exists"}), 409
            ft.name = new_name

        ft.description = data.get("description", ft.description)
        db.session.commit()

        # Update user mappings
        new_user_ids = data.get("user_ids", [])
        if isinstance(new_user_ids, list):
            FormTypeUserNoti.query.filter_by(form_type_id=ft.id).delete()
            for uid in new_user_ids:
                db.session.add(FormTypeUserNoti(form_type_id=ft.id, user_id=uid))
            db.session.commit()

        return jsonify({
            "message": "Form type updated successfully.",
            "id": ft.id,
            "name": ft.name,
            "description": ft.description,
            "user_ids": new_user_ids
        }), 200

    except Exception as e:
        db.session.rollback()
        print(f"❌ Error in update_form_type: {e}")
        return jsonify({"error": str(e)}), 500



# =====================================
# 🔴 DELETE FORM TYPE + USER MAPS
# =====================================
@form_entries_blueprint.route("/form_types/<int:type_id>", methods=["DELETE"])
def delete_form_type(type_id):
    """Delete a form type and its user mappings."""
    try:
        ft = FormType.query.get(type_id)
        if not ft:
            return jsonify({"error": "Form type not found"}), 404

        FormTypeUserNoti.query.filter_by(form_type_id=ft.id).delete()
        db.session.delete(ft)
        db.session.commit()

        return jsonify({"message": "Form type and user mappings deleted successfully."}), 200

    except Exception as e:
        db.session.rollback()
        print(f"❌ Error in delete_form_type: {e}")
        return jsonify({"error": str(e)}), 500
