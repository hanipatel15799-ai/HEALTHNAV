"""
fix2.py — adds all missing columns to patient_files table
"""
import os, psycopg2
from dotenv import load_dotenv
load_dotenv()

conn = psycopg2.connect(
    host=os.getenv('DB_HOST'),
    dbname=os.getenv('DB_NAME'),
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASSWORD'),
    port='5432'
)
conn.autocommit = True
cur = conn.cursor()

print("=== Adding missing columns to patient_files ===")
columns = [
    "ALTER TABLE patient_files ADD COLUMN IF NOT EXISTS original_filename TEXT;",
    "ALTER TABLE patient_files ADD COLUMN IF NOT EXISTS stored_filename TEXT;",
    "ALTER TABLE patient_files ADD COLUMN IF NOT EXISTS file_path TEXT;",
    "ALTER TABLE patient_files ADD COLUMN IF NOT EXISTS content_type TEXT;",
    "ALTER TABLE patient_files ADD COLUMN IF NOT EXISTS notes TEXT;",
    "ALTER TABLE patient_files ADD COLUMN IF NOT EXISTS category TEXT DEFAULT 'other';",
    "ALTER TABLE patient_files ADD COLUMN IF NOT EXISTS uploaded_at TIMESTAMPTZ DEFAULT NOW();",
    "ALTER TABLE patient_files ADD COLUMN IF NOT EXISTS parse_status TEXT DEFAULT 'pending';",
    "ALTER TABLE patient_files ADD COLUMN IF NOT EXISTS parse_report_type TEXT;",
    "ALTER TABLE patient_files ADD COLUMN IF NOT EXISTS parse_confidence TEXT;",
    "ALTER TABLE patient_files ADD COLUMN IF NOT EXISTS parse_notes TEXT;",
    "ALTER TABLE patient_files ADD COLUMN IF NOT EXISTS labs_parsed INT DEFAULT 0;",
    "ALTER TABLE patient_files ADD COLUMN IF NOT EXISTS visits_parsed INT DEFAULT 0;",
    "ALTER TABLE patient_files ADD COLUMN IF NOT EXISTS meds_parsed INT DEFAULT 0;",
    "ALTER TABLE patient_files ADD COLUMN IF NOT EXISTS parsed_at TIMESTAMPTZ;",
]
for sql in columns:
    try:
        cur.execute(sql)
        col = sql.split('EXISTS ')[1].split(' ')[0]
        print(f"  OK: {col}")
    except Exception as e:
        print(f"  SKIP: {e}")

print()
print("=== Checking patient_files columns now ===")
cur.execute("""
    SELECT column_name, data_type 
    FROM information_schema.columns 
    WHERE table_name='patient_files' 
    ORDER BY ordinal_position;
""")
for row in cur.fetchall():
    print(f"  {row[0]:30} {row[1]}")

print()
print("=== Files in patient_files ===")
cur.execute("SELECT patient_id, original_filename, parse_status FROM patient_files ORDER BY id DESC LIMIT 10;")
rows = cur.fetchall()
if rows:
    for r in rows:
        print(f"  patient={r[0]}  file={r[1]}  status={r[2]}")
else:
    print("  No files uploaded yet")

print()
print("=== Users and their data ===")
cur.execute("SELECT username, patient_id FROM patient_users ORDER BY created_at DESC;")
users = cur.fetchall()
for u in users:
    username, pid = u
    cur.execute("SELECT COUNT(*) FROM patient_labs WHERE patient_id=%s;", (pid,))
    labs = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM patient_files WHERE patient_id=%s;", (pid,))
    files = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM patient_medications WHERE patient_id=%s;", (pid,))
    meds = cur.fetchone()[0]
    print(f"  {username:15} patient_id={pid}  labs={labs}  files={files}  meds={meds}")

conn.close()
print()
print("=== All done — restart your server now ===")
