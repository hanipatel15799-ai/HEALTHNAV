import psycopg2

conn = psycopg2.connect(
    host="database-1.cybyas8mse1u.us-east-1.rds.amazonaws.com",
    database="postgres",
    user="postgres",
    password="Hanivishv9050",
    port=5432
)

print("Connected!")

cur = conn.cursor()
cur.execute("SELECT version();")
print(cur.fetchone())

cur.close()
conn.close()