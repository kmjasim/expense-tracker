# wsgi.py
from dotenv import load_dotenv
load_dotenv()  # loads .env before config

from app import create_app
app = create_app()
