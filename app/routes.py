from flask import Blueprint, render_template, request, redirect, url_for, session, flash, abort
from datetime import datetime
from decimal import Decimal
import secrets
from app.supabase_client import supabase

main = Blueprint("main", __name__)
 
# -----------------------------
# HELPERS
# -----------------------------
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
            flash("Log eerst in om deze pagina te bekijken.", "warning")
            return redirect(url_for("main.login"))
        return func(*args, **kwargs)
    return wrapper


# -----------------------------
# REGISTER / LOGIN
# -----------------------------
@main.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip().lower()
        phone_number = request.form.get("phone_number", "").strip()
        iban = request.form.get("iban", "").strip()
        password = request.form.get("password", "").strip()

        if not all([name, username, email, phone_number, iban, password]):
            flash("Vul alle velden in.", "danger")
            return redirect(url_for("main.register"))

        existing = supabase.table("users").select("users_id").or_(
            f"email.eq.{email},username.eq.{username}"
        ).execute().data
        if existing:
            flash("Gebruikersnaam of e-mailadres bestaat al.", "danger")
            return redirect(url_for("main.register"))

        new_user = supabase.table("users").insert({
            "name": name,
            "username": username,
            "email": email,
            "phone_number": phone_number,
            "iban": iban,
            "password": password,
            "created_at": datetime.utcnow().isoformat()
        }).execute().data[0]

        session["users_id"] = new_user["users_id"]

        # ✅ Als er een join_code in sessie staat → automatisch toevoegen
        join_code = session.pop("pending_join_code", None)
        if join_code:
            return redirect(url_for("main.join_group", join_code=join_code))

        flash("Registratie succesvol!", "success")
        return redirect(url_for("main.index"))

    return render_template("register.html") 


@main.route("/login", methods=["GET", "POST"]) 
def login():
    if request.method == "POST":
        identifier = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        if not identifier or not password:
            flash("Vul alle velden in.", "danger")
            return redirect(url_for("main.login"))

        res = supabase.table("users").select("*").or_(
            f"email.eq.{identifier.lower()},username.eq.{identifier}"
        ).execute()
        data = res.data or []
        if not data or data[0].get("password") != password:
            flash("Ongeldige inloggegevens.", "danger")
            return redirect(url_for("main.login"))

        session["users_id"] = data[0]["users_id"]

        # ✅ Na login: als er een join-code in sessie staat, meteen joinen
        join_code = session.pop("pending_join_code", None)
        if join_code:
            return redirect(url_for("main.join_group", join_code=join_code))

        flash(f"Welkom terug, {data[0]['username']}!", "success")
        return redirect(url_for("main.index"))

    return render_template("login.html")


@main.route("/logout")
def logout():
    session.clear()
    flash("Je bent uitgelogd.", "info")
    return redirect(url_for("main.login"))


# -----------------------------
# HOME / GROEPEN
# -----------------------------
@main.route("/")
@login_required
def index():
    user = current_user()
    groups = []

    created = supabase.table("groups").select("*").eq("created_by_user_id", user["users_id"]).execute().data or []
    groups.extend(created)

    member_records = supabase.table("group_members").select("*").eq("user_id", user["users_id"]).execute().data or []
    group_ids = [m["group_id"] for m in member_records]
    if group_ids:
        other_groups = (
            supabase.table("groups")
            .select("*")
            .in_("group_id", group_ids)
            .execute().data or []
        )
        for g in other_groups:
            if g not in groups:
                groups.append(g)

    return render_template("index.html", user=user, groups=groups)


@main.route("/groups/new", methods=["GET", "POST"])
@login_required
def create_group():
    user = current_user()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("Geef een groepsnaam in.", "danger")
            return redirect(url_for("main.create_group"))

        join_code = secrets.token_hex(4).upper()
        group_payload = {
            "name": name,
            "currency": "EUR",
            "join_code": join_code,
            "created_by_user_id": user["users_id"],
            "created_at": datetime.utcnow().isoformat(),
        }
        group_resp = supabase.table("groups").insert(group_payload).execute()
        group = group_resp.data[0]

        supabase.table("group_members").insert({
            "group_id": group["group_id"],
            "user_id": user["users_id"],
            "created_at": datetime.utcnow().isoformat()
        }).execute()

        flash("Groep aangemaakt!", "success")
        return redirect(url_for("main.group_detail", group_id=group["group_id"]))

    return render_template("group_new.html")


@main.route("/groups/<int:group_id>")
@login_required
def group_detail(group_id):
    user = current_user()

    g = supabase.table("groups").select("*").eq("group_id", group_id).execute().data
    if not g:
        abort(404)
    group = g[0]

    members = supabase.table("group_members").select("*").eq("group_id", group_id).execute().data or []
    expenses = supabase.table("expenses").select("*").eq("group_id", group_id).execute().data or []
    payments = supabase.table("payments").select("*").eq("group_id", group_id).execute().data or []

    member_list = []
    for m in members:
        u = supabase.table("users").select("username").eq("users_id", m["user_id"]).execute().data
        username = u[0]["username"] if u else f"User {m['user_id']}"
        member_list.append(username)

    balances_named = {}
    if members:
        balances = {m["user_id"]: Decimal("0.00") for m in members}

        for e in expenses:
            total = Decimal(str(e.get("total_amount", 0)))
            share = total / max(1, len(members))
            for m in members:
                balances[m["user_id"]] -= share
            balances[e["created_by_user_id"]] += total

        for p in payments:
            amt = Decimal(str(p.get("amount", 0)))
            balances[p["sender_id"]] -= amt
            balances[p["receiver_id"]] += amt

        for uid, amount in balances.items():
            udata = supabase.table("users").select("username").eq("users_id", uid).execute().data
            uname = udata[0]["username"] if udata else f"User {uid}"
            balances_named[uname] = round(amount, 2)

    join_link = url_for("main.join_group", join_code=group["join_code"], _external=True)
    return render_template(
        "group_detail.html",
        user=user,
        group=group,
        members=member_list,
        balances_named=balances_named,
        join_link=join_link
    )


# -----------------------------
# JOIN GROUP (werkt ook voor niet-ingelogden!)
# -----------------------------
@main.route("/join/<string:join_code>")
def join_group(join_code):
    # ⛔️ Niet ingelogd? Sla join_code op en stuur naar login
    if "users_id" not in session:
        session["pending_join_code"] = join_code
        flash("Log in of registreer om aan de groep toegevoegd te worden.", "info")
        return redirect(url_for("main.login"))

    user = current_user()
    g = supabase.table("groups").select("*").eq("join_code", join_code).execute().data
    if not g:
        flash("Ongeldige of verlopen code.", "danger")
        return redirect(url_for("main.index"))

    group = g[0]
    existing = supabase.table("group_members").select("*").eq("group_id", group["group_id"]).eq("user_id", user["users_id"]).execute().data
    if existing:
        flash("Je bent al lid van deze groep.", "info")
        return redirect(url_for("main.group_detail", group_id=group["group_id"]))

    supabase.table("group_members").insert({
        "group_id": group["group_id"],
        "user_id": user["users_id"],
        "created_at": datetime.utcnow().isoformat()
    }).execute()

    flash(f"Je bent toegevoegd aan de groep '{group['name']}'!", "success")
    return redirect(url_for("main.group_detail", group_id=group["group_id"]))
