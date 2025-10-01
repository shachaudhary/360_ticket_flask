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
    ticket_id = db.Column(db.Integer, db.ForeignKey("ticket.id"), nullable=False)
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
