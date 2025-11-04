# app/__init__.py
from flask import Flask
from .config import Config
from .model import db
from .routes import main

def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)
    with app.app_context():
        db.create_all()  # ok voor SQLite dev; weghalen als je Supabase-only gebruikt

    app.register_blueprint(main)
    return app

