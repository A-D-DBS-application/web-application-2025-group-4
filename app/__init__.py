# app/__init__.py
from flask import Flask, session, request
from flask_babel import Babel

from .config import Config
from .routes import main

babel = Babel()

def get_locale():
    # 1) user keuze via session
    lang = session.get("lang")
    if lang in ("nl", "en"):
        return lang

    # 2) browser fallback
    return request.accept_languages.best_match(["nl", "en"]) or "nl"


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    app.register_blueprint(main)

    # ✅ Babel init met locale selector (Flask-Babel >= 3)
    babel.init_app(app, locale_selector=get_locale)

    return app
