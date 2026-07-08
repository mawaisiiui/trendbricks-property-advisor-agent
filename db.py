"""
db.py

Thin MySQL connection helper for the TrendBricks Property Advisor Agent.

Design note: this is intentionally a plain connection wrapper, not an ORM.
The agent's tools need full control over the SQL they run (dynamic filters,
ranges, LIKE clauses on a stringified address list), so a lightweight
connector is a better fit here than an ORM layer.
"""

import os
from contextlib import contextmanager

import mysql.connector
from mysql.connector import Error as MySQLError
from dotenv import load_dotenv

load_dotenv()

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "port": int(os.getenv("DB_PORT", "3306")),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "database": os.getenv("DB_NAME"),
}

TABLE_NAME = os.getenv("DB_TABLE", "properties")


@contextmanager
def get_connection():
    """
    Yields a live MySQL connection, closing it afterwards even if the
    caller raises. Raises a clear error early if credentials are missing,
    instead of failing deep inside a query with a confusing driver error.
    """
    missing = [k for k in ("user", "password", "database") if not DB_CONFIG.get(k)]
    if missing:
        raise RuntimeError(
            f"Missing required DB config values: {missing}. "
            f"Did you copy .env.example to .env and fill it in?"
        )

    conn = None
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        yield conn
    except MySQLError as e:
        raise RuntimeError(f"MySQL connection failed: {e}") from e
    finally:
        if conn is not None and conn.is_connected():
            conn.close()


def run_query(sql: str, params: tuple = ()) -> list[dict]:
    """
    Runs a parameterized SELECT and returns rows as a list of dicts.
    Always uses parameterized queries (never raw string formatting of
    user input) to avoid SQL injection, since some filter values
    (location text, property type) will eventually come from an LLM-
    extracted, user-controlled query.
    """
    with get_connection() as conn:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        cursor.close()
        return rows
