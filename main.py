import contextlib
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from src.database import MatchModel, SessionLocal, init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize DB (create tables if needed)
    init_db()
    yield

app = FastAPI(title="Judo Real-Time API", lifespan=lifespan)

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- WebSocket Manager ---

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            with contextlib.suppress(Exception):
                await connection.send_json(message)

manager = ConnectionManager()

# --- API & WebSockets ---

@app.get("/api/matches")
def get_matches():
    from src.database import FightModel, ParticipantModel
    with SessionLocal() as session:
        # Fetch all fights sorted by fight_number
        fights = session.query(FightModel).order_by(FightModel.fight_number).all()

        # Build lookup: (bracket_id, phase, round, pos_in_round) → fight.id
        fight_lookup = {}
        for f in fights:
            key = (f.bracket_id, f.bracket_phase, f.round, f.pos_in_round)
            fight_lookup[key] = f.id

        # Pre-fetch all participants for efficiency
        participant_ids = set()
        for f in fights:
            if f.participant1_id:
                participant_ids.add(f.participant1_id)
            if f.participant2_id:
                participant_ids.add(f.participant2_id)
        participants = {p.id: p for p in session.query(ParticipantModel).filter(ParticipantModel.id.in_(participant_ids)).all()} if participant_ids else {}

        match_list = []
        for f in fights:
            p1_obj = participants.get(f.participant1_id)
            p2_obj = participants.get(f.participant2_id)

            p1_data = {
                "id": str(p1_obj.id) if p1_obj else "WAIT",
                "firstName": p1_obj.first_name if p1_obj else "",
                "lastName": p1_obj.last_name if p1_obj else "TBD",
                "club": p1_obj.club if p1_obj else "",
                "score": {"points": f.score1 if f.score1 is not None else 0}
            }

            p2_data = {
                "id": str(p2_obj.id) if p2_obj else "WAIT",
                "firstName": p2_obj.first_name if p2_obj else "",
                "lastName": p2_obj.last_name if p2_obj else "TBD",
                "club": p2_obj.club if p2_obj else "",
                "score": {"points": f.score2 if f.score2 is not None else 0}
            }

            # Compute next match in bracket tree dynamically
            next_round = (f.round or 0) + 1
            next_pos = (f.pos_in_round or 0) // 2
            next_key = (f.bracket_id, f.bracket_phase, next_round, next_pos)
            next_match_id = fight_lookup.get(next_key)
            next_match_pos = "p1" if (f.pos_in_round or 0) % 2 == 0 else "p2"

            # Distribute fights across tables via round-robin
            table_id = str((f.fight_number or f.id) % 4 + 1)

            match_dict = {
                "matchId": f.id,
                "tableId": table_id,
                "fightNr": f.fight_number or f.id,
                "category": f"Bracket {f.bracket_id}" if f.bracket_id else "Unknown Category",
                "round": (f.round or 0) + 1,
                "posInRound": f.pos_in_round or 0,
                "p1": p1_data,
                "p2": p2_data,
                "status": "finished" if f.status == "completed" else (f.status or "upcoming"),
                "order": f.fight_number or f.id,
                "restTimeMin": 0,
                "phase": f.bracket_phase,
                "nextMatchId": next_match_id,
                "nextMatchPos": next_match_pos if next_match_id else None
            }
            match_list.append(match_dict)

        return {"tournamentName": "Automated Tournament", "matches": match_list}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_json()

            with SessionLocal() as session:
                from src.database import FightModel, ParticipantModel
                if data["type"] == "SCORE_UPDATE":
                    fight = session.query(FightModel).filter(FightModel.id == data["matchId"]).first()
                    if fight:
                        # Direct assignment to native columns
                        if data["playerNum"] == 1:
                            fight.score1 = data["value"]
                        else:
                            fight.score2 = data["value"]

                        session.commit()
                        session.refresh(fight)

                        # Rebuild the dictionary identically to get_matches to broadcast the sync
                        p1_obj = session.query(ParticipantModel).filter(ParticipantModel.id == fight.participant1_id).first() if fight.participant1_id else None
                        p2_obj = session.query(ParticipantModel).filter(ParticipantModel.id == fight.participant2_id).first() if fight.participant2_id else None

                        # Compute next match in bracket tree dynamically
                        next_round = (fight.round or 0) + 1
                        next_pos = (fight.pos_in_round or 0) // 2
                        # Find next fight in DB
                        next_fight = session.query(FightModel).filter(
                            FightModel.bracket_id == fight.bracket_id,
                            FightModel.bracket_phase == fight.bracket_phase,
                            FightModel.round == next_round,
                            FightModel.pos_in_round == next_pos
                        ).first()

                        next_match_id = next_fight.id if next_fight else None
                        next_match_pos = "p1" if (fight.pos_in_round or 0) % 2 == 0 else "p2"
                        table_id = str((fight.fight_number or fight.id) % 4 + 1)

                        match_dict = {
                            "matchId": fight.id,
                            "tableId": table_id,
                            "fightNr": fight.fight_number or fight.id,
                            "category": f"Bracket {fight.bracket_id}" if fight.bracket_id else "Unknown Category",
                            "round": (fight.round or 0) + 1,
                            "posInRound": fight.pos_in_round or 0,
                            "p1": {
                                "id": str(p1_obj.id) if p1_obj else "WAIT",
                                "firstName": p1_obj.first_name if p1_obj else "",
                                "lastName": p1_obj.last_name if p1_obj else "TBD",
                                "club": p1_obj.club if p1_obj else "",
                                "score": {"points": fight.score1 if fight.score1 is not None else 0}
                            },
                            "p2": {
                                "id": str(p2_obj.id) if p2_obj else "WAIT",
                                "firstName": p2_obj.first_name if p2_obj else "",
                                "lastName": p2_obj.last_name if p2_obj else "TBD",
                                "club": p2_obj.club if p2_obj else "",
                                "score": {"points": fight.score2 if fight.score2 is not None else 0}
                            },
                            "status": "finished" if fight.status == "completed" else (fight.status or "upcoming"),
                            "order": fight.fight_number or fight.id,
                            "restTimeMin": 0,
                            "phase": fight.bracket_phase,
                            "nextMatchId": next_match_id,
                            "nextMatchPos": next_match_pos if next_match_id else None
                        }

                        await manager.broadcast({"type": "SCORE_SYNC", "matchId": data["matchId"], "match": match_dict})

                elif data["type"] == "STATUS_UPDATE":
                    fight = session.query(FightModel).filter(FightModel.id == data["matchId"]).first()
                    if fight:
                        fight.status = data["status"]

                        # If finished, determine the winner.
                        if data["status"] == "finished":
                            s1 = fight.score1 or 0
                            s2 = fight.score2 or 0
                            if s1 > s2:
                                fight.winner_id = fight.participant1_id
                            elif s2 > s1:
                                fight.winner_id = fight.participant2_id

                        session.commit()
                        session.refresh(fight)

                        # Rebuild the dictionary to broadcast
                        p1_obj = session.query(ParticipantModel).filter(ParticipantModel.id == fight.participant1_id).first() if fight.participant1_id else None
                        p2_obj = session.query(ParticipantModel).filter(ParticipantModel.id == fight.participant2_id).first() if fight.participant2_id else None

                        # Compute next match in bracket tree dynamically
                        next_round = (fight.round or 0) + 1
                        next_pos = (fight.pos_in_round or 0) // 2

                        # Find next fight in DB
                        next_fight = session.query(FightModel).filter(
                            FightModel.bracket_id == fight.bracket_id,
                            FightModel.bracket_phase == fight.bracket_phase,
                            FightModel.round == next_round,
                            FightModel.pos_in_round == next_pos
                        ).first()

                        next_match_id = next_fight.id if next_fight else None
                        next_match_pos = "p1" if (fight.pos_in_round or 0) % 2 == 0 else "p2"
                        table_id = str((fight.fight_number or fight.id) % 4 + 1)

                        match_dict = {
                            "matchId": fight.id,
                            "tableId": table_id,
                            "fightNr": fight.fight_number or fight.id,
                            "category": f"Bracket {fight.bracket_id}" if fight.bracket_id else "Unknown Category",
                            "round": (fight.round or 0) + 1,
                            "posInRound": fight.pos_in_round or 0,
                            "p1": {
                                "id": str(p1_obj.id) if p1_obj else "WAIT",
                                "firstName": p1_obj.first_name if p1_obj else "",
                                "lastName": p1_obj.last_name if p1_obj else "TBD",
                                "club": p1_obj.club if p1_obj else "",
                                "score": {"points": fight.score1 if fight.score1 is not None else 0}
                            },
                            "p2": {
                                "id": str(p2_obj.id) if p2_obj else "WAIT",
                                "firstName": p2_obj.first_name if p2_obj else "",
                                "lastName": p2_obj.last_name if p2_obj else "TBD",
                                "club": p2_obj.club if p2_obj else "",
                                "score": {"points": fight.score2 if fight.score2 is not None else 0}
                            },
                            "status": "finished" if fight.status == "completed" else (fight.status or "upcoming"),
                            "order": fight.fight_number or fight.id,
                            "restTimeMin": 0,
                            "phase": fight.bracket_phase,
                            "nextMatchId": next_match_id,
                            "nextMatchPos": next_match_pos if next_match_id else None
                        }
                        await manager.broadcast({"type": "SCORE_SYNC", "matchId": data["matchId"], "match": match_dict})

                elif data["type"] == "REORDER":
                    for m_id, order in data["orders"].items():
                        session.query(FightModel).filter(FightModel.id == int(m_id)).update({"fight_number": order})
                    session.commit()
                    await manager.broadcast({"type": "REFRESH_LIST"})

                elif data["type"] == "SIGNAL":
                    await manager.broadcast(data)

    except WebSocketDisconnect:
        manager.disconnect(websocket)

@app.post("/api/import-brackets")
def import_brackets(groups: list[dict]):
    with SessionLocal() as session:
        matches_imported = 0
        for group in groups:
            for match_data in group.get("matches", []):
                match = MatchModel(**match_data)
                session.add(match)
                matches_imported += 1
        session.commit()
        return {"status": "success", "matches_imported": matches_imported}
