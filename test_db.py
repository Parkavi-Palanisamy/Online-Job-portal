
import mysql.connector
import traceback

print("Starting database test...", flush=True)

try:
    connection = mysql.connector.connect(
        host="127.0.0.1",
        port=3306,
        user="root",
        password="Kavi@2005",
        database="online_job_portal",
        connection_timeout=5,
        use_pure=True
    )

    print("Database connected successfully!", flush=True)

    cursor = connection.cursor()
    cursor.execute("SHOW TABLES")
    print("Tables:", cursor.fetchall(), flush=True)

    cursor.close()
    connection.close()

except Exception:
    traceback.print_exc()

input("Press Enter to exit...")
