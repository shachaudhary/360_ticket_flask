from flask import Blueprint, request, jsonify
from app import db
from app.model import Category
from app.utils.helper_function import get_user_info_by_id

category_bp = Blueprint("category_bp", __name__)

# ───────────────────────────────
# Get all categories (default: only active)
@category_bp.route("/category", methods=["GET"])
def get_categories():
    include_inactive = request.args.get("include_inactive", "false").lower() == "true"

    query = Category.query
    if not include_inactive:
        query = query.filter_by(is_active=True)

    categories = query.order_by(Category.created_at.desc()).all()
    result = []

    for c in categories:
        assignee_info = get_user_info_by_id(c.assignee_id) if c.assignee_id else None

        result.append({
            "id": c.id,
            "name": c.name,
            "assignee_id": c.assignee_id,
            "assignee_name": assignee_info["username"] if assignee_info else None,
            "created_at": c.created_at,
            "is_active": c.is_active
        })

    return jsonify(result)

# ───────────────────────────────
# Get single category
@category_bp.route("/category/<int:category_id>", methods=["GET"])
def get_category(category_id):
    category = Category.query.get(category_id)
    if not category:
        return jsonify({"error": "Category not found"}), 404

    assignee_info = get_user_info_by_id(category.assignee_id) if category.assignee_id else None

    return jsonify({
        "id": category.id,
        "name": category.name,
        "assignee_id": category.assignee_id,
        "assignee_name": assignee_info["username"] if assignee_info else None,
        "created_at": category.created_at,
        "is_active": category.is_active
    })

# ───────────────────────────────
# Create category
@category_bp.route("/category", methods=["POST"])
def create_category():
    data = request.get_json()
    name = data.get("name")
    assignee_id = data.get("assignee_id")
    is_active = data.get("is_active", True)

    if not name:
        return jsonify({"error": "Category name is required"}), 400

    if Category.query.filter_by(name=name).first():
        return jsonify({"error": "Category already exists"}), 400

    category = Category(name=name, assignee_id=assignee_id, is_active=is_active)
    db.session.add(category)
    db.session.commit()

    return jsonify({
        "success": True,
        "id": category.id,
        "name": category.name,
        "assignee_id": category.assignee_id,
        "is_active": category.is_active
    })

# ───────────────────────────────
# Update category
@category_bp.route("/category/<int:category_id>", methods=["PATCH"])
def update_category(category_id):
    category = Category.query.get(category_id)
    if not category:
        return jsonify({"error": "Category not found"}), 404

    data = request.get_json()

    # --- Update name
    name = data.get("name")
    if name:
        # Duplicate check
        if Category.query.filter(Category.id != category_id, Category.name == name).first():
            return jsonify({"error": "Category with this name already exists"}), 400
        category.name = name

    # --- Update assignee
    if "assignee_id" in data:
        category.assignee_id = data["assignee_id"]

    # --- Update active/inactive
    if "is_active" in data:
        category.is_active = bool(data["is_active"])

    db.session.commit()

    assignee_info = get_user_info_by_id(category.assignee_id) if category.assignee_id else None

    return jsonify({
        "success": True,
        "id": category.id,
        "name": category.name,
        "assignee_id": category.assignee_id,
        "assignee_name": assignee_info["username"] if assignee_info else None,
        "is_active": category.is_active,
        "created_at": category.created_at
    })

# ───────────────────────────────
# Enable/Disable category (quick toggle)
@category_bp.route("/category/<int:category_id>/status", methods=["PATCH"])
def toggle_category_status(category_id):
    category = Category.query.get(category_id)
    if not category:
        return jsonify({"error": "Category not found"}), 404

    data = request.get_json()
    # Default True rakhte hain agar value na mile
    category.is_active = bool(data.get("is_active", True))
    db.session.commit()

    assignee_info = get_user_info_by_id(category.assignee_id) if category.assignee_id else None

    return jsonify({
        "success": True,
        "id": category.id,
        "name": category.name,
        "assignee_id": category.assignee_id,
        "assignee_name": assignee_info["username"] if assignee_info else None,
        "is_active": category.is_active,
        "created_at": category.created_at
    })


# ───────────────────────────────
# Delete category (hard delete)
@category_bp.route("/category/<int:category_id>", methods=["DELETE"])
def delete_category(category_id):
    category = Category.query.get(category_id)
    if not category:
        return jsonify({"error": "Category not found"}), 404
    db.session.delete(category)
    db.session.commit()
    return jsonify({"success": True, "message": "Category deleted"})
