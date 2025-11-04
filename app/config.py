class Config:
    SECRET_KEY = "your_secret_key"
    SQLALCHEMY_DATABASE_URI = "postgresql+psycopg2://postgres:BaDeDrMaMe2005%21@db.nvuiebutbcxfcdaitgbs.supabase.co:5432/postgres"
    SQLALCHEMY_TRACK_MODIFICATIONS = False 

app.config['SQLALCHEMY_DATABASE_URI'] = 'postgresql+psycopg2://postgres:BaDeDrMaMe2005%21@db.nvuiebutbcxfcdaitgbs.supabase.co:5432/postgres'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
