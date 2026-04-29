# Place at: db/db.py
"""
db/db.py — single psycopg2 connection factory.
Import get_connection() from here everywhere.
"""
from __future__ import annotations
import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

def save_file_record(patient_id: str, file_name: str, s3_path: str):
    conn = psycopg2.connect(
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT"),
    )

    cur = conn.cursor()

    cur.execute(
        """
        INSERT INTO patient_files (patient_id, file_name, s3_path)
        VALUES (%s, %s, %s)
        RETURNING id, upload_time;
        """,
        (patient_id, file_name, s3_path),
    )

    result = cur.fetchone()
    conn.commit()

    cur.close()
    conn.close()

    return {
        "file_id": result[0],
        "upload_time": result[1],
    }