from flask import Blueprint, request, jsonify
from datetime import datetime
from app import db
from app.model import FormEntry, FormFieldValue, FormEmailRecipient, FormType
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
@form_entries_blueprint.route("/form_entries/field_values", methods=["POST"])
def create_form_entry_with_field_values():
    """
    Create a new FormEntry using form_type_id only,
    save field values, and send notification emails.
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
            field_name = field.get("field_name")
            field_value = field.get("field_value")
            if not field_name:
                continue
            db.session.add(FormFieldValue(
                form_entry_id=new_entry.id,
                field_name=field_name,
                field_value=field_value
            ))
        db.session.commit()

        # ✅ Fetch email recipients by form_type_id
        recipients = FormEmailRecipient.query.filter_by(form_type_id=form_type_id).all()
        recipient_emails = [r.email for r in recipients]

        # ✅ Email notification
        subject = f"New {ft.display_name or ft.name} Form Submitted"
        body_lines = [
            f"A new <b>{ft.display_name or ft.name}</b> form has been submitted on the Dental360 portal.",
            "<br>Please review the form in your Dental360 dashboard."
        ]
        body_html = generate_email_template(subject, body_lines)

        email_status = []
        if recipient_emails:
            for email in recipient_emails:
                sent = send_email(email, subject, body_html)
                email_status.append({"email": email, "status": "sent" if sent else "failed"})
        else:
            email_status.append({"status": "no recipients found"})

        return jsonify({
            "message": "Form entry created successfully and notifications sent.",
            "form_entry_id": new_entry.id,
            "form_type_id": form_type_id,
            "recipients": recipient_emails,
            "email_status": email_status
        }), 201

    except Exception as e:
        db.session.rollback()
        print(f"❌ Error in create_form_entry_with_field_values: {e}")
        return jsonify({"error": str(e)}), 500


# =====================================
# 🟢 STEP 4 — Update Field Values
# =====================================
@form_entries_blueprint.route("/form_entries/field_values/<int:form_entry_id>", methods=["PUT"])
def update_field_values(form_entry_id):
    """
    Update an existing FormEntry using form_type_id only,
    update field values, and send email notifications.
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

        # ✅ Resolve FormType (auto-handle string)
        ft = None
        if isinstance(form_type_id, str):
            ft = FormType.query.filter_by(name=form_type_id).first()
            if not ft:
                return jsonify({"error": f"Invalid form_type name '{form_type_id}'"}), 404
            form_type_id = ft.id
        elif form_type_id:
            ft = FormType.query.get(form_type_id)
            if not ft:
                return jsonify({"error": "Invalid form_type_id"}), 404
        else:
            ft = FormType.query.get(form_entry.form_type_id)
            form_type_id = ft.id if ft else form_entry.form_type_id

        # ✅ Update metadata
        form_entry.form_type_id = form_type_id
        if submitted_by_id:
            form_entry.submitted_by_id = submitted_by_id
        if clinic_id:
            form_entry.clinic_id = clinic_id
        if location_id:
            form_entry.location_id = location_id

        db.session.commit()

        # ✅ Update / insert field values
        for field in field_values:
            field_name = field.get("field_name")
            field_value = field.get("field_value")
            if not field_name:
                continue

            existing_field = FormFieldValue.query.filter_by(
                form_entry_id=form_entry.id, field_name=field_name
            ).first()

            if existing_field:
                existing_field.field_value = field_value
            else:
                db.session.add(FormFieldValue(
                    form_entry_id=form_entry.id,
                    field_name=field_name,
                    field_value=field_value
                ))

        db.session.commit()

        # ✅ Fetch recipients using form_type_id (not name)
        recipients = FormEmailRecipient.query.filter_by(form_type_id=form_type_id).all()
        recipient_emails = [r.email for r in recipients]

        # ✅ Compose email
        subject = f"{ft.display_name or ft.name} Form Updated"
        body_lines = [
            f"The <b>{ft.display_name or ft.name}</b> form (ID: {form_entry.id}) has been updated.",
            "<br>Please review the changes in your Dental360 dashboard."
        ]
        body_html = generate_email_template(subject, body_lines)

        # ✅ Send notification emails
        email_status = []
        if recipient_emails:
            for email in recipient_emails:
                sent = send_email(email, subject, body_html)
                email_status.append({"email": email, "status": "sent" if sent else "failed"})
        else:
            email_status.append({"status": "no recipients found"})

        return jsonify({
            "message": "Form entry updated successfully and notifications sent.",
            "form_entry_id": form_entry.id,
            "form_type_id": form_type_id,
            "form_type_name": ft.name if ft else None,
            "updated_fields": len(field_values),
            "recipients": recipient_emails,
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
    Fetch all FormEntry records with optional filters:
    - clinic_id
    - location_id
    - form_type or form_type_id
    Joins with form_types manually to include form type name & description.
    """
    try:
        clinic_id = request.args.get("clinic_id", type=int)
        location_id = request.args.get("location_id", type=int)
        form_type = request.args.get("form_type")      # form_types.name
        form_type_id = request.args.get("form_type_id", type=int)

        # ✅ Explicit join (no relationship needed)
        query = (
            db.session.query(FormEntry, FormType)
            .select_from(FormEntry)
            .join(FormType, FormEntry.form_type_id == FormType.id)
        )

        # ✅ Apply filters
        if clinic_id:
            query = query.filter(FormEntry.clinic_id == clinic_id)
        if location_id:
            query = query.filter(FormEntry.location_id == location_id)
        if form_type_id:
            query = query.filter(FormEntry.form_type_id == form_type_id)
        if form_type:
            query = query.filter(FormType.name == form_type)

        rows = query.order_by(FormEntry.id.desc()).all()

        results = []
        for entry, ft in rows:
            # ✅ Field values
            field_values = FormFieldValue.query.filter_by(form_entry_id=entry.id).all()
            values_list = [
                {"field_name": fv.field_name, "field_value": fv.field_value}
                for fv in field_values
            ]

            # ✅ Recipients (still by string form_type name)
            # recipients = FormEmailRecipient.query.filter_by(form_type=ft.name).all()
            # recipient_emails = [r.email for r in recipients]

            # ✅ Submitter info
            submitted_user = get_user_info_by_id(entry.submitted_by_id) if entry.submitted_by_id else None

            # ✅ Build response
            results.append({
                "id": entry.id,
                "form_type_id": entry.form_type_id,
                "form_type_name": ft.name,
                "form_type_display_name": ft.display_name,
                "form_type_description": ft.description,
                "submitted_by": submitted_user,
                "clinic_id": entry.clinic_id,
                "location_id": entry.location_id,
                "created_at": entry.created_at.isoformat() if entry.created_at else None,
                "updated_at": entry.updated_at.isoformat() if entry.updated_at else None,
                "field_values": values_list,
                # "recipients": recipient_emails
            })

        return jsonify({"total": len(results), "form_entries": results}), 200

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
            "form_type_display_name": ft.display_name,
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



@form_entries_blueprint.route("/form_types", methods=["POST"])
def create_form_type():
    """Create a new form type"""
    try:
        data = request.get_json() or {}
        name = data.get("name")
        # display_name = data.get("display_name")
        description = data.get("description")

        if not name:
            return jsonify({"error": "name is required"}), 400

        # Check if already exists
        existing = FormType.query.filter_by(name=name).first()
        if existing:
            return jsonify({"error": "Form type already exists"}), 409

        form_type = FormType(name=name, description=description)
        db.session.add(form_type)
        db.session.commit()

        return jsonify({
            "message": "Form type created successfully.",
            "id": form_type.id,
            "name": form_type.name,
            # "display_name": form_type.display_name,
            "description": form_type.description
        }), 201

    except Exception as e:
        db.session.rollback()
        print(f"❌ Error in create_form_type: {e}")
        return jsonify({"error": str(e)}), 500

@form_entries_blueprint.route("/form_types", methods=["GET"])
def get_all_form_types():
    """Fetch all form types"""
    try:
        form_types = FormType.query.order_by(FormType.id.desc()).all()
        results = [
            {
                "id": ft.id,
                "name": ft.name,
                # "display_name": ft.display_name,
                "description": ft.description,
                "created_at": ft.created_at.isoformat() if ft.created_at else None
            }
            for ft in form_types
        ]
        return jsonify({
            "total": len(results),
            "form_types": results
        }), 200

    except Exception as e:
        print(f"❌ Error in get_all_form_types: {e}")
        return jsonify({"error": str(e)}), 500
    
@form_entries_blueprint.route("/form_types/<int:type_id>", methods=["GET"])
def get_form_type_by_id(type_id):
    """Fetch a single form type by ID"""
    try:
        form_type = FormType.query.get(type_id)
        if not form_type:
            return jsonify({"error": "Form type not found"}), 404

        return jsonify({
            "id": form_type.id,
            "name": form_type.name,
            # "display_name": form_type.display_name,
            "description": form_type.description,
            "created_at": form_type.created_at.isoformat() if form_type.created_at else None
        }), 200

    except Exception as e:
        print(f"❌ Error in get_form_type_by_id: {e}")
        return jsonify({"error": str(e)}), 500

@form_entries_blueprint.route("/form_types/<int:type_id>", methods=["PUT"])
def update_form_type(type_id):
    """Update a form type"""
    try:
        data = request.get_json() or {}
        form_type = FormType.query.get(type_id)

        if not form_type:
            return jsonify({"error": "Form type not found"}), 404

        new_name = data.get("name")
        if new_name and new_name != form_type.name:
            if FormType.query.filter_by(name=new_name).first():
                return jsonify({"error": "Form type name already exists"}), 409
            form_type.name = new_name

        form_type.display_name = data.get("display_name", form_type.display_name)
        form_type.description = data.get("description", form_type.description)

        db.session.commit()

        return jsonify({
            "message": "Form type updated successfully.",
            "id": form_type.id,
            "name": form_type.name,
            "display_name": form_type.display_name,
            "description": form_type.description
        }), 200

    except Exception as e:
        db.session.rollback()
        print(f"❌ Error in update_form_type: {e}")
        return jsonify({"error": str(e)}), 500

@form_entries_blueprint.route("/form_types/<int:type_id>", methods=["DELETE"])
def delete_form_type(type_id):
    """Delete a form type"""
    try:
        form_type = FormType.query.get(type_id)
        if not form_type:
            return jsonify({"error": "Form type not found"}), 404

        db.session.delete(form_type)
        db.session.commit()

        return jsonify({"message": "Form type deleted successfully."}), 200

    except Exception as e:
        db.session.rollback()
        print(f"❌ Error in delete_form_type: {e}")
        return jsonify({"error": str(e)}), 500
