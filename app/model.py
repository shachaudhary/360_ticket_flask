from datetime import datetime
from app import db

class Ticket(db.Model):
    """
    Base Ticket information
    """
    __tablename__ = "tickets"

    id           = db.Column(db.Integer, primary_key=True)
    clinic_id    = db.Column(db.Integer)
    location_id  = db.Column(db.Integer)
    user_id      = db.Column(db.Integer)  # creator

    title        = db.Column(db.String(255))
    details      = db.Column(db.Text)
    category_id  = db.Column(db.Integer)  # e.g. HR, IT, Billing

    status       = db.Column(db.String(255), default="Pending")  # Pending | In Progress | Completed
    priority     = db.Column(db.String(255))
    due_date     = db.Column(db.Date)

    created_at   = db.Column(db.DateTime, default=datetime.utcnow)
    completed_at = db.Column(db.DateTime)    
    updated_at   = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<Ticket {self.id} - {self.title} ({self.status})>"



class TicketAssignment(db.Model):
    __tablename__ = "ticket_assignments"

    id        = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer)
    assign_by = db.Column(db.Integer)
    assign_to = db.Column(db.Integer)
    assigned_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<TicketAssignment Ticket={self.ticket_id} → assign_to={self.assign_to}>"

class TicketAssignmentLog(db.Model):
    __tablename__ = "ticket_assignment_log"

    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, nullable=False)
    old_assign_to = db.Column(db.Integer, nullable=True)
    new_assign_to = db.Column(db.Integer, nullable=False)
    changed_by = db.Column(db.Integer, nullable=False)
    changed_at = db.Column(db.DateTime, default=datetime.utcnow)


class TicketFile(db.Model):
    __tablename__ = "ticket_files"

    id         = db.Column(db.Integer, primary_key=True)
    ticket_id  = db.Column(db.Integer)
    file_url   = db.Column(db.Text)
    file_name  = db.Column(db.String(255))
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)


class TicketComment(db.Model):
    __tablename__ = "ticket_comments"

    id        = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer)
    user_id   = db.Column(db.Integer)
    comment   = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class TicketTag(db.Model):
    __tablename__ = "ticket_tags"

    id        = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer)
    tag_name  = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Category(db.Model):
    """
    Simple categories e.g. HR, IT, Billing
    """
    __tablename__ = "categories"

    id          = db.Column(db.Integer, primary_key=True)
    name        = db.Column(db.String(255), unique=True, nullable=False)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)
    assignee_id = db.Column(db.Integer)  # 👈 FK to users table
    is_active   = db.Column(db.Boolean, default=True)  # ✅ Enable/Disable flag

    def __repr__(self):
        return f"<Category {self.id} - {self.name} (Active={self.is_active})>"

    

class TicketFollowUp(db.Model):
    __tablename__ = "ticket_followups"

    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer)
    user_id = db.Column(db.Integer)   # jisne followup add kiya
    note = db.Column(db.Text, nullable=True)          # followup note
    followup_date = db.Column(db.DateTime, default=db.func.now())
    created_at = db.Column(db.DateTime, default=db.func.now())
    updated_at = db.Column(db.DateTime, default=db.func.now(), onupdate=db.func.now())

    def __repr__(self):
        return f"<TicketFollowUp {self.id} - {self.ticket_id}>"
class TicketNotification(db.Model):
    __tablename__ = "ticket_notifications"

    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer)
    receiver_id = db.Column(db.Integer)   # 👈 jis user ko notification gayi
    sender_id = db.Column(db.Integer)     # 👈 jis user ne notification trigger ki
    notification_type = db.Column(db.String(255))      # assign | tag | followup
    message = db.Column(db.Text, nullable=True)       # optional message
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<TicketNotification ticket={self.ticket_id} user={self.user_id} type={self.notification_type}>"


class TicketStatusLog(db.Model):
    __tablename__ = "ticket_status_logs"

    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, nullable=False)
    old_status = db.Column(db.String(255))
    new_status = db.Column(db.String(255))
    changed_by = db.Column(db.Integer)  # jis user ne status change kiya
    changed_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<TicketStatusLog ticket={self.ticket_id} {self.old_status} → {self.new_status}>"





class FormEntry(db.Model):
    """
    Represents a type/entries of form (linked to form_types table)
    """
    __tablename__ = "form_entries"

    id = db.Column(db.Integer, primary_key=True)
    form_type_id = db.Column(db.Integer, nullable=False)
    submitted_by_id = db.Column(db.Integer)
    clinic_id = db.Column(db.Integer)
    location_id = db.Column(db.Integer)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


    def __repr__(self):
        return f"<FormEntry {self.id} - {self.form_type.name}>"


class FormFieldValue(db.Model):
    """
    Stores individual field data (like field_name and value) for each form.
    """
    __tablename__ = "form_field_values"

    id = db.Column(db.Integer, primary_key=True)
    form_entry_id = db.Column(db.Integer)
    field_name = db.Column(db.String(255))
    field_value = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<FormFieldValue {self.field_name}={self.field_value}>"


class FormEmailLog(db.Model):
    """
    Logs every email sent for form notifications.
    """
    __tablename__ = "form_email_logs"

    id = db.Column(db.Integer, primary_key=True)
    form_entry_id = db.Column(db.Integer)
    form_type_id = db.Column(db.Integer)
    sender_id = db.Column(db.Integer)                 # user who triggered or submitted
    email_type = db.Column(db.String(255))            # e.g. "form_submission", "form_update"
    sender_email = db.Column(db.String(255))
    receiver_id = db.Column(db.Integer)               # 👈 fix spelling (was reciver_id)
    message = db.Column(db.Text)
    status = db.Column(db.String(100))                # "sent" or "failed"
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<FormEmailLog from={self.sender_email} to={self.receiver_id} ({self.status})>"



class ContactFormSubmission(db.Model):
    __tablename__ = 'contact_form_submissions'

    id = db.Column(db.Integer, primary_key=True)
    clinic_id = db.Column(db.Integer, nullable=False)
    location_id = db.Column(db.Integer, nullable=True)
    form_name = db.Column(db.String(255), nullable=False)
    name = db.Column(db.String(255), nullable=True)
    phone = db.Column(db.String(255), nullable=True)
    email = db.Column(db.String(255), nullable=True)
    message = db.Column(db.String(500), nullable=True)
    data = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(50), default='pending', nullable=False)

    def __repr__(self):
        return f"<ContactFormSubmission id={self.id} form_name='{self.form_name}' clinic_id={self.clinic_id}>"
# =========================================================================


class ContactFormTicketLink(db.Model):
    __tablename__ = "contact_form_ticket_links"

    id = db.Column(db.Integer, primary_key=True)
    contact_form_id = db.Column(db.Integer, db.ForeignKey("contact_form_submissions.id"), nullable=False)
    ticket_id = db.Column(db.Integer, db.ForeignKey("tickets.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # optional relationships
    contact_form = db.relationship("ContactFormSubmission", backref="ticket_link", uselist=False)
    ticket = db.relationship("Ticket", backref="contact_form_link", uselist=False)

    def __repr__(self):
        return f"<ContactFormTicketLink form_id={self.contact_form_id} ticket_id={self.ticket_id}>"

class EmailLog(db.Model):
    __tablename__ = "email_logs"

    id = db.Column(db.Integer, primary_key=True)
    to = db.Column(db.String(255), nullable=False)
    subject = db.Column(db.String(500), nullable=True)
    body_html = db.Column(db.Text, nullable=True)
    body_text = db.Column(db.Text, nullable=True)
    mailgun_response = db.Column(db.Text, nullable=True)
    status_code = db.Column(db.Integer, nullable=True)
    success = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class EmailProcessedLog(db.Model):
    """
    Tracks processed emails to prevent duplicate ticket creation.
    Uses conversationId to group email threads.
    """
    __tablename__ = "email_processed_logs"

    id = db.Column(db.Integer, primary_key=True)
    email_id = db.Column(db.String(500), unique=True, nullable=False)  # Microsoft Graph email ID
    conversation_id = db.Column(db.String(500), nullable=True, index=True)  # For grouping email threads
    ticket_id = db.Column(db.Integer, nullable=True)  # Which ticket this email belongs to
    sender_email = db.Column(db.String(255), nullable=True)
    user_id = db.Column(db.Integer, nullable=True)  # User ID from Auth System
    email_subject = db.Column(db.String(500), nullable=True)
    processed_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_followup = db.Column(db.Boolean, default=False)  # True if added as comment to existing ticket

    def __repr__(self):
        return f"<EmailProcessedLog email_id={self.email_id} ticket_id={self.ticket_id} conversation_id={self.conversation_id}>"


class TicketAssignLocation(db.Model):
    """
    Stores location assignments for tickets.
    A ticket can be assigned to multiple locations.
    """
    __tablename__ = "ticket_assign_locations"

    id = db.Column(db.Integer, primary_key=True)
    ticket_id = db.Column(db.Integer, nullable=False, index=True)
    location_id = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by = db.Column(db.Integer, nullable=True)  # User who assigned the location

    def __repr__(self):
        return f"<TicketAssignLocation ticket_id={self.ticket_id} location_id={self.location_id}>"


class Project(db.Model):
    """
    Project model for managing projects
    """
    __tablename__ = "projects"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text, nullable=True)
    status = db.Column(db.String(50), default="Active")  # Active, Completed, On Hold, Cancelled
    priority = db.Column(db.String(50), default="Low")  # Low, Medium, High
    due_date = db.Column(db.Date, nullable=True)
    color = db.Column(db.String(50), default="#ef4444")  # Project color (hex code)
    created_by = db.Column(db.Integer, nullable=False)  # User who created the project
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<Project {self.id} - {self.name} ({self.status})>"


class ProjectTicket(db.Model):
    """
    Junction table linking projects and tickets
    """
    __tablename__ = "project_tickets"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False, index=True)
    ticket_id = db.Column(db.Integer, db.ForeignKey("tickets.id"), nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<ProjectTicket project_id={self.project_id} ticket_id={self.ticket_id}>"


class ProjectTag(db.Model):
    """
    Tags for projects
    """
    __tablename__ = "project_tags"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False, index=True)
    tag_name = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<ProjectTag project_id={self.project_id} tag={self.tag_name}>"


class ProjectAssignment(db.Model):
    """
    Team member assignments for projects
    """
    __tablename__ = "project_assignments"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, nullable=False)  # Assigned team member
    assigned_by = db.Column(db.Integer, nullable=True)  # User who assigned
    assigned_at = db.Column(db.DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<ProjectAssignment project_id={self.project_id} user_id={self.user_id}>"