# app/routes.py
from datetime import datetime, date
from io import StringIO
import csv
import re
from decimal import Decimal

from flask import (
    Blueprint, render_template, request, redirect, url_for,
    session, flash, abort, send_file, Response
)
from werkzeug.utils import secure_filename

from .models import db, User, Group, GroupMember, Expense, Payment  # zie model-suggesties in comments onderaan

main = Blueprint("main", __name__)

# -----------------------
# Helpers / middleware
# -----------------------
def current_user():
    if "user_id" in session:
        return User.query.get(session["user_id"])
    return None

def login_required(fn):
    from functools import wraps
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not current_user():
            flash("Please log in first.", "warning")
            return redirect(url_for("main.login"))
        return fn(*args, **kwargs)
    return wrapper

def parse_voice_expense(text):
    """
    Ultra-simpele parser: '3 beers for Tom, 2 pizzas for us' -> [('Tom', 'beers', 3), ('GROUP', 'pizzas', 2)]
    Dit is geen NLP, gewoon demo.
    """
    items = []
    # Voorbeeld-patronen
    for chunk in text.split(","):
        m_for = re.search(r"(\d+)\s+([a-zA-Z]+).*for\s+([A-Za-z]+)", chunk.strip())
        m_group = re.search(r"(\d+)\s+([a-zA-Z]+).*for\s+us", chunk.strip(), re.I)
        if m_for:
            qty, item, person = int(m_for.group(1)), m_for.group(2), m_for.group(3).title()
            items.append((person, item, qty))
        elif m_group:
            qty, item = int(m_group.group(1)), m_group.group(2)
            items.append(("GROUP", item, qty))
    return items

def calc_balances(group_id):
    """
    Geeft per user_id het saldo (positief = krijgt geld, negatief = moet betalen).
    App fee (group.app_fee_amount) verdelen we automatisch pro rata per lid (simpel: iedereen gelijk deel).
    """
    group = Group.query.get_or_404(group_id)
    members = GroupMember.query.filter_by(group_id=group.id).all()
    member_ids = [m.user_id for m in members]

    balances = {uid: Decimal("0.00") for uid in member_ids}

    # 1) Expenses: payer betaalt voor iedereen (split even)
    expenses = Expense.query.filter_by(group_id=group.id).all()
    headcount = max(1, len(member_ids))
    for e in expenses:
        share = Decimal(str(e.total_amount)) / headcount
        # elke deelnemer -share
        for uid in member_ids:
            balances[uid] -= share
        # betaler +totaal
        balances[e.paid_by_user_id] += Decimal(str(e.total_amount))

    # 2) App fee verdelen (bv 2 of 5 EUR over alle leden)
    if group.app_fee_amount and group.app_fee_amount > 0:
        fee_share = Decimal(str(group.app_fee_amount)) / headcount
        for uid in member_ids:
            balances[uid] -= fee_share
        # organisator int de fee (of app-account; hier organisator)
        if group.created_by_user_id in balances:
            balances[group.created_by_user_id] += Decimal(str(group.app_fee_amount))

    # 3) Reeds geregistreerde betalingen (Payconiq/Revolut/IBAN simulatie)
    pays = Payment.query.filter_by(group_id=group.id, status="confirmed").all()
    for p in pays:
        # payer -> -amount, receiver -> +amount
        balances[p.paid_by_user_id] -= Decimal(str(p.amount))
        balances[p.received_by_user_id] += Decimal(str(p.amount))

    return balances

def group_is_closed(group: "Group"):
    return bool(group.end_date and date.today() > group.end_date)

# -----------------------
# UI: simpele menu / landing
# -----------------------
@main.route("/")
def index():
    user = current_user()
    # Simpele menu + overzicht eigen en publieke groepen
    my_groups = []
    if user:
        my_groups = Group.query.join(GroupMember, GroupMember.group_id == Group.id)\
            .filter(GroupMember.user_id == user.id).all()

    public_groups = Group.query.filter_by(is_public=True).order_by(Group.created_at.desc()).limit(10).all()
    return render_template("index.html", user=user, my_groups=my_groups, public_groups=public_groups)

# -----------------------
# Auth (vereiste: niet-bestaande users krijgen fout; eerst registreren)
# -----------------------
@main.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        if not username or not password:
            flash("Username and password are required.", "danger")
            return redirect(url_for("main.register"))

        if User.query.filter_by(username=username).first():
            flash("Username already taken.", "danger")
            return redirect(url_for("main.register"))

        u = User(username=username)
        u.set_password(password)  # verwacht method in model (generate_password_hash)
        db.session.add(u)
        db.session.commit()
        flash("Registration successful. You can now log in.", "success")
        return redirect(url_for("main.login"))
    return render_template("register.html")

@main.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        user = User.query.filter_by(username=username).first()
        if not user:
            # Vereiste: als geen account -> expliciete fout + eerst registreren
            flash("No account found. Please register before logging in.", "danger")
            return redirect(url_for("main.login"))
        if not user.check_password(password):
            flash("Invalid credentials.", "danger")
            return redirect(url_for("main.login"))

        session["user_id"] = user.id
        flash(f"Welcome back, {user.username}!", "success")
        return redirect(url_for("main.index"))
    return render_template("login.html")

@main.route("/logout", methods=["POST"])
def logout():
    session.pop("user_id", None)
    flash("Logged out.", "info")
    return redirect(url_for("main.index"))

# -----------------------
# Groups
# -----------------------
@main.route("/groups/new", methods=["GET", "POST"])
@login_required
def create_group():
    """
    User-story: tijdelijke groep met end-date zodat saldi automatisch sluiten.
    """
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        end_date_str = request.form.get("end_date")  # yyyy-mm-dd of leeg
        is_public = bool(request.form.get("is_public"))
        app_fee_amount = Decimal(request.form.get("app_fee_amount") or "0")

        if not name:
            flash("Group name is required.", "danger")
            return redirect(url_for("main.create_group"))

        end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date() if end_date_str else None

        g = Group(
            name=name,
            created_by_user_id=current_user().id,
            end_date=end_date,
            app_fee_amount=app_fee_amount,
            is_public=is_public
        )
        db.session.add(g)
        db.session.commit()

        # creator is automatisch lid
        gm = GroupMember(group_id=g.id, user_id=current_user().id)
        db.session.add(gm)
        db.session.commit()

        flash("Group created.", "success")
        return redirect(url_for("main.group_detail", group_id=g.id))

    return render_template("group_new.html")

@main.route("/groups/<int:group_id>")
@login_required
def group_detail(group_id):
    group = Group.query.get_or_404(group_id)
    # Toegang beperken tot leden
    is_member = GroupMember.query.filter_by(group_id=group.id, user_id=current_user().id).first()
    if not (group.is_public or is_member):
        abort(403)

    members = GroupMember.query.filter_by(group_id=group.id).all()
    expenses = Expense.query.filter_by(group_id=group.id).order_by(Expense.created_at.desc()).all()
    payments = Payment.query.filter_by(group_id=group.id).order_by(Payment.created_at.desc()).all()
    balances = calc_balances(group.id)

    # Auto-close (alleen UI-hint; echte hard close zou je in cron/scheduled job doen)
    closed = group_is_closed(group)
    return render_template("group_detail.html",
                           group=group, members=members, expenses=expenses, payments=payments,
                           balances=balances, closed=closed)

@main.route("/groups/<int:group_id>/invite", methods=["POST"])
@login_required
def add_member_via_link(group_id):
    """
    User-story: invite by pasting WhatsApp group link (we slaan gewoon de link op)
    """
    group = Group.query.get_or_404(group_id)
    if group.created_by_user_id != current_user().id:
        abort(403)

    wa_url = request.form.get("whatsapp_url", "").strip()
    if not wa_url.startswith("https://"):
        flash("Please paste a valid WhatsApp invite URL.", "danger")
        return redirect(url_for("main.group_detail", group_id=group.id))

    group.invite_link = wa_url
    db.session.commit()
    flash("Invite link saved. Share it with friends!", "success")
    return redirect(url_for("main.group_detail", group_id=group.id))

@main.route("/groups/<int:group_id>/join", methods=["POST"])
@login_required
def join_group(group_id):
    """
    Eenvoudige join (alsof je via invite kwam).
    """
    group = Group.query.get_or_404(group_id)
    if GroupMember.query.filter_by(group_id=group.id, user_id=current_user().id).first():
        flash("You are already a member.", "info")
        return redirect(url_for("main.group_detail", group_id=group.id))
    db.session.add(GroupMember(group_id=group.id, user_id=current_user().id))
    db.session.commit()
    flash("Joined the group.", "success")
    return redirect(url_for("main.group_detail", group_id=group.id))

# -----------------------
# Expenses
# -----------------------
@main.route("/groups/<int:group_id>/expenses/new", methods=["POST"])
@login_required
def add_expense(group_id):
    """
    Klassieke expense: beschrijving + bedrag, betaald door current_user, even split.
    """
    group = Group.query.get_or_404(group_id)
    if group_is_closed(group):
        flash("This group is closed (end-date passed).", "warning")
        return redirect(url_for("main.group_detail", group_id=group.id))

    desc = request.form.get("description", "").strip()
    amount = Decimal(request.form.get("amount") or "0")
    if not desc or amount <= 0:
        flash("Description and positive amount required.", "danger")
        return redirect(url_for("main.group_detail", group_id=group.id))

    e = Expense(
        group_id=group.id,
        description=desc,
        total_amount=float(amount),
        paid_by_user_id=current_user().id
    )
    db.session.add(e)
    db.session.commit()
    flash("Expense added.", "success")
    return redirect(url_for("main.group_detail", group_id=group.id))

@main.route("/groups/<int:group_id>/expenses/voice", methods=["POST"])
@login_required
def add_expense_by_voice(group_id):
    """
    User-story: '3 beers for Tom, 2 pizzas for us' (simple demo parser)
    We maken 1 samengevoegde expense met beschrijving en totaalbedrag.
    """
    text = request.form.get("voice_text", "")
    items = parse_voice_expense(text)
    if not items:
        flash("Couldn't parse your voice text. Try a simpler phrase.", "danger")
        return redirect(url_for("main.group_detail", group_id=group_id))

    # Simuleer prijs per item als €3 (beers) / €10 (pizza) — puur demo
    price_map = {"beer": Decimal("3"), "beers": Decimal("3"), "pizza": Decimal("10"), "pizzas": Decimal("10")}
    total = Decimal("0")
    for _, item, qty in items:
        total += price_map.get(item.lower(), Decimal("5")) * qty

    e = Expense(
        group_id=group_id,
        description=f"Voice quick add: {text}",
        total_amount=float(total),
        paid_by_user_id=current_user().id
    )
    db.session.add(e)
    db.session.commit()
    flash(f"Added voice expense for €{total}.", "success")
    return redirect(url_for("main.group_detail", group_id=group_id))

@main.route("/groups/<int:group_id>/expenses/receipt", methods=["POST"])
@login_required
def upload_receipt(group_id):
    """
    User-story: foto van rekening uploaden -> autoscan (hier: stub).
    We bewaren de file en maken één 'gescande' expense (geen echte OCR in MVP).
    """
    file = request.files.get("receipt")
    if not file:
        flash("No file provided.", "danger")
        return redirect(url_for("main.group_detail", group_id=group_id))

    filename = secure_filename(file.filename)
    if not filename:
        flash("Invalid filename.", "danger")
        return redirect(url_for("main.group_detail", group_id=group_id))

    filepath = f"uploads/{filename}"
    file.save(filepath)

    # TODO: OCR integratie. Voor nu: dummy bedrag €42
    e = Expense(
        group_id=group_id,
        description=f"Scanned receipt: {filename}",
        total_amount=42.00,
        paid_by_user_id=current_user().id
    )
    db.session.add(e)
    db.session.commit()
    flash("Receipt uploaded and expense created (OCR stub).", "success")
    return redirect(url_for("main.group_detail", group_id=group_id))

# -----------------------
# Payments (Payconiq/Revolut/IBAN – gesimuleerd)
# -----------------------
@main.route("/groups/<int:group_id>/payments/new", methods=["POST"])
@login_required
def create_payment(group_id):
    """
    User-story: in-app betalen/ontvangen (simulatie).
    We maken een payment record met 'pending' en laten 'confirm' als aparte stap.
    """
    receiver_id = int(request.form.get("receiver_id"))
    amount = Decimal(request.form.get("amount") or "0")
    if amount <= 0:
        flash("Amount must be positive.", "danger")
        return redirect(url_for("main.group_detail", group_id=group_id))

    p = Payment(
        group_id=group_id,
        paid_by_user_id=current_user().id,
        received_by_user_id=receiver_id,
        amount=float(amount),
        method=request.form.get("method") or "iban",
        status="pending",
        external_ref=request.form.get("external_ref")  # bv. Payconiq/Revolut ref
    )
    db.session.add(p)
    db.session.commit()
    flash("Payment created (pending). Ask receiver to confirm.", "info")
    return redirect(url_for("main.group_detail", group_id=group_id))

@main.route("/payments/<int:payment_id>/confirm", methods=["POST"])
@login_required
def confirm_payment(payment_id):
    p = Payment.query.get_or_404(payment_id)
    # Alleen ontvanger mag bevestigen
    if p.received_by_user_id != current_user().id:
        abort(403)
    p.status = "confirmed"
    db.session.commit()
    flash("Payment confirmed.", "success")
    return redirect(url_for("main.group_detail", group_id=p.group_id))

# -----------------------
# Ledger / Export
# -----------------------
@main.route("/groups/<int:group_id>/ledger")
@login_required
def ledger(group_id):
    """
    User-story: clean ledger met wie wat betaalde en wie wat moet.
    """
    group = Group.query.get_or_404(group_id)
    expenses = Expense.query.filter_by(group_id=group.id).order_by(Expense.created_at.asc()).all()
    payments = Payment.query.filter_by(group_id=group.id, status="confirmed").order_by(Payment.created_at.asc()).all()
    balances = calc_balances(group.id)
    return render_template("ledger.html", group=group, expenses=expenses, payments=payments, balances=balances)

@main.route("/groups/<int:group_id>/export.csv")
@login_required
def export_csv(group_id):
    """
    User-story: export CSV van final balances.
    """
    balances = calc_balances(group_id)
    si = StringIO()
    cw = csv.writer(si, delimiter=';')
    cw.writerow(["user_id", "balance"])
    for uid, bal in balances.items():
        cw.writerow([uid, f"{bal:.2f}"])
    output = si.getvalue().encode("utf-8")
    return Response(output,
                    mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename=group_{group_id}_balances.csv"})

# -----------------------
# Kleine settings / toggles
# -----------------------
@main.route("/groups/<int:group_id>/toggle-public", methods=["POST"])
@login_required
def toggle_public(group_id):
    group = Group.query.get_or_404(group_id)
    if group.created_by_user_id != current_user().id:
        abort(403)
    group.is_public = not group.is_public
    db.session.commit()
    flash(f"Group visibility set to {'public' if group.is_public else 'private'}.", "success")
    return redirect(url_for("main.group_detail", group_id=group.id))
