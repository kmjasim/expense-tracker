# reset_db.py
import os
from app import create_app
from app.extensions import db

def reset_database():
    """Completely drop and recreate all tables and sequences (fresh state)."""
    app = create_app()
    with app.app_context():
        print("⚠️  Dropping ALL tables and sequences...")
        db.drop_all()
        db.session.commit()

        print("✅ Creating fresh schema...")
        db.create_all()
        db.session.commit()

        print("🎉 Database reset complete — now it's brand new!")

if __name__ == "__main__":
    reset_database()
