"""Helper script to ensure fieldops role and fieldops_browser_qa database exist on local PostgreSQL."""
import psycopg

def main():
    conn = psycopg.connect("host=localhost port=5432 user=postgres password=postgres dbname=postgres", autocommit=True)
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM pg_roles WHERE rolname='fieldops'")
    if not cur.fetchone():
        cur.execute("CREATE ROLE fieldops WITH LOGIN PASSWORD 'fieldops' SUPERUSER CREATEDB")
        print("Created role fieldops")
    else:
        cur.execute("ALTER ROLE fieldops WITH LOGIN PASSWORD 'fieldops' SUPERUSER CREATEDB")
        print("Updated role fieldops")

    cur.execute("SELECT 1 FROM pg_database WHERE datname='fieldops_browser_qa'")
    if not cur.fetchone():
        cur.execute("CREATE DATABASE fieldops_browser_qa OWNER fieldops")
        print("Created database fieldops_browser_qa")
    else:
        print("Database fieldops_browser_qa already exists")
    conn.close()

    conn2 = psycopg.connect("host=localhost port=5432 user=fieldops password=fieldops dbname=fieldops_browser_qa")
    print("Connected successfully as fieldops to fieldops_browser_qa!")
    conn2.close()

if __name__ == "__main__":
    main()
