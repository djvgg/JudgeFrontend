import os
import sys

# Add project root to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.database import SessionLocal, MatchModel

def view_data():
    print("--- Connexion a la base de donnees PostgreSQL ---")
    
    try:
        with SessionLocal() as session:
            # Compter le nombre total de combats
            total_matches = session.query(MatchModel).count()
            print(f"[OK] Succes : La base de donnees contient {total_matches} combats.\n")
            
            if total_matches > 0:
                print("--- Voici un apercu des 3 premiers combats ---")
                # Récupérer les 3 premiers combats triés par numéro
                first_matches = session.query(MatchModel).order_by(MatchModel.fightNr).limit(3).all()
                
                for m in first_matches:
                    print(f"Combat n={m.fightNr} (Table {m.tableId} - Round {m.round})")
                    print(f"  Joueur 1 : {m.p1.get('firstName')} {m.p1.get('lastName')} ({m.p1.get('club')}) - Score: {m.p1.get('score', {}).get('points', 0)}")
                    print(f"  Joueur 2 : {m.p2.get('firstName')} {m.p2.get('lastName')} ({m.p2.get('club')}) - Score: {m.p2.get('score', {}).get('points', 0)}")
                    print(f"  Statut   : {m.status}")
                    print("-" * 40)
                    
    except Exception as e:
        print(f"[ERREUR] Erreur lors de la connexion : {e}")

if __name__ == "__main__":
    view_data()
