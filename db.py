
import mysql.connector

def get_db_connection():
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
        return connection

    except mysql.connector.Error as error:
        print("DATABASE CONNECTION ERROR:", error)
        raise
