import psycopg2, os
from dotenv import load_dotenv

load_dotenv()

conn_string = (
    f"host={os.getenv('SUPABASE_HOST')} "
    f"dbname={os.getenv('SUPABASE_DB')} "
    f"user={os.getenv('SUPABASE_USER')} "
    f"password={os.getenv('SUPABASE_PASSWORD')} "
    f"sslmode=require"
)

try:
    conn = psycopg2.connect(conn_string)
    print("✅ Connected to Supabase!")
    conn.close()
except Exception as e:
    print("❌ Error:", e)
