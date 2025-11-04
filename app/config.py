# app/config.py
import os

class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "devkey123")
    # Voor nu: SQLite (geen server nodig)
    SQLALCHEMY_DATABASE_URI = "sqlite:///dev.db"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
