"""Initialize the database: create all tables.

Usage:
    venv\Scripts\python.exe scripts\init_db.py
"""
from app.core.database import init_db

if __name__ == "__main__":
    init_db()
    print("Database tables created successfully.")
