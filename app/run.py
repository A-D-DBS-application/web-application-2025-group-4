#als je deze file runt, begint je app te werken
#typ in terminal: $env:FLASK_APP = "run.py"
#typ daarna $env:FLASK_ENV = "development" 
#flask run
from app import create_app

app = create_app()

if __name__ == '__main__':
    app.run(debug=True)