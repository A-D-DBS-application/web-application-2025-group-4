# app/__init__.py
from flask import Flask, session, request
from flask_babel import Babel

from .config import Config
from .routes import main

babel = Babel()


def select_locale():
    lang = session.get("lang")
    print(">>> select_locale – session['lang'] =", lang)

    if lang in ("nl", "en"):
        print(">>> select_locale – using session lang:", lang)
        return lang

    auto = request.accept_languages.best_match(["nl", "en"]) or "nl"
    print(">>> select_locale – using auto-detected lang:", auto)
    return auto



def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    # Default taal en welke talen je ondersteunt
    app.config.setdefault("BABEL_DEFAULT_LOCALE", "nl")
    app.config.setdefault("BABEL_SUPPORTED_LOCALES", ["nl", "en"])

    # HEEL BELANGRIJK:
    # Vertalingen staan in app/translations → voor Flask-Babel is dat gewoon "translations"
    app.config.setdefault("BABEL_TRANSLATION_DIRECTORIES", "translations")

    # Babel initialiseren met onze locale-selector
    babel.init_app(app, locale_selector=select_locale)

    # Blueprints
    app.register_blueprint(main)

    return app
