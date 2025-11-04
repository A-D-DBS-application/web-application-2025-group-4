from dotenv import load_dotenv
from flask import Blueprint, render_template, request, redirect, url_for, flash, Flask
from supabase import create_client, Client
import os

load_dotenv()  # Laad .env

# Supabase client
supabase: Client = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_KEY")
)

# ------------------- REGISTER ROUTE -------------------
main = Blueprint("main", __name__)

@main.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name")
        email = request.form.get("email")
        phone = request.form.get("phone")
        iban = request.form.get("iban")

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


# ------------------- LOGIN ROUTE -------------------
@main.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email")

        response = supabase.table("users").select("*").eq("email", email).execute()
        if response.data:
            flash(f"Welkom terug, {response.data[0]['name']}!", "success")
            return redirect(url_for("main.dashboard"))  # Of je dashboard route
        else:
            flash("Geen account gevonden met dat e-mailadres.", "warning")

    return render_template("login.html")


# ------------------- DASHBOARD ROUTE -------------------
@main.route("/dashboard")
def dashboard():
    # Voor nu gewoon een simpele pagina
    return """
    <h2>Dashboard</h2>
    <p>Welkom op je dashboard!</p>
    <p><a href='/login'>Uitloggen</a></p>
    """

# ------------------- MAIN APP -------------------
app = Flask(__name__)

# Registreer de blueprint na de app
app.register_blueprint(main)

# ------------------- ROOT ROUTE -------------------
@app.route('/')
def home():
    return "Hello, world!"  # Of je template, bijvoorbeeld render_template('index.html')

if __name__ == '__main__':
    app.run(debug=True)