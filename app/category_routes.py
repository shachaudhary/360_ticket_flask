from flask import Blueprint, request, jsonify
from app import db
from app.model import Category, ContactFormSubmission, Ticket
from app.utils.helper_function import get_user_info_by_id
from app.dashboard_routes import require_api_key, validate_token
from datetime import datetime, timedelta
from app import llm_client
import threading
category_bp = Blueprint("category_bp", __name__)

# ───────────────────────────────
# Get all categories (default: only active)
@category_bp.route("/category", methods=["GET"])
@require_api_key
@validate_token
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
@require_api_key
@validate_token
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
@require_api_key
@validate_token
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
@require_api_key
@validate_token
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
@require_api_key
@validate_token
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
@require_api_key
@validate_token
def delete_category(category_id):
    category = Category.query.get(category_id)
    if not category:
        return jsonify({"error": "Category not found"}), 404
    db.session.delete(category)
    db.session.commit()
    return jsonify({"success": True, "message": "Category deleted"})


# ================================= Contact Form Submission Routes =====================================================
# @category_bp.route("/contact/submit", methods=["POST"])
# def submit_contact_form():
#     try:
#         data = request.get_json(force=True)

#         # ✅ Validate required fields
#         required_fields = ["clinic_id"]
#         missing = [f for f in required_fields if not data.get(f)]
#         if missing:
#             return jsonify({
#                 "status": "error",
#                 "message": f"Missing required field(s): {', '.join(missing)}"
#             }), 400

#         # 🧩 Combine first + last name safely
#         first_name = (data.get("first_name") or "").strip()
#         last_name = (data.get("last_name") or "").strip()
#         full_name = f"{first_name} {last_name}".strip() if first_name or last_name else data.get("name")

#         # ✅ Create new record
#         form_entry = ContactFormSubmission(
#             clinic_id=data.get("clinic_id"),
#             form_name="Contact Us",  # consistent with your default
#             name=full_name,
#             phone=data.get("phone"),
#             email=data.get("email"),
#             message=data.get("message"),
#             data=data.get("data"),
#             status=data.get("status", "pending"),
#             created_at=datetime.utcnow()
#         )

#         db.session.add(form_entry)
#         db.session.commit()

#         return jsonify({
#             "status": "success",
#             "message": "Contact form submitted successfully.",
#             "form_id": form_entry.id,
#             "full_name": full_name
#         }), 201

#     except Exception as e:
#         db.session.rollback()
#         print("❌ Error submitting contact form:", e)
#         return jsonify({
#             "status": "error",
#             "message": str(e)
#         }), 500
import re, json
# --- helper to analyze message category ---
def analyze_message_category(app, form_id, message_text):
    """Background LLM analysis run inside Flask app context and auto-create ticket."""
    with app.app_context():
        try:
            print(f"🧠 Starting category analysis for form_id={form_id}")

            # 1️⃣ Fetch active categories
            categories = [c.name for c in Category.query.filter_by(is_active=True).all()]
            if not categories:
                print("⚠️ No categories found in DB.")
                return

            # 2️⃣ Build LLM prompt
            system_prompt = (
                "You are an AI assistant that classifies dental contact form messages "
                "into one of the available categories. Respond only with the category name."
            )
            user_prompt = f"""
            Available categories: {', '.join(categories)}
            Message: "{message_text}"
            Which category best fits this message?
            """

            # 3️⃣ Call LLM
            response = llm_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.2,
            )

            raw_content = response.choices[0].message.content.strip()

            # 4️⃣ Trim LLM response safely
            match = re.search(
                r"<\|start\|>assistant<\|channel\|>final<\|message\|>(.*)",
                raw_content,
                re.DOTALL
            )
            final_message = match.group(1).strip() if match else raw_content.strip()

            category_result = final_message.split("\n")[0].strip()
            print(f"✅ LLM predicted category: {category_result}")

            # 5️⃣ Save to DB (store as JSON/text safely)
            form = ContactFormSubmission.query.get(form_id)
            if form:
                form.data = json.dumps({"predicted_category": category_result})
                db.session.commit()

                # 6️⃣ Find matching category
                matched_category = Category.query.filter(
                    Category.name.ilike(category_result)
                ).first()

                # 7️⃣ Create ticket automatically
                ticket = Ticket(
                    clinic_id=form.clinic_id,
                    title=f"Contact Form: {form.name or 'Unknown'}",
                    details=form.message or "(no message)",
                    category_id=matched_category.id if matched_category else None,
                    status="Pending",
                    priority="Medium",
                    due_date=None,
                    user_id=None,         # you can assign system user id here
                    location_id=None,     # optional if not available
                )
                db.session.add(ticket)
                db.session.commit()

                print(f"🎫 Auto Ticket Created → ID={ticket.id} | Category={category_result}")

                # (Optional) if category has assignee → auto assign ticket
                if matched_category and matched_category.assignee_id:
                    from app.ticket_routes import TicketAssignment, get_user_info_by_id, send_assign_email, create_notification
                    
                    assignee_info = get_user_info_by_id(matched_category.assignee_id)
                    if assignee_info:
                        assignment = TicketAssignment(
                            ticket_id=ticket.id,
                            assign_to=matched_category.assignee_id,
                            assign_by=None  # system
                        )
                        db.session.add(assignment)
                        db.session.commit()

                        # send assign email
                        send_assign_email(ticket, assignee_info, {"username": "System"})
                        create_notification(
                            ticket_id=ticket.id,
                            receiver_id=matched_category.assignee_id,
                            sender_id=None,
                            notification_type="assign",
                            message=f"Auto-assigned to you for category {category_result}"
                        )

        except Exception as e:
            print(f"❌ Error in analyze_message_category: {e}")


# --- Contact Form Submit Endpoint ---
@category_bp.route("/contact/submit", methods=["POST"])
def submit_contact_form():
    try:
        data = request.get_json(force=True)

        # ✅ Validate required fields
        required_fields = ["clinic_id"]
        missing = [f for f in required_fields if not data.get(f)]
        if missing:
            return jsonify({
                "status": "error",
                "message": f"Missing required field(s): {', '.join(missing)}"
            }), 400

        # 🧩 Combine first + last name safely
        first_name = (data.get("first_name") or "").strip()
        last_name = (data.get("last_name") or "").strip()
        full_name = f"{first_name} {last_name}".strip() if first_name or last_name else data.get("name")

        # ✅ Save record
        form_entry = ContactFormSubmission(
            clinic_id=data.get("clinic_id"),
            form_name="Contact Us",
            name=full_name,
            phone=data.get("phone"),
            email=data.get("email"),
            message=data.get("message"),
            data=data.get("data"),
            status=data.get("status", "pending"),
            created_at=datetime.utcnow()
        )

        db.session.add(form_entry)
        db.session.commit()

        # ✅ Start analysis thread with app context
        from flask import current_app
        app = current_app._get_current_object()

        threading.Thread(
            target=analyze_message_category,
            args=(app, form_entry.id, data.get("message")),
            daemon=True
        ).start()

        return jsonify({
            "status": "success",
            "message": "Contact form submitted successfully.",
            "form_id": form_entry.id,
            "full_name": full_name
        }), 201

    except Exception as e:
        db.session.rollback()
        print("❌ Error submitting contact form:", e)
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500
    
@category_bp.route("/contact/get_all", methods=["GET"])
def get_all_contact_forms():
    try:
        # 🔹 Required or optional clinic_id
        clinic_id = request.args.get("clinic_id", type=int)
        if not clinic_id:
            return jsonify({
                "status": "error",
                "message": "clinic_id is required."
            }), 400

        # 🔹 Pagination params
        page = request.args.get("page", default=1, type=int)
        per_page = request.args.get("per_page", default=10, type=int)
        per_page = min(max(per_page, 1), 200)  # safe bounds

        # 🔹 Optional search by name
        search = request.args.get("search", "", type=str).strip()

        query = ContactFormSubmission.query.filter_by(clinic_id=clinic_id)

        if search:
            query = query.filter(ContactFormSubmission.name.ilike(f"%{search}%"))

        # 🔹 Paginate results
        pagination = query.order_by(ContactFormSubmission.created_at.desc()).paginate(
            page=page, per_page=per_page, error_out=False
        )

        # 🔹 Serialize results with name split
        forms_data = []
        for form in pagination.items:
            # Split name safely
            first_name, last_name = None, None
            if form.name:
                name_parts = form.name.strip().split(" ", 1)
                first_name = name_parts[0]
                if len(name_parts) > 1:
                    last_name = name_parts[1]

            forms_data.append({
                "id": form.id,
                "clinic_id": form.clinic_id,
                "form_name": form.form_name,
                "name": form.name,
                "first_name": first_name,
                "last_name": last_name,
                "phone": form.phone,
                "email": form.email,
                "message": form.message,
                "data": form.data,
                "status": form.status,
                "created_at": form.created_at.strftime("%Y-%m-%d %H:%M:%S")
            })

        return jsonify({
            "status": "success",
            "message": "Forms retrieved successfully.",
            "clinic_id": clinic_id,
            "page": page,
            "per_page": per_page,
            "total": pagination.total,
            "has_next": pagination.has_next,
            "has_prev": pagination.has_prev,
            "forms": forms_data
        }), 200

    except Exception as e:
        print("❌ Error fetching contact forms:", e)
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500


@category_bp.route("/contact/get_by_id/<int:id>", methods=["GET"])
def get_contact_form_by_id(id):
    try:
        # 🔹 Fetch record by ID
        form = ContactFormSubmission.query.get(id)

        if not form:
            return jsonify({
                "status": "error",
                "message": f"Contact form with ID {id} not found."
            }), 404

        # 🔹 Split name into first and last
        first_name, last_name = None, None
        if form.name:
            name_parts = form.name.strip().split(" ", 1)
            first_name = name_parts[0]
            if len(name_parts) > 1:
                last_name = name_parts[1]

        # 🔹 Serialize result
        form_data = {
            "id": form.id,
            "clinic_id": form.clinic_id,
            "form_name": form.form_name,
            "name": form.name,
            "first_name": first_name,
            "last_name": last_name,
            "phone": form.phone,
            "email": form.email,
            "message": form.message,
            "data": form.data,
            "status": form.status,
            "created_at": form.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        }

        return jsonify({
            "status": "success",
            "message": "Contact form retrieved successfully.",
            "form": form_data
        }), 200

    except Exception as e:
        print(f"❌ Error fetching contact form ID={id}:", e)
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500
