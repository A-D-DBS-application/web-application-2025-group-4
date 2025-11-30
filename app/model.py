# app/model.py
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
from datetime import date

db = SQLAlchemy()

# ==========================
# USERS
# ==========================
class User(db.Model):
    __tablename__ = "users"

    users_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    name = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(255), unique=True, nullable=False)
    phone_number = db.Column(db.String(50))
    iban = db.Column(db.String(34))
    password = db.Column(db.String(255))  # nog niet functioneel gebruikt in jouw code
    deleted_at = db.Column(db.DateTime) 
    is_active = db.Column(db.Boolean, default=True)

# ==========================
# GROUPS
# ==========================
class Group(db.Model):
    __tablename__ = "groups"

    group_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(255), nullable=False)
    start_date = db.Column(db.Date)
    end_date = db.Column(db.Date)
    auto_close_on_end = db.Column(db.Boolean, default=True)
    invite_link = db.Column(db.String(255))
    currency = db.Column(db.String(3), default="EUR")
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.users_id"), nullable=False)
    app_fee_amount = db.Column(db.Float)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# ==========================
# GROUP MEMBERS
# ==========================
class GroupMember(db.Model):
    __tablename__ = "group_members"

    group_id = db.Column(db.Integer, db.ForeignKey("groups.group_id"), primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.users_id"), primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    role = db.Column(db.String(50))

# ==========================
# EXPENSES
# ==========================
class Expense(db.Model):
    __tablename__ = "expenses" 

    expense_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    group_id = db.Column(db.Integer, db.ForeignKey("groups.group_id"), nullable=False)
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.users_id"), nullable=False)
    description = db.Column(db.String(255), nullable=False)
    total_amount = db.Column(db.Float, nullable=False)
    split_method = db.Column(db.String(20), default="equal")
    attachment_id = db.Column(db.Integer)
    status = db.Column(db.String(20))
    deleted_at = db.Column(db.DateTime)
    is_active = db.Column(db.Boolean, default=True) 

# ==========================
# PAYMENTS
# ==========================
class Payment(db.Model):
    __tablename__ = "payments"

    payments_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    amount = db.Column(db.Float, nullable=False)
    group_id = db.Column(db.Integer, db.ForeignKey("groups.group_id"), nullable=False)
    sender_id = db.Column(db.Integer, db.ForeignKey("users.users_id"), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey("users.users_id"), nullable=False)
    currency = db.Column(db.String(3), default="EUR")
    deleted_at = db.Column(db.DateTime)
    is_active = db.Column(db.Boolean, default=True)

 
class Group(db.Model):
    __tablename__ = "groups"
    # je bestaande kolommen:
    # id, name, start_date, end_date, ...

    @property
    def is_closed(self) -> bool:
        """
        Groep is gesloten zodra er een einddatum is
        én die einddatum in het verleden ligt.
        """
        if self.end_date is None:
            return False
        # als start_date / end_date al type date zijn is dit perfect
        return self.end_date < date.today()
