# app/supabase_client.py
import os
from dotenv import load_dotenv
from supabase import create_client, Client

# Zorg dat de .env-variabelen geladen worden
load_dotenv()
 
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise RuntimeError(
        "❌ Missing SUPABASE_URL or SUPABASE_KEY in .env. "
        "Check that your .env file contains these keys."
    )

# Maak de Supabase client aan
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
