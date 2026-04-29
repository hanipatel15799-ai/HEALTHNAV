# This file marks the utils/ directory as a Python package
import psycopg2

conn = psycopg2.connect(
    host="database-1.cybyas8mse1u.us-east-1.rds.amazonaws.com",
    database="postgres",
    user="postgres",
    password="Hanivishv9050",
    port=5432
)

cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS patient_files (
    id SERIAL PRIMARY KEY,
    patient_id TEXT,
    file_name TEXT,
    s3_path TEXT,
    upload_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
""")

conn.commit()
cur.close()
conn.close()

print("Table created!")