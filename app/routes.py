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
import re


from flask_babel import gettext as _  # ✅ i18n

from app.supabase_client import supabase

import os
from openai import OpenAI

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))



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
# CATEGORIEËN
# -----------------------------
CATEGORY_IDS = [
    "transport",
    "food",
    "accommodation",
    "activities",
    "shopping",
]

CATEGORY_LABELS = {
    "transport": _("Transport"),
    "food": _("Eten & drinken"),
    "accommodation": _("Accommodatie"),
    "activities": _("Activiteiten"),
    "shopping": _("Winkelen"),
}


def category_label(cat: str) -> str:
    """
    Map een category-id naar een leesbaar label.
    Onbekende dingen -> 'activiteiten' als veilige default.
    """
    if cat not in CATEGORY_IDS:
        cat = "activities"
    return CATEGORY_LABELS[cat]


# -----------------------------
# NORMALISATIE & KEYWORD-FALLBACK
# -----------------------------
STOPWORDS = {
    "de", "het", "een", "en", "of", "voor", "met", "op",
    "in", "the", "a", "an", "to", "for", "and"
}


def normalize_description(description: str) -> str:
    """
    Maakt beschrijvingen consistent zodat caching goed werkt.

    - lowercasing
    - trimmen
    - speciale tekens/emoji eraf
    - stopwoorden en ultra-korte woorden weg
    """
    if not description:
        return ""

    text = description.lower().strip()

    # alles wat geen letter/cijfer/underscore/space is -> spatie
    text = re.sub(r"[^\w\s]", " ", text)
    # meerdere spaties -> één
    text = re.sub(r"\s+", " ", text)

    words = []
    for w in text.split():
        if w in STOPWORDS:
            continue
        if len(w) <= 2:
            continue
        words.append(w)

    return " ".join(words)


def fallback_category_keywords(description: str) -> str:
    """
    Eenvoudige keyword-classifier (zonder AI) als backup.
    description wordt best al genormaliseerd.
    """
    if not description:
        return "activities"

    d = description.lower()

    # transport
    if any(k in d for k in [
        "trein", "train", "bus", "tram", "metro",
        "vliegtuig", "vlucht", "flight", "uber", "taxi", "rit", "fuel", "benzine"
    ]):
        return "transport"

    # eten & drinken
    if any(k in d for k in [
        "restaurant", "eten", "food", "diner", "lunch", "ontbijt",
        "drank", "drinks", "bier", "beer", "pizza", "burger", "meal", "snack"
    ]):
        return "food"

    # accommodatie
    if any(k in d for k in [
        "hotel", "airbnb", "bnb", "hostel", "overnachting",
        "kamer", "room", "apartment", "appartement"
    ]):
        return "accommodation"

    # shoppen
    if any(k in d for k in [
        "shopping", "winkel", "boodschappen", "groceries",
        "souvenir", "souvenirs", "cadeau", "cadeautje", "kopen"
    ]):
        return "shopping"

    # default: activiteiten
    return "activities"


# -----------------------------
# AI + LOKALE CACHE
# -----------------------------
def infer_category_ai(description: str, raw_category: str | None = None) -> str:
    """
    Bepaalt de categorie met AI + keyword fallback + lokale Supabase-cache.

    Logica:
    1. Als de user expliciet een geldige categorie kiest -> gebruik die.
    2. Description normaliseren (voor caching).
    3. Eerst in lokale Supabase-tabel `category_cache` kijken.
    4. Anders OpenAI aanroepen met vaste prompt-structuur.
    5. Als AI iets anders dan 1 van CATEGORY_IDS teruggeeft -> keyword fallback.
    6. Resultaat in lokale cache steken voor volgende keren.
    """

    # 1) User override
    if raw_category and raw_category in CATEGORY_IDS:
        print(">>> infer_category_ai: USER OVERRIDE =", raw_category)
        return raw_category

    # 2) Normaliseren
    norm_desc = normalize_description(description or "")
    print(">>> infer_category_ai: START")
    print("    raw description =", repr(description))
    print("    normalized      =", repr(norm_desc))

    if not norm_desc:
        # geen zinnige tekst -> pak veilige default
        print(">>> infer_category_ai: EMPTY DESCRIPTION -> 'activities'")
        return "activities"

    # 3) Lokale cache checken
    try:
        cache_rows = (
            supabase.table("category_cache")
            .select("category")
            .eq("normalized_desc", norm_desc)
            .execute()
            .data
            or []
        )
        if cache_rows:
            cached_cat = cache_rows[0]["category"]
            if cached_cat in CATEGORY_IDS:
                print(">>> infer_category_ai: HIT LOCAL CACHE ->", cached_cat)
                return cached_cat
            else:
                print(">>> infer_category_ai: CACHE INVALID CAT ->", cached_cat)
    except Exception as e:
        print(">>> infer_category_ai: cache lookup ERROR:", e)

    # 4) AI-call met vaste prompt-structuur
    prompt = f"""
CATEGORY_CLASSIFIER
DESC="{norm_desc}"
VALID_IDS: transport | food | accommodation | activities | shopping

Geef EXACT één category-id terug uit VALID_IDS.
Antwoord met enkel dat ene woord, zonder extra tekst.
"""

    try:
        print(">>> infer_category_ai: CALLING OPENAI...")
        resp = client.responses.create(
            model="gpt-4.1-mini",
            input=prompt,
            max_output_tokens=32,
        )

        print(">>> infer_category_ai: RAW RESPONSE =", resp)

        text = resp.output[0].content[0].text.strip().lower()
        print(">>> infer_category_ai: MODEL TEXT =", repr(text))

        ai_cat = text.split()[0]
        print(">>> infer_category_ai: ai_cat (parsed) =", ai_cat)

        if ai_cat not in CATEGORY_IDS:
            print(">>> infer_category_ai: ai_cat NOT IN CATEGORY_IDS -> use keyword fallback")
            kw_cat = fallback_category_keywords(norm_desc)
            final_cat = kw_cat
        else:
            final_cat = ai_cat

        print(">>> infer_category_ai: FINAL =", final_cat)

    except Exception as e:
        print(">>> infer_category_ai: ERROR while calling OpenAI:", e)
        # pure keyword fallback
        final_cat = fallback_category_keywords(norm_desc)
        print(">>> infer_category_ai: FALLBACK kw_cat =", final_cat)

    # 5) in lokale cache steken (best effort)
    try:
        supabase.table("category_cache").insert(
            {
                "normalized_desc": norm_desc,
                "category": final_cat,
            }
        ).execute()
        print(">>> infer_category_ai: cached", norm_desc, "->", final_cat)
    except Exception as e:
        # als hij al bestaat (unique constraint), is dat niet erg
        print(">>> infer_category_ai: cache insert ERROR:", e)

    return final_cat




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
                    "is_app_fee": True,
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
# HOME (landing page) + DASHBOARD
# -----------------------------
@main.route("/")
def home():
    """
    Publieke landing page.
    Altijd de homepage tonen, ook als je ingelogd bent.
    """
    return render_template("home.html")


@main.route("/dashboard")
@login_required
def dashboard():
    """
    Dashboard met overzicht van alle groepen.
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

    # 3) Uitgaven ophalen (incl. app fee) – NIEUWSTE EERST
    expenses_rows = (
        supabase.table("expenses")
        .select("*")
        .eq("group_id", group_id)
        .eq("is_active", True)
        .order("created_at", desc=True)
        .execute()
        .data
        or []
    )

    app_fee_expense = None
    normal_expenses = []
    for exp in expenses_rows:
        if exp.get("is_app_fee"):
            if (
                app_fee_expense is None
                or exp["created_at"] > app_fee_expense["created_at"]
            ):
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

    # 6) Uitgavenlijst voor de hero/laatste uitgaven (zonder app fee)
    expense_list = []
    for exp in normal_expenses:
        eid = exp["expense_id"]

        # categorie uit DB (voor oude records kan dit None of 'other' zijn)
        stored_cat = (exp.get("category") or "").lower()
        if stored_cat not in CATEGORY_IDS:
            stored_cat = "activities"
        cat = stored_cat

        expense_list.append(
            {
                "expense_id": eid,
                "description": exp["description"],
                "total_amount": exp["total_amount"],
                "created_at": exp["created_at"],
                "payer_username": username_map.get(
                    exp["created_by_user_id"], _("Onbekend")
                ),
                "shares": shares_by_expense.get(eid, []),
                "category": cat,
                "category_label": category_label(cat),
            }
        )

    # sorteer op datum (nieuwste eerst) zodat template gewoon expenses[:3] kan nemen
    expense_list.sort(key=lambda e: e["created_at"], reverse=True)

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
                "sender_username": username_map.get(
                    p["sender_user_id"], _("Onbekend")
                ),
                "receiver_username": username_map.get(
                    p["receiver_user_id"], _("Onbekend")
                ),
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
        CATEGORY_LABELS=CATEGORY_LABELS,
    )


# -----------------------------
# GROUP EXPENSES – VOLLEDIGE LIJST
# -----------------------------
@main.route("/groups/<int:group_id>/expenses")
@login_required
def group_expenses(group_id):
    user = current_user()
    if not user:
        abort(403)

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

    username_map = {m["users_id"]: m["username"] for m in members}

    # 3) Uitgaven ophalen (incl. app fee) – NIEUWSTE EERST
    expenses_rows = (
        supabase.table("expenses")
        .select("*")
        .eq("group_id", group_id)
        .eq("is_active", True)
        .order("created_at", desc=True)
        .execute()
        .data
        or []
    )

    app_fee_expense = None
    normal_expenses = []
    for exp in expenses_rows:
        if exp.get("is_app_fee"):
            if (
                app_fee_expense is None
                or exp["created_at"] > app_fee_expense["created_at"]
            ):
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

    # 5) Lijst voor template (incl. categorie)
    expense_list = []
    for exp in normal_expenses:
        eid = exp["expense_id"]

        stored_cat = (exp.get("category") or "").lower()
        if stored_cat not in CATEGORY_IDS:
            stored_cat = "activities"
        cat = stored_cat

        expense_list.append(
            {
                "expense_id": eid,
                "description": exp["description"],
                "total_amount": exp["total_amount"],
                "created_at": exp["created_at"],
                "payer_username": username_map.get(
                    exp["created_by_user_id"], _("Onbekend")
                ),
                "shares": shares_by_expense.get(eid, []),
                "category": cat,
                "category_label": category_label(cat),
            }
        )

    # sorteren (nieuwste eerst)
    expense_list.sort(key=lambda e: e["created_at"], reverse=True)

    return render_template(
        "group_expenses.html",
        user=user,
        group=group,
        members=members,
        expenses=expense_list,
        app_fee_expense=app_fee_expense,
        CATEGORY_LABELS=CATEGORY_LABELS,
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
            _(
                "Geen groep gevonden met deze code. Controleer de code en probeer opnieuw."
            ),
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
# EXPENSES – NIEUWE UITGAVE
# -----------------------------
@main.route("/group/<int:group_id>/expense/new", methods=["GET", "POST"])
@login_required
def expense_new(group_id):
    uid = session.get("users_id")

    # Check of de user lid is van de groep
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

    # Groep ophalen
    g = (
        supabase.table("groups")
        .select("*")
        .eq("group_id", group_id)
        .execute()
        .data
    )
    group = g[0] if g else None

    # Leden ophalen
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

    # ---------------- POST: nieuwe uitgave ----------------
    if request.method == "POST":
        description = request.form.get("description", "").strip()
        amount_raw = request.form.get("amount", "").strip()
        raw_category = request.form.get("category")  # kan leeg zijn

        print(">>> expense_new POST: description =", repr(description))
        print(">>> expense_new POST: amount_raw  =", repr(amount_raw))
        print(">>> expense_new POST: raw_category (from form) =", repr(raw_category))

        if not description or not amount_raw:
            flash(_("Vul alle velden in."), "danger")
            return redirect(request.url)

        # bedrag parsen
        try:
            total_amount = float(amount_raw.replace(",", "."))
        except ValueError:
            flash(_("Bedrag moet een getal zijn."), "danger")
            return redirect(request.url)

        if total_amount <= 0:
            flash(_("Bedrag moet groter zijn dan 0."), "danger")
            return redirect(request.url)

        # shares verzamelen
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

        # check of som van shares klopt
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

        # categorie laten bepalen door AI + fallback
        guessed_cat = infer_category_ai(description, raw_category)
        print(">>> expense_new POST: guessed_cat =", guessed_cat)

        now_iso = datetime.utcnow().isoformat()
        exp_resp = (
            supabase.table("expenses")
            .insert(
                {
                    "group_id": group_id,
                    "created_by_user_id": uid,
                    "description": description,
                    "total_amount": total_amount,
                    "created_at": now_iso,
                    "is_active": True,
                    "category": guessed_cat,
                }
            )
            .execute()
        )

        if not exp_resp.data:
            flash(_("Kon uitgave niet opslaan."), "danger")
            return redirect(request.url)

        expense = exp_resp.data[0]
        expense_id = expense["expense_id"]
        print(">>> expense_new POST: saved expense, id =", expense_id)

        # shares wegschrijven
        if shares:
            rows = []
            for s in shares:
                rows.append(
                    {
                        "expense_id": expense_id,
                        "group_id": group_id,
                        "user_id": s["user_id"],
                        "amount": s["amount"],
                        "created_at": now_iso,
                    }
                )
            supabase.table("expense_shares").insert(rows).execute()

        flash(_("Uitgave toegevoegd!"), "success")
        return redirect(url_for("main.group_detail", group_id=group_id))

    # ---------------- GET: formulier tonen ----------------
    return render_template(
        "expense_new.html",
        group=group,
        group_id=group_id,
        members=members,
        CATEGORY_IDS=CATEGORY_IDS,
        CATEGORY_LABELS=CATEGORY_LABELS,
        expense=None,
        share_map={},
        is_edit=False,
        form_action=url_for("main.expense_new", group_id=group_id),
    )

# -----------------------------
# EXPENSES – BEWERKEN
# -----------------------------
@main.route("/group/<int:group_id>/expense/<int:expense_id>/edit", methods=["GET", "POST"])
@login_required
def expense_edit(group_id, expense_id):
    user = current_user()
    if not user:
        abort(403)

    uid = user["users_id"]

    # check: lid van groep?
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

    # groep ophalen
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

    # bestaande expense ophalen
    exp_rows = (
        supabase.table("expenses")
        .select("*")
        .eq("group_id", group_id)
        .eq("expense_id", expense_id)
        .eq("is_active", True)
        .execute()
        .data
        or []
    )
    if not exp_rows:
        abort(404)
    expense = exp_rows[0]

    # geen edit/delete op app-fee
    if expense.get("is_app_fee"):
        flash(_("De app-fee kan je niet aanpassen."), "warning")
        return redirect(url_for("main.ledger", group_id=group_id))

    # alleen maker mag bewerken
    if expense["created_by_user_id"] != uid:
        flash(_("Je kan enkel je eigen uitgaven aanpassen."), "danger")
        return redirect(url_for("main.ledger", group_id=group_id))

    # leden ophalen
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

    # bestaande shares ophalen
    share_rows = (
        supabase.table("expense_shares")
        .select("*")
        .eq("expense_id", expense_id)
        .execute()
        .data
        or []
    )
    share_map = {s["user_id"]: s["amount"] for s in share_rows}

    # ---------- POST: update ----------
    if request.method == "POST":
        description = request.form.get("description", "").strip()
        amount_raw = request.form.get("amount", "").strip()
        raw_category = request.form.get("category")  # kan leeg zijn

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

        # shares verzamelen
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

        # categorie via AI
        guessed_cat = infer_category_ai(description, raw_category)

        now_iso = datetime.utcnow().isoformat()

        # expense updaten
        supabase.table("expenses").update(
            {
                "description": description,
                "total_amount": total_amount,
                "category": guessed_cat,
                "updated_at": now_iso,
            }
        ).eq("expense_id", expense_id).eq("group_id", group_id).execute()

        # oude shares weg + nieuwe schrijven
        supabase.table("expense_shares").delete().eq(
            "expense_id", expense_id
        ).execute()

        if shares:
            rows = []
            for s in shares:
                rows.append(
                    {
                        "expense_id": expense_id,
                        "group_id": group_id,
                        "user_id": s["user_id"],
                        "amount": s["amount"],
                        "created_at": now_iso,
                    }
                )
            supabase.table("expense_shares").insert(rows).execute()

        flash(_("Uitgave bijgewerkt!"), "success")
        return redirect(url_for("main.ledger", group_id=group_id))

    # ---------- GET: formulier tonen ----------
    return render_template(
        "expense_new.html",
        group=group,
        group_id=group_id,
        members=members,
        CATEGORY_IDS=CATEGORY_IDS,
        CATEGORY_LABELS=CATEGORY_LABELS,
        expense=expense,
        share_map=share_map,
        is_edit=True,
        form_action=url_for("main.expense_edit", group_id=group_id, expense_id=expense_id),
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
# LEDGER – VOLLEDIGE HISTORIEK
# -----------------------------
@main.route("/groups/<int:group_id>/ledger")
@login_required
def ledger(group_id):
    user = current_user()
    if not user:
        abort(403)

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

    # 2) Leden ophalen (voor namen)
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

    username_map = {m["users_id"]: m["username"] for m in members}

    # 3) Alle actieve uitgaven – NIEUWSTE EERST
    expenses_rows = (
        supabase.table("expenses")
        .select("*")
        .eq("group_id", group_id)
        .eq("is_active", True)
        .order("created_at", desc=True)
        .execute()
        .data
        or []
    )

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

    # 5) Expense-lijst + delta per user (groen/rood tekstje)
    current_user_id = user["users_id"]
    expense_list = []

    for exp in expenses_rows:
        eid = exp["expense_id"]
        total_amount = float(exp.get("total_amount") or 0.0)
        shares = shares_by_expense.get(eid, [])

        my_share = 0.0
        for s in shares:
            if s["user_id"] == current_user_id:
                my_share = float(s["amount"] or 0.0)

        payer_is_me = exp["created_by_user_id"] == current_user_id

        # delta > 0 → jij leende uit; delta < 0 → jij leende
        if payer_is_me:
            delta = total_amount - my_share
        else:
            delta = -my_share

        stored_cat = (exp.get("category") or "").lower()
        if stored_cat not in CATEGORY_IDS:
            stored_cat = "activities"
        cat = stored_cat

        expense_list.append(
            {
                "expense_id": eid,
                "description": exp["description"],
                "total_amount": total_amount,
                "created_at": exp["created_at"],
                "payer_username": username_map.get(
                    exp["created_by_user_id"], _("Onbekend")
                ),
                "shares": shares,
                "delta": delta,
                "category": cat,
                "category_label": category_label(cat),
                "is_mine": (exp["created_by_user_id"] == current_user_id),
            }
        )

    # sorteren op datum (nieuwste eerst)
    expense_list.sort(key=lambda e: e["created_at"], reverse=True)

    return render_template(
        "ledger.html",
        user=user,
        group=group,
        expenses=expense_list,
        CATEGORY_LABELS=CATEGORY_LABELS,
    )

@main.route("/groups/<int:group_id>/expense/<int:expense_id>/delete", methods=["POST"])
@login_required
def expense_delete(group_id, expense_id):
    user = current_user()

    # expense ophalen
    res = (
        supabase.table("expenses")
        .select("*")
        .eq("group_id", group_id)
        .eq("expense_id", expense_id)
        .execute()
    )
    data = res.data or []
    if not data:
        abort(404)

    expense = data[0]

    # alleen maker mag verwijderen
    if expense["created_by_user_id"] != user["users_id"]:
        abort(403)

    # shares verwijderen
    supabase.table("expense_shares").delete().eq(
        "expense_id", expense_id
    ).execute()

    # expense 'soft delete' (is_active = False) of echt weg
    supabase.table("expenses").update(
        {"is_active": False}
    ).eq("expense_id", expense_id).execute()

    flash(_("Uitgave verwijderd."), "success")
    return redirect(url_for("main.ledger", group_id=group_id))
