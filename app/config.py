import os

class Config:
    SECRET_KEY = os.getenv("SECRET_KEY", "devkey123")

    # 🔧 Haal databasegegevens uit .env
    SUPABASE_USER = os.getenv("SUPABASE_USER", "postgres")
    SUPABASE_PASSWORD = os.getenv("SUPABASE_PASSWORD", "BaDeDrMaMe2005!")  # ← let op: hier zonder %21
    SUPABASE_HOST = "db.nvuiebutbcxfcdaitgbs.supabase.co"
    SUPABASE_DB = "postgres"

    SQLALCHEMY_DATABASE_URI = (
        f"postgresql+psycopg2://{SUPABASE_USER}:{SUPABASE_PASSWORD}@"
        f"{SUPABASE_HOST}:5432/{SUPABASE_DB}?sslmode=require"
    )

    SQLALCHEMY_TRACK_MODIFICATIONS = False

