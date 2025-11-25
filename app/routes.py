# app/routes.py
from flask import (
    Blueprint,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
    abort,
)
from datetime import datetime
from decimal import Decimal
import secrets

from flask_babel import gettext as _  # ✅ i18n

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
            flash(_("Log eerst in om deze pagina te bekijken."), "warning")
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
            flash(_("Vul alle velden in."), "danger")
            return redirect(url_for("main.register"))

        existing = (
            supabase.table("users")
            .select("users_id")
            .or_(f"email.eq.{email},username.eq.{username}")
            .execute()
            .data
        )
        if existing:
            flash(_("Gebruikersnaam of e-mailadres bestaat al."), "danger")
            return redirect(url_for("main.register"))

        new_user = (
            supabase.table("users")
            .insert(
                {
                    "name": name,
                    "username": username,
                    "email": email,
                    "phone_number": phone_number,
                    "iban": iban,
                    "password": password,  # (later: hashen!)
                    "created_at": datetime.utcnow().isoformat(),
                }
            )
            .execute()
            .data[0]
        )

        session["users_id"] = new_user["users_id"]

        # pending join-code na registratie
        join_code = session.pop("pending_join_code", None)
        if join_code:
            return redirect(url_for("main.join_group", join_code=join_code))

        flash(_("Registratie succesvol!"), "success")
        return redirect(url_for("main.index"))

    return render_template("register.html")


@main.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        identifier = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        if not identifier or not password:
            flash(_("Vul alle velden in."), "danger")
            return redirect(url_for("main.login"))

        res = (
            supabase.table("users")
            .select("*")
            .or_(f"email.eq.{identifier.lower()},username.eq.{identifier}")
            .execute()
        )
        data = res.data or []
        if not data or data[0].get("password") != password:
            flash(_("Ongeldige inloggegevens."), "danger")
            return redirect(url_for("main.login"))

        session["users_id"] = data[0]["users_id"]

        # pending join-code na login
        join_code = session.pop("pending_join_code", None)
        if join_code:
            return redirect(url_for("main.join_group", join_code=join_code))

        flash(_("Welkom terug, %(username)s!", username=data[0]["username"]), "success")
        return redirect(url_for("main.index"))

    return render_template("login.html")


@main.route("/logout")
def logout():
    # bewaar taalkeuze
    lang = session.get("lang", "nl")

    session.clear()

    # zet taal terug
    session["lang"] = lang

    flash(_("Je bent uitgelogd."), "info")
    return redirect(url_for("main.login"))


# -----------------------------
# HOME / GROEPEN
# -----------------------------
@main.route("/")
@login_required
def index():
    user = current_user()
    groups = []

    # Groepen die de user zelf heeft aangemaakt
    created = (
        supabase.table("groups")
        .select("*")
        .eq("created_by_user_id", user["users_id"])
        .execute()
        .data
        or []
    )
    groups.extend(created)

    # Groepen waar hij/zij lid van is
    member_records = (
        supabase.table("group_members")
        .select("*")
        .eq("user_id", user["users_id"])
        .execute()
        .data
        or []
    )
    group_ids = [m["group_id"] for m in member_records]
    if group_ids:
        other_groups = (
            supabase.table("groups")
            .select("*")
            .in_("group_id", group_ids)
            .execute()
            .data
            or []
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
            flash(_("Geef een groepsnaam in."), "danger")
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

        supabase.table("group_members").insert(
            {
                "group_id": group["group_id"],
                "user_id": user["users_id"],
                "created_at": datetime.utcnow().isoformat(),
            }
        ).execute()

        flash(_("Groep aangemaakt!"), "success")
        return redirect(url_for("main.group_detail", group_id=group["group_id"]))

    return render_template("group_new.html")


# -----------------------------
# GROUP DETAIL (saldo + uitgaven + shares)
# -----------------------------
@main.route("/groups/<int:group_id>")
@login_required
def group_detail(group_id):
    user = current_user()

    # 1) Groep ophalen
    g = (
        supabase.table("groups")
        .select("*")
        .eq("group_id", group_id)
        .execute()
        .data
    )
    if not g:
        abort(404)
    group = g[0]

    # 2) Leden ophalen
    gm_rows = (
        supabase.table("group_members")
        .select("*")
        .eq("group_id", group_id)
        .execute()
        .data
        or []
    )

    members = []
    for gm in gm_rows:
        u = (
            supabase.table("users")
            .select("users_id, username")
            .eq("users_id", gm["user_id"])
            .execute()
            .data
        )
        if u:
            members.append(u[0])

    username_map = {m["users_id"]: m["username"] for m in members}

    # 3) Uitgaven ophalen
    expenses = (
        supabase.table("expenses")
        .select("*")
        .eq("group_id", group_id)
        .eq("is_active", True)
        .execute()
        .data
        or []
    )

    # 4) Shares ophalen
    shares = (
        supabase.table("expense_shares")
        .select("*")
        .eq("group_id", group_id)
        .execute()
        .data
        or []
    )

    # shares per expense_id
    shares_by_expense = {}
    for s in shares:
        eid = s["expense_id"]
        shares_by_expense.setdefault(eid, [])
        shares_by_expense[eid].append(
            {
                "user_id": s["user_id"],
                "username": username_map.get(s["user_id"], _("Onbekend")),
                "amount": s["amount"],
            }
        )

    # 5) Saldo-berekening
    balances = {m["users_id"]: Decimal("0.00") for m in members}

    for exp in expenses:
        creator_id = exp["created_by_user_id"]
        total = Decimal(str(exp["total_amount"]))
        eid = exp["expense_id"]

        if eid in shares_by_expense:
            # custom verdeling
            for sh in shares_by_expense[eid]:
                uid = sh["user_id"]
                val = Decimal(str(sh["amount"]))
                balances[uid] -= val
            balances[creator_id] += total
        else:
            # gelijke verdeling
            count = max(1, len(members))
            equal_share = total / count
            for m in members:
                balances[m["users_id"]] -= equal_share
            balances[creator_id] += total

    balances_named = {
        username_map[uid]: float(amount)
        for uid, amount in balances.items()
    }

    join_link = url_for(
        "main.join_group",
        join_code=group["join_code"],
        _external=True
    )

    expense_list = []
    for exp in expenses:
        eid = exp["expense_id"]
        expense_list.append(
            {
                "expense_id": eid,
                "description": exp["description"],
                "total_amount": exp["total_amount"],
                "created_at": exp["created_at"],
                "creator": username_map.get(exp["created_by_user_id"], _("Onbekend")),
                "shares": shares_by_expense.get(eid, []),
            }
        )

    return render_template(
        "group_detail.html",
        user=user,
        group=group,
        members=[m["username"] for m in members],
        balances_named=balances_named,
        join_link=join_link,
        expenses=expense_list,
    )


# -----------------------------
# JOIN GROUP (via dashboard form)
# -----------------------------
@main.route("/join", methods=["POST"])
@login_required
def join_group_form():
    code = request.form.get("join_code", "").strip().upper()

    if not code:
        flash(_("Vul een groepscode in om te joinen."), "warning")
        return redirect(url_for("main.index"))

    return redirect(url_for("main.join_group", join_code=code))


# -----------------------------
# JOIN GROUP (link)
# -----------------------------
@main.route("/join/<string:join_code>")
def join_group(join_code):
    join_code = (join_code or "").strip().upper()

    if "users_id" not in session:
        session["pending_join_code"] = join_code
        flash(_("Log in of registreer om aan de groep toegevoegd te worden."), "info")
        return redirect(url_for("main.login"))

    user = current_user()

    g = (
        supabase.table("groups")
        .select("*")
        .eq("join_code", join_code)
        .execute()
        .data
    )
    if not g:
        flash(
            _("Geen groep gevonden met deze code. Controleer de code en probeer opnieuw."),
            "danger",
        )
        return redirect(url_for("main.index"))

    group = g[0]

    existing = (
        supabase.table("group_members")
        .select("*")
        .eq("group_id", group["group_id"])
        .eq("user_id", user["users_id"])
        .execute()
        .data
    )
    if existing:
        flash(_("Je bent al lid van '%(name)s'.", name=group["name"]), "info")
        return redirect(url_for("main.group_detail", group_id=group["group_id"]))

    supabase.table("group_members").insert(
        {
            "group_id": group["group_id"],
            "user_id": user["users_id"],
            "created_at": datetime.utcnow().isoformat(),
        }
    ).execute()

    flash(_("Je bent toegevoegd aan de groep '%(name)s' 🎉", name=group["name"]), "success")
    return redirect(url_for("main.group_detail", group_id=group["group_id"]))


# -----------------------------
# EXPENSES – UITGAVEN
# -----------------------------
@main.route("/group/<int:group_id>/expense/new", methods=["GET", "POST"])
@login_required
def expense_new(group_id):
    uid = session.get("users_id")

    membership = (
        supabase.table("group_members")
        .select("*")
        .eq("group_id", group_id)
        .eq("user_id", uid)
        .execute()
        .data
        or []
    )
    if not membership:
        flash(_("Je hebt geen toegang tot deze groep."), "danger")
        return redirect(url_for("main.index"))

    g = (
        supabase.table("groups")
        .select("*")
        .eq("group_id", group_id)
        .execute()
        .data
    )
    group = g[0] if g else None

    gm_rows = (
        supabase.table("group_members")
        .select("*")
        .eq("group_id", group_id)
        .execute()
        .data
        or []
    )

    members = []
    for gm in gm_rows:
        u = (
            supabase.table("users")
            .select("users_id, username")
            .eq("users_id", gm["user_id"])
            .execute()
            .data
        )
        if u:
            members.append(
                {"user_id": u[0]["users_id"], "username": u[0]["username"]}
            )

    if request.method == "POST":
        description = request.form.get("description", "").strip()
        amount_raw = request.form.get("amount", "").strip()

        if not description or not amount_raw:
            flash(_("Vul alle velden in."), "danger")
            return redirect(request.url)

        try:
            total_amount = float(amount_raw.replace(",", "."))
        except ValueError:
            flash(_("Bedrag moet een getal zijn."), "danger")
            return redirect(request.url)

        if total_amount <= 0:
            flash(_("Bedrag moet groter zijn dan 0."), "danger")
            return redirect(request.url)

        shares = []
        for m in members:
            field_name = f"share_{m['user_id']}"
            share_raw = request.form.get(field_name, "").strip()

            if not share_raw:
                continue

            try:
                share_val = float(share_raw.replace(",", "."))
            except ValueError:
                flash(
                    _("Bedrag bij %(user)s is geen geldig getal.", user=m["username"]),
                    "danger",
                )
                return redirect(request.url)

            if share_val < 0:
                flash(
                    _("Bedrag bij %(user)s mag niet negatief zijn.", user=m["username"]),
                    "danger",
                )
                return redirect(request.url)

            if share_val > 0:
                shares.append({"user_id": m["user_id"], "amount": share_val})

        if shares:
            sum_shares = sum(s["amount"] for s in shares)
            if abs(sum_shares - total_amount) > 0.01:
                flash(
                    _(
                        "De som van de individuele bedragen (%(sum).2f) komt niet overeen met het totaal (%(total).2f).",
                        sum=sum_shares,
                        total=total_amount,
                    ),
                    "danger",
                )
                return redirect(request.url)

        exp_resp = (
            supabase.table("expenses")
            .insert(
                {
                    "group_id": group_id,
                    "created_by_user_id": uid,
                    "description": description,
                    "total_amount": total_amount,
                    "created_at": datetime.utcnow().isoformat(),
                    "is_active": True,
                }
            )
            .execute()
        )

        if not exp_resp.data:
            flash(_("Kon uitgave niet opslaan."), "danger")
            return redirect(request.url)

        expense = exp_resp.data[0]
        expense_id = expense["expense_id"]

        if shares:
            rows = []
            for s in shares:
                rows.append(
                    {
                        "expense_id": expense_id,
                        "group_id": group_id,
                        "user_id": s["user_id"],
                        "amount": s["amount"],
                        "created_at": datetime.utcnow().isoformat(),
                    }
                )
            supabase.table("expense_shares").insert(rows).execute()

        flash(_("Uitgave toegevoegd!"), "success")
        return redirect(url_for("main.group_detail", group_id=group_id))

    return render_template(
        "expense_new.html",
        group=group,
        group_id=group_id,
        members=members,
    )


# -----------------------------
# TAAL SWITCH
# -----------------------------
@main.route("/set-lang/<lang>")
def set_language(lang):
    if lang not in ["nl", "en"]:
        lang = "nl"
    session["lang"] = lang
    return redirect(request.referrer or url_for("main.index"))
