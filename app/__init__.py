# app/__init__.py
from flask import Flask, session, request
from flask_babel import Babel

from .config import Config
from .routes import main

babel = Babel()


def get_locale():
    """
    Bepaal de actieve taal.

    1. Eerst: expliciete keuze in session['lang'] (via NL | EN links)
    2. Anders: best-match met de browser
    3. Fallback: 'nl'
    """
    lang = session.get("lang")
    if lang in ("nl", "en"):
        return lang

    return request.accept_languages.best_match(["nl", "en"]) or "nl"


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    # ✅ Default taal en ondersteunde talen
    app.config["BABEL_DEFAULT_LOCALE"] = "nl"
    app.config["BABEL_SUPPORTED_LOCALES"] = ["nl", "en"]

    # ❌ NIET "app/translations" gebruiken!
    # Laat de default staan: "translations" onder app.root_path
    # app.config["BABEL_TRANSLATION_DIRECTORIES"] = "translations"

    # ✅ Nieuwe API van Flask-Babel (3.x/4.x)
    babel.init_app(app, locale_selector=get_locale)

    # Blueprints
    app.register_blueprint(main)

    return app
