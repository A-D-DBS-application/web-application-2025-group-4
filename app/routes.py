from flask import Blueprint, render_template, request, redirect, url_for, flash
from supabase import create_client, Client
import os

main = Blueprint("main", __name__)

# 🔐 Supabase-instellingen
url = os.getenv("SUPABASE_URL")
key = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(url, key)

# -------------------
# REGISTER ROUTE
# -------------------
@main.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name")
        email = request.form.get("email")
        phone = request.form.get("phone")
        iban = request.form.get("iban")

        # Voeg gebruiker toe aan Supabase
        response = supabase.table("users").insert({
            "name": name,
            "email": email,
            "phone": phone,
            "iban": iban
        }).execute()

        if response.data:
            flash("Account aangemaakt! Je kan nu inloggen.", "success")
            return redirect(url_for("main.login"))
        else:
            flash("Er ging iets mis bij het registreren.", "danger")

    return render_template("register.html")


# -------------------
# LOGIN ROUTE
# -------------------
@main.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email")

        # Zoek gebruiker in Supabase
        response = supabase.table("users").select("*").eq("email", email).execute()
        if response.data:
            flash(f"Welkom terug, {response.data[0]['name']}!", "success")
            # hier kun je later session["user_id"] = response.data[0]["id"] zetten
            return redirect(url_for("main.dashboard"))  # of waar je naartoe wilt
        else:
            flash("Geen account gevonden met dat e-mailadres.", "warning")

    return render_template("login.html")
