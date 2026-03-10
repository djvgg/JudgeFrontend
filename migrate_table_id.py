from sqlalchemy import text

from src.database import engine


def add_table_id_column():
    with engine.connect() as conn:
        # Check if column exists first
        try:
            conn.execute(text("SELECT table_id FROM fights LIMIT 1"))
            print("table_id already exists")
        except Exception:
            # Column doesn't exist, ignore the error and add it
            print("table_id does not exist, adding it...")
            with conn.begin():
                conn.execute(text("ALTER TABLE fights ADD COLUMN table_id VARCHAR;"))
                print("Column added successfully!")

if __name__ == "__main__":
    add_table_id_column()
