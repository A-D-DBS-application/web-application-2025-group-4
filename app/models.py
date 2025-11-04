from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

# eventueel voorbeeldmodel
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    phone = db.Column(db.String(20))
    iban = db.Column(db.String(34))
