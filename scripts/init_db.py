"""Check and create the places_db database if needed."""
import psycopg2

try:
    conn = psycopg2.connect("postgresql://postgres:postgres@localhost:5432/postgres")
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM pg_database WHERE datname='places_db'")
    exists = cur.fetchone()
    if not exists:
        cur.execute("CREATE DATABASE places_db")
        print("Database 'places_db' created successfully.")
    else:
        print("Database 'places_db' already exists.")
    cur.close()
    conn.close()
except Exception as e:
    print(f"Error: {e}")
    print("Trying with different credentials...")
    try:
        conn = psycopg2.connect("postgresql://postgres:@localhost:5432/postgres")
        conn.autocommit = True
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM pg_database WHERE datname='places_db'")
        exists = cur.fetchone()
        if not exists:
            cur.execute("CREATE DATABASE places_db")
            print("Database 'places_db' created (no password).")
        else:
            print("Database 'places_db' already exists (no password).")
        cur.close()
        conn.close()
    except Exception as e2:
        print(f"Error with no password: {e2}")
