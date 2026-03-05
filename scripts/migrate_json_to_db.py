import json
import os
import sys

# Add project root to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


from src.database import MatchModel, SessionLocal, init_db

JSON_FILE = "mock_fights.json"

def migrate():
    print("--- Starting Synchronous Migration: JSON to PostgreSQL ---")

    # Initialize tables
    init_db()

    try:
        with open(JSON_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"[ERROR] {JSON_FILE} not found. Nothing to migrate.")
        return

    matches = data.get("matches", [])
    print(f"Found {len(matches)} matches in JSON.")

    with SessionLocal() as session:
        # Clear existing matches
        print("Clearing existing matches in DB...")
        session.query(MatchModel).delete()

        print("Migrating matches...")
        for m_data in matches:
            # Flatten score to just use 'points' to match frontend 5, 7, 10 logic
            for player_key in ['p1', 'p2']:
                if player_key in m_data and 'score' in m_data[player_key]:
                    score_dict = m_data[player_key]['score']
                    # Calculate a fallback points value if it doesn't explicitly exist but ippon/wazaari do (for older data)
                    points = score_dict.get('points', 0)
                    if points == 0 and 'ippon' in score_dict:
                        points = score_dict.get('ippon', 0) * 10 + score_dict.get('wazaari', 0) * 1
                    m_data[player_key]['score'] = {'points': points}

            match = MatchModel(**m_data)
            session.add(match)

        session.commit()
        print(f"[OK] Successfully migrated {len(matches)} matches to PostgreSQL (Sync Mode).")

if __name__ == "__main__":
    migrate()
