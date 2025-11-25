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
# APP-FEE HELPER
# -----------------------------
def ensure_app_fee(group_id, creator_id=None):
    """
    Zorgt dat er één 'App fee' expense bestaat in deze groep
    en dat die gelijk verdeeld is over alle leden.

    - voor groepen met <= 8 leden: totaal 2 EUR
    - voor groepen met >= 9 leden: totaal 5 EUR
    """

    # 1) alle leden ophalen
    gm_rows = (
        supabase.table("group_members")
        .select("user_id")
        .eq("group_id", group_id)
        .execute()
        .data
        or []
    )
    member_ids = [gm["user_id"] for gm in gm_rows]
    member_count = len(member_ids) or 1

    # 2) totale fee bepalen
    fee_total = Decimal("2.00") if member_count <= 8 else Decimal("5.00")

    # 3) bestaande app-fee zoeken (vaste description, NIET vertalen)
    app_exp_res = (
        supabase.table("expenses")
        .select("*")
        .eq("group_id", group_id)
        .eq("description", "App fee")
        .execute()
    )
    app_exp_data = app_exp_res.data or []

    if app_exp_data:
        expense = app_exp_data[0]
        expense_id = expense["expense_id"]
        supabase.table("expenses").update(
            {"total_amount": float(fee_total)}
        ).eq("expense_id", expense_id).execute()
    else:
        expense = (
            supabase.table("expenses")
            .insert(
                {
                    "group_id": group_id,
                    "created_by_user_id": creator_id,
                    "description": "App fee",  # NIET vertalen
                    "total_amount": float(fee_total),
                    "created_at": datetime.utcnow().isoformat(),
                    "is_active": True,
                }
            )
            .execute()
            .data[0]
        )
        expense_id = expense["expense_id"]

    # 4) bestaande shares verwijderen
    supabase.table("expense_shares").delete().eq(
        "expense_id", expense_id
    ).execute()

    # 5) shares opnieuw gelijk verdelen
    per_person = (fee_total / member_count).quantize(Decimal("0.01"))

    rows = []
    for uid in member_ids:
        rows.append(
            {
                "expense_id": expense_id,
                "group_id": group_id,
                "user_id": uid,
                "amount": float(per_person),
                "created_at": datetime.utcnow().isoformat(),
            }
        )

    if rows:
        supabase.table("expense_shares").insert(rows).execute()


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
        return redirect(url_for("main.dashboard"))

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
        return redirect(url_for("main.dashboard"))

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
# HOME / LANDING + DASHBOARD
# -----------------------------
@main.route("/")
def home():
    """
    Publieke landing page.
    Als de gebruiker al ingelogd is -> stuur naar dashboard.
    """
    user = current_user()
    if user:
        return redirect(url_for("main.dashboard"))
    return render_template("home.html")


@main.route("/dashboard")
@login_required
def dashboard():
    """
    Jouw oude index/dashboard met groepen.
    """
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


# -----------------------------
# GROUPS – AANMAKEN
# -----------------------------
@main.route("/groups/new", methods=["GET", "POST"])
@login_required
def create_group():
    user = current_user()
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        start_date = request.form.get("start_date", "").strip()
        end_date = request.form.get("end_date", "").strip()
        max_members_raw = request.form.get("max_members", "").strip()

        if not name:
            flash(_("Geef een groepsnaam in."), "danger")
            return redirect(url_for("main.create_group"))

        # optioneel: max members
        try:
            max_members = int(max_members_raw) if max_members_raw else None
        except ValueError:
            max_members = None

        join_code = secrets.token_hex(4).upper()
        now_iso = datetime.utcnow().isoformat()

        # --- bepaal app fee: < 8 leden = 2 EUR, anders 5 EUR ---
        if max_members and max_members >= 8:
            app_fee_amount = 5.0
        else:
            app_fee_amount = 2.0

        # 1) groep aanmaken
        group_payload = {
            "name": name,
            "currency": "EUR",
            "join_code": join_code,
            "created_by_user_id": user["users_id"],
            "created_at": now_iso,
            "start_date": start_date or None,
            "end_date": end_date or None,
            "max_members": max_members,
            "app_fee_amount": app_fee_amount,
        }
        group_resp = supabase.table("groups").insert(group_payload).execute()
        group = group_resp.data[0]

        # 2) maker als lid toevoegen
        supabase.table("group_members").insert(
            {
                "group_id": group["group_id"],
                "user_id": user["users_id"],
                "created_at": now_iso,
            }
        ).execute()

        # 3) App-fee expense aanmaken (flag voor UI)
        supabase.table("expenses").insert(
            {
                "group_id": group["group_id"],
                "created_by_user_id": user["users_id"],
                "description": "App fee",
                "total_amount": app_fee_amount,
                "created_at": now_iso,
                "is_active": True,
                "is_app_fee": True,
            }
        ).execute()

        flash(_("Groep aangemaakt!"), "success")
        return redirect(url_for("main.group_detail", group_id=group["group_id"]))

    # GET
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
            .select("users_id, username, email")
            .eq("users_id", gm["user_id"])
            .execute()
            .data
        )
        if u:
            members.append(u[0])

    # map user_id -> username (voor saldi + lijsten)
    username_map = {m["users_id"]: m["username"] for m in members}

    # 3) Uitgaven ophalen (incl. app fee)
    expenses_rows = (
        supabase.table("expenses")
        .select("*")
        .eq("group_id", group_id)
        .eq("is_active", True)
        .execute()
        .data
        or []
    )

    app_fee_expense = None
    normal_expenses = []
    for exp in expenses_rows:
        if exp.get("is_app_fee"):
            if app_fee_expense is None or exp["created_at"] > app_fee_expense["created_at"]:
                app_fee_expense = exp
        else:
            normal_expenses.append(exp)

    # 4) Shares ophalen
    shares_rows = (
        supabase.table("expense_shares")
        .select("*")
        .eq("group_id", group_id)
        .execute()
        .data
        or []
    )

    shares_by_expense = {}
    for s in shares_rows:
        eid = s["expense_id"]
        shares_by_expense.setdefault(eid, []).append(
            {
                "user_id": s["user_id"],
                "username": username_map.get(s["user_id"], _("Onbekend")),
                "amount": s["amount"],
            }
        )

    # 5) Saldi berekenen  (incl. app fee)
    balances = {m["users_id"]: Decimal("0.00") for m in members}

    for exp in expenses_rows:
        creator_id = exp["created_by_user_id"]
        total = Decimal(str(exp["total_amount"] or 0))
        eid = exp["expense_id"]

        # 5a) App fee: altijd equal split
        if exp.get("is_app_fee"):
            count = max(1, len(members))
            equal_share = total / count
            for m in members:
                balances[m["users_id"]] -= equal_share
            balances[creator_id] += total
            continue

        # 5b) Gewone expenses
        exp_shares = shares_by_expense.get(eid)

        if exp_shares:  # custom / expliciete shares
            for sh in exp_shares:
                uid = sh["user_id"]
                val = Decimal(str(sh["amount"]))
                balances[uid] -= val
            balances[creator_id] += total
        else:  # equal split fallback
            count = max(1, len(members))
            equal_share = total / count
            for m in members:
                balances[m["users_id"]] -= equal_share
            balances[creator_id] += total

    balance_rows = []
    for uid, amount in balances.items():
        balance_rows.append(
            {
                "user_id": uid,
                "username": username_map.get(uid, _("Unknown")),
                "balance": float(amount),
            }
        )

    # 6) Uitgavenlijst voor de tabel (zonder app fee)
    expense_list = []
    for exp in normal_expenses:
        eid = exp["expense_id"]
        expense_list.append(
            {
                "expense_id": eid,
                "description": exp["description"],
                "total_amount": exp["total_amount"],
                "created_at": exp["created_at"],
                "payer_username": username_map.get(exp["created_by_user_id"], _("Onbekend")),
                "shares": shares_by_expense.get(eid, []),
            }
        )

    # 7) Betalingen ophalen
    payments_rows = (
        supabase.table("payments")
        .select("*")
        .eq("group_id", group_id)
        .execute()
        .data
        or []
    )

    payments = []
    for p in payments_rows:
        payments.append(
            {
                "created_at": p["created_at"],
                "amount": p["amount"],
                "sender_username": username_map.get(p["sender_user_id"], _("Onbekend")),
                "receiver_username": username_map.get(p["receiver_user_id"], _("Onbekend")),
            }
        )

    return render_template(
        "group_detail.html",
        user=user,
        group=group,
        members=members,
        balances=balance_rows,
        expenses=expense_list,
        payments=payments,
        app_fee_expense=app_fee_expense,
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
        return redirect(url_for("main.dashboard"))

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
        return redirect(url_for("main.dashboard"))

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

    # App fee opnieuw verdelen over alle leden
    ensure_app_fee(group["group_id"], creator_id=group["created_by_user_id"])

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
        return redirect(url_for("main.dashboard"))

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
    return redirect(request.referrer or url_for("main.home"))


# -----------------------------
# LEDGER (TODO)
# -----------------------------
@main.route("/groups/<int:group_id>/ledger")
@login_required
def ledger(group_id):
    # TODO: implement ledger
    return render_template("ledger.html", group_id=group_id)

