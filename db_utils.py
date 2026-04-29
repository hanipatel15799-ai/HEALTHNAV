import psycopg2

def get_connection():
    return psycopg2.connect(
        host="database-1.cybyas8mse1u.us-east-1.rds.amazonaws.com",
        database="postgres",
        user="postgres",
        password="Hanivishv9050",
        port=5432
    )

def save_to_db(patient_id, file_name, s3_path):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO patient_files (patient_id, file_name, s3_path)
        VALUES (%s, %s, %s)
    """, (patient_id, file_name, s3_path))

    conn.commit()
    cur.close()
    conn.close()