# app/model.py
import os
from datetime import datetime, date
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

# ---- MySQL URI (vul aan via .env of hardcode tijdelijk) ----
def mysql_uri():
    host = os.getenv("MYSQL_HOST", "localhost")
    port = os.getenv("MYSQL_PORT", "3306")
    user = os.getenv("MYSQL_USER", "root")
    pwd  = os.getenv("MYSQL_PASSWORD", "")
    name = os.getenv("MYSQL_DB", "groepsuitgaven")
    return f"mysql+pymysql://{user}:{pwd}@{host}:{port}/{name}?charset=utf8mb4"

# ---- Modellen volgens jouw schema ----
class User(db.Model):
    __tablename__ = "users"
    user_id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name    = db.Column(db.String(255), nullable=False)
    email   = db.Column(db.String(255), unique=True)
    phone   = db.Column(db.String(50))
    iban    = db.Column(db.String(34))

class Group(db.Model):
    __tablename__ = "groups"
    group_id           = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name               = db.Column(db.String(255), nullable=False)
    start_date         = db.Column(db.Date)
    end_date           = db.Column(db.Date)
    auto_close_on_end  = db.Column(db.Boolean, nullable=False, default=True)
    invite_link        = db.Column(db.String(255))
    currency           = db.Column(db.String(3), nullable=False, default="EUR")
    created_by_user_id = db.Column(db.Integer, db.ForeignKey("users.user_id"), nullable=False)
    app_fee_amount     = db.Column(db.Numeric(12,2))

class GroupMember(db.Model):
    __tablename__ = "group_members"
    group_id = db.Column(db.Integer, db.ForeignKey("groups.group_id"), primary_key=True)
    user_id  = db.Column(db.Integer, db.ForeignKey("users.user_id"), primary_key=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Expense(db.Model):
    __tablename__ = "expenses"
    expense_id        = db.Column(db.Integer, primary_key=True, autoincrement=True)
    group_id          = db.Column(db.Integer, db.ForeignKey("groups.group_id"), nullable=False)
    created_by_user_id= db.Column(db.Integer, db.ForeignKey("users.user_id"), nullable=False)
    description       = db.Column(db.String(255), nullable=False)
    total_amount      = db.Column(db.Numeric(12,2), nullable=False)
    split_method      = db.Column(db.String(20), nullable=False, default="equal")
    attachment_id     = db.Column(db.Integer)
    created_at        = db.Column(db.DateTime, default=datetime.utcnow)

class Payment(db.Model):
    __tablename__ = "payments"
    payment_id  = db.Column(db.Integer, primary_key=True, autoincrement=True)
    group_id    = db.Column(db.Integer, db.ForeignKey("groups.group_id"), nullable=False)
    sender_id   = db.Column(db.Integer, db.ForeignKey("users.user_id"), nullable=False)
    receiver_id = db.Column(db.Integer, db.ForeignKey("users.user_id"), nullable=False)
    amount      = db.Column(db.Numeric(12,2), nullable=False)
    created_at  = db.Column(db.DateTime, default=datetime.utcnow)
