from flask import Blueprint, request, jsonify
from datetime import datetime
from app import db
from app.model import (
    FormEntry,
    FormFieldValue,
    FormEmailRecipient,
    FormType,
    FormTypeUserNoti,
    FormEmailLog
)
from app.utils.email_templete import (
    send_email,
    get_user_info_by_id,
    generate_email_template
)
from app.dashboard_routes import require_api_key, validate_token


# =====================================
# 🔹 Blueprint Registration
# =====================================
form_types_blueprint = Blueprint("form_types", __name__)


# =====================================
# 🟢 CREATE FORM TYPE + USER MAP
# =====================================
@form_types_blueprint.route("/form_types", methods=["POST"])
def create_form_type():
    """Create a new form type with optional user mappings."""
    try:
        data = request.get_json() or {}
        name = data.get("name")
        description = data.get("description")
        user_id = data.get("user_id")  # owner
        clinic_id = data.get("clinic_id")
        location_id = data.get("location_id")
        user_ids = data.get("user_ids", [])  # notification users

        if not name:
            return jsonify({"error": "name is required"}), 400

        if FormType.query.filter_by(name=name).first():
            return jsonify({"error": "Form type already exists"}), 409

        # Create new FormType
        form_type = FormType(
            name=name,
            description=description,
            user_id=user_id,
            clinic_id=clinic_id,
            location_id=location_id
        )
        db.session.add(form_type)
        db.session.commit()

        # Add notification user mappings
        for uid in user_ids:
            db.session.add(FormTypeUserNoti(form_type_id=form_type.id, user_id=uid))
        db.session.commit()

        return jsonify({
            "message": "Form type created successfully.",
            "id": form_type.id,
            "name": form_type.name,
            "description": form_type.description,
            "user_id": user_id,
            "clinic_id": clinic_id,
            "location_id": location_id,
            "user_ids": user_ids
        }), 201

    except Exception as e:
        db.session.rollback()
        print(f"❌ Error in create_form_type: {e}")
        return jsonify({"error": str(e)}), 500


# =====================================
# 🔵 GET ALL FORM TYPES (with users)
# =====================================
@form_types_blueprint.route("/form_types", methods=["GET"])
def get_all_form_types():
    try:
        # 🔹 Query params for pagination
        page = request.args.get("page", 1, type=int)
        per_page = request.args.get("per_page", 10, type=int)

        # 🔹 Query params for filtering
        clinic_id = request.args.get("clinic_id", type=int)
        location_id = request.args.get("location_id", type=int)
        name = request.args.get("name", type=str)

        # 🔹 Base query
        query = FormType.query

        if clinic_id:
            query = query.filter_by(clinic_id=clinic_id)
        if location_id:
            query = query.filter_by(location_id=location_id)
        if name:
            query = query.filter(FormType.name.ilike(f"%{name}%"))

        # 🔹 Order by latest
        query = query.order_by(FormType.id.desc())

        # 🔹 Pagination
        paginated = query.paginate(page=page, per_page=per_page, error_out=False)
        form_types = paginated.items

        results = []
        for ft in form_types:
            mappings = FormTypeUserNoti.query.filter_by(form_type_id=ft.id).all()
            user_ids = [m.user_id for m in mappings]
            users = [get_user_info_by_id(uid) for uid in user_ids if uid]
            owner = get_user_info_by_id(ft.user_id) if ft.user_id else None

            results.append({
                "id": ft.id,
                "name": ft.name,
                "description": ft.description,
                "user_id": ft.user_id,
                "owner": owner,
                "clinic_id": ft.clinic_id,
                "location_id": ft.location_id,
                "users": users,
                "created_at": ft.created_at.isoformat() if ft.created_at else None
            })

        return jsonify({
            "total": paginated.total,
            "page": paginated.page,
            "per_page": paginated.per_page,
            "pages": paginated.pages,
            "form_types": results
        }), 200

    except Exception as e:
        print(f"❌ Error in get_all_form_types: {e}")
        return jsonify({"error": str(e)}), 500


# =====================================
# 🔵 GET FORM TYPE BY ID (with users)
# =====================================
@form_types_blueprint.route("/form_types/<int:type_id>", methods=["GET"])
def get_form_type_by_id(type_id):
    """Fetch a single form type with assigned users and owner info."""
    try:
        ft = FormType.query.get(type_id)
        if not ft:
            return jsonify({"error": "Form type not found"}), 404

        # Notification user mappings
        mappings = FormTypeUserNoti.query.filter_by(form_type_id=ft.id).all()
        user_ids = [m.user_id for m in mappings]
        users = [get_user_info_by_id(uid) for uid in user_ids if uid]
        owner_info = get_user_info_by_id(ft.user_id) if ft.user_id else None

        return jsonify({
            "id": ft.id,
            "name": ft.name,
            "description": ft.description,
            "user_id": ft.user_id,
            "owner": owner_info,
            "clinic_id": ft.clinic_id,
            "location_id": ft.location_id,
            # "user_ids": user_ids,
            "users": users,
            "created_at": ft.created_at.isoformat() if ft.created_at else None
        }), 200

    except Exception as e:
        print(f"❌ Error in get_form_type_by_id: {e}")
        return jsonify({"error": str(e)}), 500


# =====================================
# 🟠 UPDATE FORM TYPE + USERS
# =====================================
@form_types_blueprint.route("/form_types/<int:type_id>", methods=["PUT"])
def update_form_type(type_id):
    """Update form type fields including clinic/location and user mappings."""
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
        ft.user_id = data.get("user_id", ft.user_id)
        ft.clinic_id = data.get("clinic_id", ft.clinic_id)
        ft.location_id = data.get("location_id", ft.location_id)
        db.session.commit()

        # Update notification user mappings
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
            "user_id": ft.user_id,
            "clinic_id": ft.clinic_id,
            "location_id": ft.location_id,
            # "user_ids": new_user_ids
        }), 200

    except Exception as e:
        db.session.rollback()
        print(f"❌ Error in update_form_type: {e}")
        return jsonify({"error": str(e)}), 500


# =====================================
# 🔴 DELETE FORM TYPE + USER MAPS
# =====================================
@form_types_blueprint.route("/form_types/<int:type_id>", methods=["DELETE"])
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
