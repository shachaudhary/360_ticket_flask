from flask import Blueprint, request, jsonify, g
from app import db
from app.model import Category, ContactFormSubmission, Ticket, ContactFormTicketLink, TicketAssignment, TicketAssignmentLog,TicketFile,TicketComment,TicketStatusLog,TicketTag, TicketFollowUp
from app.utils.helper_function import get_user_info_by_id
from app.utils.email_templete import send_email
from app.dashboard_routes import require_api_key, validate_token
from datetime import datetime, timedelta
from app import llm_client
import threading
category_bp = Blueprint("category_bp", __name__)
AUTH_SYSTEM_URL = "https://api.dental360grp.com/api"

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
def analyze_message_category(app, form_id, message_text, location_id=None, postal_code=None):
    """Background LLM analysis run inside Flask app context and auto-create ticket + link to contact form."""
    with app.app_context():
        try:
            print(f":brain: Starting category analysis for form_id={form_id}")
            # :one: Fetch active categories
            categories = [c.name for c in Category.query.filter_by(is_active=True).all()]
            if not categories:
                print(":warning: No categories found in DB.")
                return
            # :two: Build prompt
            system_prompt = (
                "You are an AI assistant that classifies dental contact form messages "
                "into one of the available categories. Respond only with the category name."
            )
            user_prompt = f"""
            Available categories: {', '.join(categories)}
            Message: "{message_text}"
            Which category best fits this message?
            """
            # :three: Call LLM
            response = llm_client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.2,
            )
            raw_content = response.choices[0].message.content.strip()
            # :four: Extract final category name
            match = re.search(
                r"<\|start\|>assistant<\|channel\|>final<\|message\|>(.*)",
                raw_content,
                re.DOTALL
            )
            final_message = match.group(1).strip() if match else raw_content.strip()
            category_result = final_message.split("\n")[0].strip()
            print(f":white_check_mark: LLM predicted category: {category_result}")
            # :five: Save category result to ContactFormSubmission
            form = ContactFormSubmission.query.get(form_id)
            if not form:
                print(f":warning: Form ID {form_id} not found.")
                return
            form.data = json.dumps({"predicted_category": category_result})
            db.session.commit()
            # :six: Find category
            matched_category = Category.query.filter(
                Category.name.ilike(category_result)
            ).first()
            
            # :location: Determine location_id
            # Use form's location_id if already set, otherwise use passed location_id
            final_location_id = form.location_id or location_id
            if not final_location_id and postal_code:
                # Analyze by postal code
                try:
                    url = f"{AUTH_SYSTEM_URL}/clinic_locations/get_all/{form.clinic_id}"
                    resp = requests.get(url, timeout=10)
                    if resp.status_code == 200:
                        locations_data = resp.json()
                        locations = locations_data.get("locations", [])
                        # Match by postal code
                        for loc in locations:
                            if loc.get("postal_code") == postal_code:
                                final_location_id = loc.get("id")
                                print(f":location: Matched location_id={final_location_id} by postal_code={postal_code}")
                                break
                        if not final_location_id:
                            print(f":warning: No location found matching postal_code={postal_code}")
                    else:
                        print(f":warning: Failed to fetch locations: {resp.status_code}")
                except Exception as e:
                    print(f":warning: Error analyzing location by postal code: {e}")
            
            # Update location_id in ContactFormSubmission if determined from postal code
            if final_location_id and final_location_id != form.location_id:
                form.location_id = final_location_id
                db.session.commit()
                print(f":location: Updated ContactFormSubmission ID={form.id} with location_id={final_location_id}")
            
            # Check if category is spam - skip ticket creation if it is
            if category_result and category_result.lower().strip() == "spam":
                print(f":no_entry: Category is 'spam' - skipping ticket creation for form ID={form.id}")
                return
            
            # :seven: Create new Ticket
            ticket = Ticket(
                clinic_id=form.clinic_id,
                title=f"Contact Form: {form.name or 'Unknown'}",
                details=form.message or "(no message)",
                category_id=matched_category.id if matched_category else None,
                status="Pending",
                priority="low",
                due_date=None,
                user_id=None,
                location_id=final_location_id,
            )
            db.session.add(ticket)
            db.session.commit()
            print(f":ticket: Auto Ticket Created → ID={ticket.id} | Category={category_result}")
            # :eight: Store link between ContactForm and Ticket
            link = ContactFormTicketLink(
                contact_form_id=form.id,
                ticket_id=ticket.id
            )
            db.session.add(link)
            db.session.commit()
            print(f":link: Linked ContactForm ID={form.id} with Ticket ID={ticket.id}")
            # :nine: Optional: auto-assign if category has assignee
            if matched_category and matched_category.assignee_id:
                from app.ticket_routes import TicketAssignment, get_user_info_by_id, send_assign_email, create_notification
                assignee_info = get_user_info_by_id(matched_category.assignee_id)
                if assignee_info:
                    assignment = TicketAssignment(
                        ticket_id=ticket.id,
                        assign_to=matched_category.assignee_id,
                        assign_by=None  # System-generated
                    )
                    db.session.add(assignment)
                    db.session.commit()
                    send_assign_email(ticket, assignee_info, {"username": "System"})
                    create_notification(
                        ticket_id=ticket.id,
                        receiver_id=matched_category.assignee_id,
                        sender_id=None,
                        notification_type="assign",
                        message=f"Auto-assigned to you for category {category_result}"
                    )
        except Exception as e:
            print(f":x: Error in analyze_message_category: {e}")


import threading, requests
from flask import current_app
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

        # ✅ Extract location_id and postal_code
        location_id = data.get("location_id")
        postal_code = None
        
        # Extract postal_code from data field if location_id not provided
        if not location_id:
            data_field = data.get("data")
            if data_field:
                if isinstance(data_field, str):
                    try:
                        data_dict = json.loads(data_field)
                        postal_code = data_dict.get("postal_code") if isinstance(data_dict, dict) else None
                    except Exception:
                        pass
                elif isinstance(data_field, dict):
                    postal_code = data_field.get("postal_code")

        # ✅ Save record locally
        form_entry = ContactFormSubmission(
            clinic_id=data.get("clinic_id"),
            location_id=location_id,
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

        # ✅ Capture app and headers before leaving request context
        from flask import current_app
        app = current_app._get_current_object()

        headers = {
            "x-api-key": request.headers.get("x-api-key", ""),
            "Authorization": request.headers.get("Authorization", ""),
            "Content-Type": "application/json"
        }

        # -----------------------------
        # 🧠 Thread 1 → Analyze category
        # -----------------------------
        threading.Thread(
            target=analyze_message_category,
            args=(app, form_entry.id, data.get("message"), location_id, postal_code),
            daemon=True
        ).start()

        # -----------------------------
        # 🧩 Thread 2 → Create patient in AUTH SYSTEM
        # -----------------------------
        def create_patient_in_auth(app, form_data, headers):
            with app.app_context():
                try:
                    # ✅ Safely normalize data field
                    form_data_dict = {}
                    if form_data.data:
                        if isinstance(form_data.data, str):
                            try:
                                form_data_dict = json.loads(form_data.data)
                                if not isinstance(form_data_dict, dict):
                                    form_data_dict = {}
                            except Exception:
                                form_data_dict = {}
                        elif isinstance(form_data.data, dict):
                            form_data_dict = form_data.data

                    # ✅ Build payload safely
                    payload = {
                        "name": form_data.name,
                        "phone": form_data.phone,
                        "email": form_data.email,
                        "clinic_id": form_data.clinic_id,
                        "address": form_data_dict.get("address"),
                        "state": form_data_dict.get("state"),
                        "postal_code": form_data_dict.get("postal_code"),
                        "insurance_name": form_data_dict.get("insurance_name"),
                        "insurance_no": form_data_dict.get("insurance_no"),
                    }

                    url = f"{AUTH_SYSTEM_URL}/patient"
                    print(f"🌐 Sending patient creation payload: {payload}")
                    resp = requests.post(url, json=payload, headers=headers, timeout=10)

                    if resp.status_code in (200, 201):
                        print(f"✅ Patient created successfully in Auth API → {form_data.name}")
                    else:
                        print(f"⚠️ Patient creation failed ({resp.status_code}): {resp.text}")
                except Exception as ex:
                    print(f"❌ Error creating patient in Auth System: {ex}")


        threading.Thread(
            target=create_patient_in_auth,
            args=(app, form_entry, headers),
            daemon=True
        ).start()

        # ✅ Return immediate response
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

        # 🔹 Serialize results with name split and category extraction
        forms_data = []
        for form in pagination.items:
            # Split name safely
            first_name, last_name = None, None
            if form.name:
                name_parts = form.name.strip().split(" ", 1)
                first_name = name_parts[0]
                if len(name_parts) > 1:
                    last_name = name_parts[1]

            # Extract predicted_category from data field
            predicted_category = None
            data_field = form.data
            try:
                if isinstance(data_field, str):
                    data_field = json.loads(data_field)
                if isinstance(data_field, dict) and "predicted_category" in data_field:
                    predicted_category = data_field.get("predicted_category")
            except Exception:
                pass

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
                "data": data_field,
                "predicted_category": predicted_category,
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

def _serialize_ticket(ticket):
    created_by = get_user_info_by_id(ticket.user_id) if ticket.user_id else None

    # Assignments (current)
    assignments = TicketAssignment.query.filter_by(ticket_id=ticket.id).all()
    assignees = []
    for a in assignments:
        assign_by_info = get_user_info_by_id(a.assign_by) if a.assign_by else None
        assign_to_info = get_user_info_by_id(a.assign_to) if a.assign_to else None
        assignees.append({
            "assign_by": a.assign_by,
            "assign_by_username": assign_by_info["username"] if assign_by_info else None,
            "assign_to": a.assign_to,
            "assign_to_username": assign_to_info["username"] if assign_to_info else None,
            "assigned_at": a.assigned_at
        })

    # Assignment logs (history)
    assignment_logs = []
    for log in TicketAssignmentLog.query.filter_by(ticket_id=ticket.id).order_by(TicketAssignmentLog.changed_at.desc()).all():
        old_user_info = get_user_info_by_id(log.old_assign_to) if log.old_assign_to else None
        new_user_info = get_user_info_by_id(log.new_assign_to) if log.new_assign_to else None
        changed_by_info = get_user_info_by_id(log.changed_by) if log.changed_by else None
        assignment_logs.append({
            "old_assign_to": log.old_assign_to,
            "old_assign_to_username": old_user_info["username"] if old_user_info else None,
            "new_assign_to": log.new_assign_to,
            "new_assign_to_username": new_user_info["username"] if new_user_info else None,
            "changed_by": log.changed_by,
            "changed_by_username": changed_by_info["username"] if changed_by_info else None,
            "changed_at": log.changed_at
        })

    # Files, Tags, Comments, Followups
    files = [{"name": f.file_name, "url": f.file_url}
             for f in TicketFile.query.filter_by(ticket_id=ticket.id).all()]

    tags = [tag.tag_name for tag in TicketTag.query.filter_by(ticket_id=ticket.id).all()]

    comments = []
    for c in TicketComment.query.filter_by(ticket_id=ticket.id).order_by(TicketComment.created_at.desc()).all():
        u_info = get_user_info_by_id(c.user_id) if c.user_id else None
        comments.append({
            "user_id": c.user_id,
            "username": u_info["username"] if u_info else None,
            "comment": c.comment,
            "created_at": c.created_at
        })

    followups = []
    for f in TicketFollowUp.query.filter_by(ticket_id=ticket.id).all():
        u_info = get_user_info_by_id(f.user_id)
        followups.append({
            "id": f.id,
            "note": f.note,
            "user_id": f.user_id,
            "username": u_info["username"] if u_info else None,
            "followup_date": f.followup_date,
            "created_at": f.created_at
        })

    # Category
    category = None
    if getattr(ticket, "category_id", None):
        cat = Category.query.get(ticket.category_id)
        if cat:
            category = {"id": cat.id, "name": cat.name, "is_active": cat.is_active}

    # Status logs
    status_logs = []
    for log in TicketStatusLog.query.filter_by(ticket_id=ticket.id).order_by(TicketStatusLog.changed_at.desc()).all():
        u_info = get_user_info_by_id(log.changed_by) if log.changed_by else None
        status_logs.append({
            "old_status": log.old_status,
            "new_status": log.new_status,
            "changed_by": log.changed_by,
            "changed_by_username": u_info["username"] if u_info else None,
            "changed_at": log.changed_at
        })

    return {
        "id": ticket.id,
        "title": ticket.title,
        "details": ticket.details,
        "priority": ticket.priority,
        "status": ticket.status,
        "due_date": ticket.due_date,
        "created_at": ticket.created_at,
        "completed_at": ticket.completed_at,
        "created_by": created_by,
        "assignees": assignees,
        "assignment_logs": assignment_logs,
        "files": files,
        "tags": tags,
        "comments": comments,
        "followups": followups,
        "category": category,
        "status_logs": status_logs
    }

@category_bp.route("/contact/get_by_id/<int:id>", methods=["GET"])
def get_contact_form_by_id(id):
    try:
        form = ContactFormSubmission.query.get(id)
        if not form:
            return jsonify({"status": "error", "message": f"Contact form with ID {id} not found."}), 404

        # split name
        first_name, last_name = None, None
        if form.name:
            parts = form.name.strip().split(" ", 1)
            first_name = parts[0]
            if len(parts) > 1:
                last_name = parts[1]

        # parse 'data' if it's JSON text
        data_field = form.data
        predicted_category = None
        try:
            if isinstance(data_field, str):
                data_field = json.loads(data_field)
            # Extract predicted_category if it exists
            if isinstance(data_field, dict) and "predicted_category" in data_field:
                predicted_category = data_field.get("predicted_category")
        except Exception:
            pass

        # --- fetch linked ticket ids
        links = ContactFormTicketLink.query.filter_by(contact_form_id=form.id).all()
        ticket_ids = [l.ticket_id for l in links]

        # --- load tickets & serialize
        tickets = []
        if ticket_ids:
            for t in Ticket.query.filter(Ticket.id.in_(ticket_ids)).all():
                tickets.append(_serialize_ticket(t))

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
            "data": data_field,
            "predicted_category": predicted_category,
            "status": form.status,
            "assigned_to": getattr(form, "assigned_to", None),
            "created_at": form.created_at.strftime("%Y-%m-%d %H:%M:%S"),
            "ticket_links": [{"ticket_id": tid} for tid in ticket_ids],  # explicit ids
            "tickets": tickets                                        # full ticket payloads
        }

        return jsonify({
            "status": "success",
            "message": "Contact form retrieved successfully.",
            "form": form_data
        }), 200

    except Exception as e:
        print(f"❌ Error fetching contact form ID={id}:", e)
        return jsonify({"status": "error", "message": str(e)}), 500

@category_bp.route("/contact/update_category/<int:id>", methods=["PUT"])
def update_contact_category(id):
    """
    Update the category in the data field of a contact form.
    Request body:
    {
        "category": "IT"  // or "Appointment", "Billing", etc.
    }
    Example: PUT /api/contact/update_category/123
    {
        "category": "IT"
    }
    """
    try:
        # Get the contact form
        form = ContactFormSubmission.query.get(id)
        if not form:
            return jsonify({
                "status": "error",
                "message": f"Contact form with ID {id} not found."
            }), 404
        # Get category from request
        data = request.get_json()
        if not data or "category" not in data:
            return jsonify({
                "status": "error",
                "message": "Category field is required in request body."
            }), 400
        new_category = data["category"]
        # Parse existing data field
        data_field = form.data
        try:
            if isinstance(data_field, str):
                data_field = json.loads(data_field)
            elif data_field is None:
                data_field = {}
        except Exception:
            data_field = {}
        # Ensure data_field is a dict
        if not isinstance(data_field, dict):
            data_field = {}
        # Update the predicted_category
        old_category = data_field.get("predicted_category")
        data_field["predicted_category"] = new_category
        # Save back to database
        form.data = json.dumps(data_field)
        db.session.commit()

        # Get updater info from g.user (set by validate_token) or use System
        updater_info = getattr(g, "user", None)
        updater_name = updater_info.get("username") if updater_info and isinstance(updater_info, dict) else "System"

        # Find the Category by name and get its assignee
        matched_category = Category.query.filter(
            Category.name.ilike(new_category)
        ).first()

        # Send email to assigned user if category exists and has an assignee
        if matched_category and matched_category.assignee_id:
            assignee_info = get_user_info_by_id(matched_category.assignee_id)
            if assignee_info and assignee_info.get("email"):
                subject = f"Dental360 Contact Form Category Updated"
                
                # Plain text fallback
                body_text = (
                    f"Hello {assignee_info['username']},\n\n"
                    f"A contact form's category has been updated and assigned to your category.\n\n"
                    f"Contact Form ID: {form.id}\n"
                    f"Contact Name: {form.name or 'Unknown'}\n"
                    f"Old Category: {old_category or 'None'}\n"
                    f"New Category: {new_category}\n"
                    f"Updated By: {updater_name}\n\n"
                    f"Please log in to the Dental360 system to review the contact form.\n\n"
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
                SUPPORT 360 - Contact Form Category Updated
            </div>
            <div style="padding:30px;">
                <p style="line-height:1.6; margin-bottom:15px;">Hello <strong>{assignee_info['username']}</strong>,</p>
                <p style="line-height:1.6; margin-bottom:15px;">A contact form's category has been updated and assigned to your category (<strong>{new_category}</strong>).</p>

                <table cellpadding="0" cellspacing="0" style="width:100%; border-collapse:collapse; margin-top:20px; margin-bottom:25px; border:1px solid #e0e0e0; border-radius:6px; overflow:hidden;">
                    <tr style="background:#f9f9fb;">
                        <td width="30%" style="padding:12px 15px; text-align:left; border-bottom:1px solid #eee; font-size:14px;"><strong>Contact Form ID:</strong></td>
                        <td style="padding:12px 15px; text-align:left; border-bottom:1px solid #eee; font-size:14px;">{form.id}</td>
                    </tr>
                    <tr>
                        <td style="padding:12px 15px; text-align:left; border-bottom:1px solid #eee; font-size:14px;"><strong>Contact Name:</strong></td>
                        <td style="padding:12px 15px; text-align:left; border-bottom:1px solid #eee; font-size:14px;">{form.name or 'Unknown'}</td>
                    </tr>
                    <tr style="background:#f9f9fb;">
                        <td style="padding:12px 15px; text-align:left; border-bottom:1px solid #eee; font-size:14px;"><strong>Old Category:</strong></td>
                        <td style="padding:12px 15px; text-align:left; border-bottom:1px solid #eee; font-size:14px;">{old_category or 'None'}</td>
                    </tr>
                    <tr>
                        <td style="padding:12px 15px; text-align:left; border-bottom:1px solid #eee; font-size:14px;"><strong>New Category:</strong></td>
                        <td style="padding:12px 15px; text-align:left; border-bottom:1px solid #eee; font-size:14px;"><span style="color:#28a745; font-weight:bold;">{new_category}</span></td>
                    </tr>
                    <tr style="background:#f9f9fb;">
                        <td style="padding:12px 15px; text-align:left; border-bottom:1px solid #eee; font-size:14px;"><strong>Updated By:</strong></td>
                        <td style="padding:12px 15px; text-align:left; border-bottom:1px solid #eee; font-size:14px;">{updater_name}</td>
                    </tr>
                </table>

                <p style="line-height:1.6; margin-bottom:15px; margin-top:25px;">You can log in to the Support 360 Portal to review the contact form.</p>
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

                print(f"📧 Sending category update email → {assignee_info['email']} | Contact Form #{form.id}")
                threading.Thread(
                    target=send_email,
                    args=(assignee_info["email"], subject, body_html, body_text)
                ).start()

        return jsonify({
            "status": "success",
            "message": f"Category updated successfully from '{old_category}' to '{new_category}'",
            "id": form.id,
            "old_category": old_category,
            "new_category": new_category,
            "data": data_field
        }), 200
    except Exception as e:
        db.session.rollback()
        print(f":x: Error updating category for form ID={id}:", e)
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500