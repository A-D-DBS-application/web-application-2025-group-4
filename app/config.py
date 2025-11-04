from flask import Flask
from flask_sqlalchemy import SQLAlchemy

app = Flask(__name__)

class Config:
    SECRET_KEY = "your_secret_key"
    SQLALCHEMY_DATABASE_URI = "postgresql+psycopg2://postgres:BaDeDrMaMe2005%21@db.nvuiebutbcxfcdaitgbs.supabase.co:5432/postgres"
    SQLALCHEMY_TRACK_MODIFICATIONS = False

# Config toepassen
app.config.from_object(Config)

# Database initialiseren
db = SQLAlchemy(app)
with app.app_context():
    try:
        # Maak een connectie
        with db.engine.connect() as connection:
            result = connection.execute("SELECT 1")
            print("Database connectie OK!", result.scalar())
    except Exception as e:
        print("Fout bij connectie:", e)

