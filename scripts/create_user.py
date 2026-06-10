"""Admin utility — create or update a user directly in the database.

Usage (from the project root, with .env present):
    python scripts/create_user.py
"""
import getpass
import os
import sys

import bcrypt
import psycopg2
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    sys.exit("ERROR: DATABASE_URL not set. Add it to your .env file.")


def get_connection():
    return psycopg2.connect(DATABASE_URL)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def create_or_update_user(
    first_name: str,
    last_name: str,
    nickname: str,
    password: str,
    is_admin: bool,
):
    password_hash = hash_password(password)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO users (first_name, last_name, nickname, password_hash, is_admin)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (nickname) DO UPDATE SET
                    first_name    = EXCLUDED.first_name,
                    last_name     = EXCLUDED.last_name,
                    password_hash = EXCLUDED.password_hash,
                    is_admin      = EXCLUDED.is_admin;
            """, (first_name, last_name, nickname, password_hash, is_admin))
        conn.commit()


if __name__ == "__main__":
    print("=== Create / update user ===")
    first_name = input("First name: ").strip()
    last_name = input("Last name: ").strip()
    nickname = input("Nickname (used to log in): ").strip()
    password = getpass.getpass("Password: ")
    is_admin = input("Is admin? [y/N]: ").strip().lower() == "y"

    create_or_update_user(first_name, last_name, nickname, password, is_admin)
    print(f"User '{nickname}' created/updated successfully.")
