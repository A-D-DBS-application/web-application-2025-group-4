# app/routes.py
from datetime import datetime, date
from io import StringIO
from decimal import Decimal
import csv, re, os

from flask import (
    Blueprint, render_template, request, redirect, url_for,
    session, flash, abort, Response
)
from werkzeug.utils import secure_filename

from .model import db, User, Group, GroupMember, Expense, Payment

main = Blueprint("main", __name__)

# ---------------- Utils ----------------
def current_user():
    uid = session.get("user_id")
    return User.query.get(uid) if uid else None

def login_required(fn):
    from functools import wraps
    @wraps(fn)
    def wrap(*a, **kw):
        if not current_user():
            flash("Log eerst in.", "warning")
            return redirect(url_for("main.login"))
        return fn(*a, **kw)
    return wrap

def group_is_closed(g: Group) -> bool:
    return bool(g.end_date and g.auto_close_on_end and date.today() > g.end_date)

def parse_voice_expense(text: str):
    items = []
    for chunk in text.split(","):
        c = chunk.strip()
        m_for = re.search(r"(\d+)\s+([A-Za-z]+).*for\s+([A-Za-z]+)$", c, re.I)
        m_us  = re.search(r"(\d+)\s+([A-Za-z]+).*for\s+us$", c, re.I)
        if m_for:
            qty, item, person = int(m_for.group(1)), m_for.group(2), m_for.group(3).title()
            items.append((person, item, qty))
        elif m_us:
            qty, item = int(m_us.group(1)), m_us.group(2)
            items.append(("GROUP", item, qty))
    return items

def member_ids(group_id:int):
    return [m.user_id for m in GroupMember.query.filter_by(group_id=group_id).all()]

def calc_balances(group_id:int):
    g = Group.query.get_or_404(group_id)
    uids = member_ids(group_id)
    balances = {uid: Decimal("0.00") for uid in uids}
    headcount = max(1, len(uids))

    # Expenses (even split)
    for e in Expense.query.filter_by(group_id=group_id).all():
        tot = Decimal(str(e.total_amount))
        share = tot / headcount
        for uid in uids: balances[uid] -= share
        balances[e.created_by_user_id] += tot

    # App fee
    fee = Decimal(str(g.app_fee_amount or 0))
    if fee > 0 and headcount > 0:
        share = fee / headcount
        for uid in uids: balances[uid] -= share
        if g.created_by_user_id in balances:
            balances[g.created_by_user_id] += fee

    # Payments (direct verwerkt)
    for p in Payment.query.filter_by(group_id=group_id).all():
        amt = Decimal(str(p.amount))
        balances[p.sender_id]   -= amt
        balances[p.receiver_id] += amt

    return balances

# ---------------- UI / Menu ----------------
@main.route("/")
def index():
    user = current_user()
    my_groups = []
    if user:
        my_groups = (
            Group.query.join(GroupMember, GroupMember.group_id == Group.group_id)
            .filter(GroupMember.user_id == user.user_id)
            .order_by(Group.start_date.desc().nullslast())
            .all()
        )
    return render_template("index.html", user=user, my_groups=my_groups)

# ---------------- Auth ----------------
@main.route("/register", methods=["GET","POST"])
def register():
    if request.method == "POST":
        name  = request.form.get("name","").strip()
        email = request.form.get("email","").strip().lower()
        if not name or not email:
            flash("Naam en e-mail zijn verplicht.", "danger")
            return redirect(url_for("main.register"))
        if User.query.filter_by(email=email).first():
            flash("E-mail is al geregistreerd.", "danger")
            return redirect(url_for("main.register"))
        u = User(name=name, email=email)
        db.session.add(u); db.session.commit()
        flash("Registratie gelukt. Je kan nu inloggen.", "success")
        return redirect(url_for("main.login"))
    return render_template("register.html")

@main.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email","").strip().lower()
        user = User.query.filter_by(email=email).first()
        if not user:
            flash("Geen account gevonden. Registreer eerst.", "danger")
            return redirect(url_for("main.login"))
        session["user_id"] = user.user_id
        flash(f"Welkom, {user.name}!", "success")
        return redirect(url_for("main.index"))
    return render_template("login.html")

@main.route("/logout", methods=["POST"])
def logout():
    session.pop("user_id", None)
    flash("Je bent uitgelogd.", "info")
    return redirect(url_for("main.index"))

# ---------------- Groups ----------------
@main.route("/groups/new", methods=["GET","POST"])
@login_required
def create_group():
    if request.method == "POST":
        name       = request.form.get("name","").strip()
        start_date = request.form.get("start_date") or None
        end_date   = request.form.get("end_date") or None
        currency   = request.form.get("currency") or "EUR"
        invite     = request.form.get("invite_link") or None
        auto_close = bool(request.form.get("auto_close_on_end"))
        fee        = request.form.get("app_fee_amount") or "0"

        if not name:
            flash("Naam is verplicht.", "danger")
            return redirect(url_for("main.create_group"))

        g = Group(
            name=name,
            start_date=datetime.strptime(start_date, "%Y-%m-%d").date() if start_date else None,
            end_date=datetime.strptime(end_date, "%Y-%m-%d").date() if end_date else None,
            auto_close_on_end=auto_close,
            invite_link=invite,
            currency=currency,
            created_by_user_id=current_user().user_id,
            app_fee_amount=Decimal(fee)
        )
        db.session.add(g); db.session.commit()
        db.session.add(GroupMember(group_id=g.group_id, user_id=current_user().user_id))
        db.session.commit()
        flash("Groep aangemaakt.", "success")
        return redirect(url_for("main.group_detail", group_id=g.group_id))
    return render_template("group_new.html")

@main.route("/groups/<int:group_id>")
@login_required
def group_detail(group_id:int):
    g = Group.query.get_or_404(group_id)
    is_member = GroupMember.query.filter_by(group_id=g.group_id, user_id=current_user().user_id).first()
    if not is_member:
        abort(403)
    members  = GroupMember.query.filter_by(group_id=g.group_id).all()
    expenses = Expense.query.filter_by(group_id=g.group_id).order_by(Expense.created_at.desc()).all()
    payments = Payment.query.filter_by(group_id=g.group_id).order_by(Payment.created_at.desc()).all()
    balances = calc_balances(g.group_id)
    closed = group_is_closed(g)
    return render_template("group_detail.html", user=current_user(),
                           group=g, members=members, expenses=expenses,
                           payments=payments, balances=balances, closed=closed)

@main.route("/groups/<int:group_id>/invite", methods=["POST"])
@login_required
def save_invite(group_id:int):
    g = Group.query.get_or_404(group_id)
    if g.created_by_user_id != current_user().user_id:
        abort(403)
    link = request.form.get("invite_link","").strip()
    g.invite_link = link or None
    db.session.commit()
    flash("Invite-link opgeslagen.", "success")
    return redirect(url_for("main.group_detail", group_id=group_id))

@main.route("/groups/<int:group_id>/join", methods=["POST"])
@login_required
def join_group(group_id:int):
    if GroupMember.query.filter_by(group_id=group_id, user_id=current_user().user_id).first():
        flash("Je bent al lid.", "info")
        return redirect(url_for("main.group_detail", group_id=group_id))
    db.session.add(GroupMember(group_id=group_id, user_id=current_user().user_id))
    db.session.commit()
    flash("Je bent toegetreden.", "success")
    return redirect(url_for("main.group_detail", group_id=group_id))

# ---------------- Expenses ----------------
@main.route("/groups/<int:gid>/expenses/new", methods=["POST"])
@login_required
def add_expense(gid:int):
    g = Group.query.get_or_404(gid)
    if group_is_closed(g):
        flash("Deze groep is gesloten.", "warning")
        return redirect(url_for("main.group_detail", group_id=gid))

    desc = request.form.get("description","").strip()
    amt  = Decimal(request.form.get("amount") or "0")
    if not desc or amt <= 0:
        flash("Beschrijving en positief bedrag vereist.", "danger")
        return redirect(url_for("main.group_detail", group_id=gid))

    e = Expense(group_id=gid, created_by_user_id=current_user().user_id,
                description=desc, total_amount=amt, split_method="equal")
    db.session.add(e); db.session.commit()
    flash("Uitgave toegevoegd.", "success")
    return redirect(url_for("main.group_detail", group_id=gid))

@main.route("/groups/<int:gid>/expenses/voice", methods=["POST"])
@login_required
def add_expense_voice(gid:int):
    text = request.form.get("voice_text","")
    items = parse_voice_expense(text)
    if not items:
        flash("Kon de zin niet parsen. Probeer eenvoudiger.", "danger")
        return redirect(url_for("main.group_detail", group_id=gid))
    price_map = {"beer": Decimal("3"), "beers": Decimal("3"),
                 "pizza": Decimal("10"), "pizzas": Decimal("10")}
    total = Decimal("0")
    for _, item, qty in items:
        total += price_map.get(item.lower(), Decimal("5")) * qty

    e = Expense(group_id=gid, created_by_user_id=current_user().user_id,
                description=f"Voice add: {text}", total_amount=total, split_method="equal")
    db.session.add(e); db.session.commit()
    flash(f"Voice-uitgave toegevoegd (€{total:.2f}).", "success")
    return redirect(url_for("main.group_detail", group_id=gid))

@main.route("/groups/<int:gid>/expenses/receipt", methods=["POST"])
@login_required
def add_expense_receipt(gid:int):
    file = request.files.get("receipt")
    if not file:
        flash("Geen bestand gekozen.", "danger")
        return redirect(url_for("main.group_detail", group_id=gid))
    os.makedirs("uploads", exist_ok=True)
    fname = secure_filename(file.filename)
    path = os.path.join("uploads", fname); file.save(path)
    # OCR-stub: €42
    e = Expense(group_id=gid, created_by_user_id=current_user().user_id,
                description=f"Scanned receipt: {fname}", total_amount=Decimal("42.00"),
                split_method="by_receipt")
    db.session.add(e); db.session.commit()
    flash("Bonnetje geüpload (OCR-stub).", "success")
    return redirect(url_for("main.group_detail", group_id=gid))

# ---------------- Payments ----------------
@main.route("/groups/<int:gid>/payments/new", methods=["POST"])
@login_required
def create_payment(gid:int):
    receiver = int(request.form.get("receiver_id"))
    amount   = Decimal(request.form.get("amount") or "0")
    if amount <= 0:
        flash("Bedrag moet positief zijn.", "danger")
        return redirect(url_for("main.group_detail", group_id=gid))
    p = Payment(group_id=gid, sender_id=current_user().user_id,
                receiver_id=receiver, amount=amount)
    db.session.add(p); db.session.commit()
    flash("Betaling geregistreerd.", "success")
    return redirect(url_for("main.group_detail", group_id=gid))

# ---------------- Ledger & Export ----------------
@main.route("/groups/<int:gid>/ledger")
@login_required
def ledger(gid:int):
    g = Group.query.get_or_404(gid)
    expenses = Expense.query.filter_by(group_id=gid).order_by(Expense.created_at.asc()).all()
    payments = Payment.query.filter_by(group_id=gid).order_by(Payment.created_at.asc()).all()
    balances = calc_balances(gid)
    return render_template("ledger.html", group=g, expenses=expenses, payments=payments, balances=balances)

@main.route("/groups/<int:gid>/export.csv")
@login_required
def export_csv(gid:int):
    balances = calc_balances(gid)
    si = StringIO(); cw = csv.writer(si, delimiter=';')
    cw.writerow(["user_id","balance"])
    for uid, bal in balances.items():
        cw.writerow([uid, f"{bal:.2f}"])
    data = si.getvalue().encode("utf-8")
    return Response(data, mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=group_{gid}_balances.csv"})

