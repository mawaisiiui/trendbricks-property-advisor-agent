import os

import mysql.connector
from dotenv import load_dotenv
from mysql.connector import Error as MySQLError
from contextlib import contextmanager

load_dotenv()


DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "user": os.getenv("DB_USER"),
    "password": os.getenv("DB_PASSWORD"),
    "database": os.getenv("DB_NAME"),
    "port": int(os.getenv("DB_PORT", "3306"))
}


TABLE_NAME = os.getenv("TABLE_NAME", "properties")

@contextmanager
def get_connection():
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
    with get_connection() as conn:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        cursor.close()
        return rows