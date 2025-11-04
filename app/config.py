from flask import Flask

app = Flask(__name__)

class Config:
    SECRET_KEY = "your_secret_key"  # Nodig voor session/flash messages

# Config toepassen
app.config.from_object(Config)
