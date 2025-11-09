# app/routes.py
from flask import (
    Blueprint, render_template, request, redirect,
    url_for, session, flash, abort, Response
)
from datetime import datetime
from decimal import Decimal
from io import StringIO
import csv

from app.supabase_client import supabase

main = Blueprint("main", __name__)

# --------------------------
# HELPERS
# --------------------------

def current_user():
    uid = session.get("users_id")
    if not uid:
        return None
    res = supabase.table("users").select("*").eq("users_id", uid).execute()
    data = res.data or []
    return data[0] if data else None


def login_required(func):
    from functools import wraps
    @wraps(func)
    def wrapper(*args, **kwargs):
        if not current_user():
            flash("Please log in first.", "warning")
            return redirect(url_for("main.login"))
        return func(*args, **kwargs)
    return wrapper


# --------------------------
# AUTH ROUTES
# --------------------------

@main.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        username = request.form.get("username", "").strip()
        phone_number = request.form.get("phone_number", "").strip()
        iban = request.form.get("iban", "").replace(" ", "").upper()
        password = request.form.get("password", "").strip()

        # Check verplichte velden
        if not all([name, email, username, phone_number, iban, password]):
            flash("Alle velden zijn verplicht.", "danger")
            return redirect(url_for("main.register"))

        # Basale IBAN-validatie
        if not iban.startswith("BE") or len(iban) < 14:
            flash("Voer een geldig Belgisch IBAN-nummer in.", "danger")
            return redirect(url_for("main.register"))

        # Controleer op bestaande e-mail
        existing = supabase.table("users").select("users_id").eq("email", email).execute().data
        if existing:
            flash("Dit e-mailadres is al geregistreerd.", "danger")
            return redirect(url_for("main.register"))

        payload = {
            "name": name,
            "email": email,
            "username": username,
            "phone_number": phone_number,
            "iban": iban,
            "password": password,
            "created_at": datetime.utcnow().isoformat()
        }

        # Verstuur naar Supabase
        try:
            supabase.table("users").insert(payload).execute()
            flash("Registratie succesvol! Je kunt nu inloggen.", "success")
            return redirect(url_for("main.login"))
        except Exception as e:
            print("❌ Fout bij registratie:", e)
            flash("Er is iets misgelopen bij het registreren.", "danger")
            return redirect(url_for("main.register"))

    return render_template("register.html")


@main.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        identifier = request.form.get("username", "").strip()
        if not identifier:
            flash("Please enter your username or email.", "danger")
            return redirect(url_for("main.login"))

        res = supabase.table("users").select("*").or_(
            f"email.eq.{identifier.lower()},username.eq.{identifier}"
        ).execute()
        users = res.data or []
        if not users:
            flash("User not found.", "danger")
            return redirect(url_for("main.login"))

        user = users[0]
        session["users_id"] = user["users_id"]
        flash(f"Welcome, {user['name']}!", "success")
        return redirect(url_for("main.index"))

    return render_template("login.html")


@main.route("/logout")
def logout():
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("main.login"))


# --------------------------
# HOME
# --------------------------

@main.route("/")
def index():
    user = current_user()
    return render_template("index.html", user=user)


# --------------------------
# GROUPS
# --------------------------

@main.route("/groups/new", methods=["GET", "POST"])
@login_required
def create_group():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        currency = request.form.get("currency", "EUR")
        if not name:
            flash("Group name is required.", "danger")
            return redirect(url_for("main.create_group"))

        payload = {
            "name": name,
            "currency": currency,
            "created_by_user_id": session["users_id"],
            "created_at": datetime.utcnow().isoformat()
        }
        resp = supabase.table("groups").insert(payload).execute()
        group = resp.data[0]
        supabase.table("group_members").insert({
            "group_id": group["group_id"],
            "user_id": session["users_id"],
            "created_at": datetime.utcnow().isoformat()
        }).execute()

        flash("Group created.", "success")
        return redirect(url_for("main.group_detail", group_id=group["group_id"]))

    return render_template("group_new.html")


def member_ids(group_id: int):
    res = supabase.table("group_members").select("user_id").eq("group_id", group_id).execute()
    rows = res.data or []
    return [r["user_id"] for r in rows]


def calc_balances(group_id: int):
    g = supabase.table("groups").select("*").eq("group_id", group_id).execute().data
    if not g:
        abort(404)
    group = g[0]
    uids = member_ids(group_id)
    balances = {uid: Decimal("0.00") for uid in uids}
    headcount = max(1, len(uids))

    exp = supabase.table("expenses").select("*").eq("group_id", group_id).execute().data or []
    for e in exp:
        total = Decimal(str(e.get("total_amount", 0)))
        share = total / headcount
        for uid in uids:
            balances[uid] -= share
        balances[e["created_by_user_id"]] += total

    pay = supabase.table("payments").select("*").eq("group_id", group_id).execute().data or []
    for p in pay:
        amt = Decimal(str(p.get("amount", 0)))
        balances[p["sender_id"]] -= amt
        balances[p["receiver_id"]] += amt

    return balances


@main.route("/groups/<int:group_id>")
@login_required
def group_detail(group_id: int):
    g = supabase.table("groups").select("*").eq("group_id", group_id).execute().data
    if not g:
        abort(404)
    group = g[0]

    members = supabase.table("group_members").select("*").eq("group_id", group_id).execute().data or []
    expenses = supabase.table("expenses").select("*").eq("group_id", group_id).order("created_at", desc=True).execute().data or []
    payments = supabase.table("payments").select("*").eq("group_id", group_id).order("created_at", desc=True).execute().data or []

    balances = calc_balances(group_id)

    return render_template(
        "group_detail.html",
        user=current_user(),
        group=group,
        members=members,
        expenses=expenses,
        payments=payments,
        balances=balances
    )


# --------------------------
# EXPENSES
# --------------------------

@main.route("/groups/<int:gid>/expenses/new", methods=["POST"])
@login_required
def add_expense(gid: int):
    desc = request.form.get("description", "").strip()
    amt = Decimal(request.form.get("amount") or "0")
    if not desc or amt <= 0:
        flash("Description and positive amount required.", "danger")
        return redirect(url_for("main.group_detail", group_id=gid))

    payload = {
        "group_id": gid,
        "created_by_user_id": session["users_id"],
        "description": desc,
        "total_amount": float(amt),
        "split_method": "equal",
        "created_at": datetime.utcnow().isoformat()
    }
    supabase.table("expenses").insert(payload).execute()
    flash("Expense added.", "success")
    return redirect(url_for("main.group_detail", group_id=gid))


# --------------------------
# PAYMENTS
# --------------------------

@main.route("/groups/<int:gid>/payments/new", methods=["POST"])
@login_required
def create_payment(gid: int):
    receiver = int(request.form.get("receiver_id"))
    amount = Decimal(request.form.get("amount") or "0")
    if amount <= 0:
        flash("Amount must be positive.", "danger")
        return redirect(url_for("main.group_detail", group_id=gid))

    payload = {
        "group_id": gid,
        "sender_id": session["users_id"],
        "receiver_id": receiver,
        "amount": float(amount),
        "currency": "EUR",
        "created_at": datetime.utcnow().isoformat()
    }
    supabase.table("payments").insert(payload).execute()
    flash("Payment registered.", "success")
    return redirect(url_for("main.group_detail", group_id=gid))


# --------------------------
# EXPORT CSV
# --------------------------

@main.route("/groups/<int:gid>/export.csv")
@login_required
def export_csv(gid: int):
    balances = calc_balances(gid)
    si = StringIO()
    writer = csv.writer(si, delimiter=';')
    writer.writerow(["user_id", "balance"])
    for uid, bal in balances.items():
        writer.writerow([uid, f"{bal:.2f}"])
    output = si.getvalue().encode("utf-8")
    return Response(
        output,
        mimetype="text/csv",
        headers={"Content-Disposition": f"attachment; filename=group_{gid}_balances.csv"}
    )
