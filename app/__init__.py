# app/__init__.py
from flask import Flask
from .config import Config
from .routes import main
from .model import db  # laat dit staan als je SQLAlchemy nog elders gebruikt
from dotenv import load_dotenv

# Laad de .env variabelen (zodat Flask en Supabase ze zien)
load_dotenv()

def create_app():
    """Flask app factory."""
    app = Flask(__name__)
    app.config.from_object(Config)

    # Initialize SQLAlchemy (maar we maken geen DB-tabellen meer aan)
    db.init_app(app)

    # ⚠️ Belangrijk: db.create_all() weghalen of uitcommentariëren!
    # with app.app_context():
    #     db.create_all()

    # Registreer je blueprint (alle routes)
    app.register_blueprint(main)

    return app