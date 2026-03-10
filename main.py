import contextlib
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from src.bracket_manager import BracketManager
from src.database import (
    BracketModel,
    FightModel,
    GroupModel,
    GroupParticipantModel,
    MatchModel,
    ParticipantModel,
    SessionLocal,
    init_db,
)


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
async def get_match_dict(fight_id: int, session):
    fight = session.query(FightModel).filter(FightModel.id == fight_id).first()
    if not fight:
        return None

    p1_obj = session.query(ParticipantModel).filter(ParticipantModel.id == fight.participant1_id).first() if fight.participant1_id else None
    p2_obj = session.query(ParticipantModel).filter(ParticipantModel.id == fight.participant2_id).first() if fight.participant2_id else None

    # Compute next match in bracket tree dynamically (legacy/ui fallback)
    next_round = (fight.round or 0) + 1
    next_pos = (fight.pos_in_round or 0) // 2

    # We don't have the lookup here easily without extra queries, so we do a quick one
    next_fight = session.query(FightModel).filter(
        FightModel.bracket_id == fight.bracket_id,
        FightModel.bracket_phase == fight.bracket_phase,
        FightModel.round == next_round,
        FightModel.pos_in_round == next_pos
    ).first()
    next_match_id = next_fight.id if next_fight else None
    next_match_pos = "p1" if (fight.pos_in_round or 0) % 2 == 0 else "p2"

    # Category name
    b_info = session.query(BracketModel, GroupModel).join(GroupModel, BracketModel.group_id == GroupModel.id).filter(BracketModel.id == fight.bracket_id).first()
    category = f"{b_info[1].age_group} {b_info[1].weight_class}" if b_info else f"Bracket {fight.bracket_id}"

    table_id = str(fight.table_id) if getattr(fight, 'table_id', None) else str((fight.fight_number or fight.id) % 4 + 1)

    return {
        "matchId": fight.id,
        "tableId": table_id,
        "fightNr": fight.fight_number or fight.id,
        "category": category,
        "bracketId": str(fight.bracket_id) if fight.bracket_id else "",
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
        "poolIndex": fight.pool_index,
        "winnerId": str(fight.winner_id) if fight.winner_id else None,
        "nextMatchId": next_match_id,
        "nextMatchPos": next_match_pos if next_match_id else None
    }

# --- API & WebSockets ---

@app.get("/api/matches")
def get_matches():
    with SessionLocal() as session:
        # Fetch all fights sorted by fight_number
        fights = session.query(FightModel).order_by(FightModel.fight_number).all()

        # Build lookup: (bracket_id, phase, round, pos_in_round) → fight.id
        fight_lookup = {(f.bracket_id, f.bracket_phase, f.round, f.pos_in_round): f.id for f in fights}

        # Cache bracket/group names
        category_names = {}
        for b_id in {f.bracket_id for f in fights if f.bracket_id}:
            b_info = session.query(BracketModel, GroupModel).join(GroupModel, BracketModel.group_id == GroupModel.id).filter(BracketModel.id == b_id).first()
            if b_info:
                bracket, group = b_info
                category_names[b_id] = f"{group.age_group} {group.weight_class}"

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

            # Distribute fights across tables via round-robin or use assigned table
            table_id = str(f.table_id) if getattr(f, 'table_id', None) else str((f.fight_number or f.id) % 4 + 1)

            match_dict = {
                "matchId": f.id,
                "tableId": table_id,
                "fightNr": f.fight_number or f.id,
                "category": category_names.get(f.bracket_id, f"Bracket {f.bracket_id}") if f.bracket_id else "Unknown Category",
                "bracketId": str(f.bracket_id) if f.bracket_id else "",
                "round": (f.round or 0) + 1,
                "posInRound": f.pos_in_round or 0,
                "p1": p1_data,
                "p2": p2_data,
                "status": "finished" if f.status == "completed" else (f.status or "upcoming"),
                "order": f.fight_number or f.id,
                "restTimeMin": 0,
                "phase": f.bracket_phase,
                "poolIndex": f.pool_index,
                "winnerId": str(f.winner_id) if f.winner_id else None,
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
                        # Validation: Prevent scoring if participants are TBD
                        if not fight.participant1_id or not fight.participant2_id:
                            # We don't raise an error to avoid breaking the socket, just ignore
                            return

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
                        table_id = str(fight.table_id) if getattr(fight, 'table_id', None) else str((fight.fight_number or fight.id) % 4 + 1)

                        match_dict = {
                            "matchId": fight.id,
                            "tableId": table_id,
                            "fightNr": fight.fight_number or fight.id,
                            "category": f"Bracket {fight.bracket_id}" if fight.bracket_id else "Unknown Category",
                            "bracketId": str(fight.bracket_id) if fight.bracket_id else "",
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
                            "poolIndex": fight.pool_index,
                            "nextMatchId": next_match_id,
                            "nextMatchPos": next_match_pos if next_match_id else None
                        }

                        await manager.broadcast({"type": "SCORE_SYNC", "matchId": data["matchId"], "match": match_dict})

                elif data["type"] == "REASSIGN_BRACKET":
                    b_id_str = data.get("bracketId")
                    new_table = data.get("newTableId")

                    if b_id_str and new_table is not None:
                        # Extract the actual integer bracket_id. If category was used, it comes in as "Bracket X"
                        try:
                            # Parse out integer if formatted as "Bracket 1"
                            if "Bracket" in b_id_str:
                                bracket_id_int = int(b_id_str.replace("Bracket ", ""))
                            else:
                                bracket_id_int = int(b_id_str)

                            # Find all fights for this bracket
                            fights = session.query(FightModel).filter(FightModel.bracket_id == bracket_id_int).all()
                            for f in fights:
                                if f.status not in ["completed", "bye"]:
                                    f.table_id = str(new_table)

                            session.commit()

                            # Tell everyone to refresh their lists
                            await manager.broadcast({"type": "REFRESH_LIST"})

                        except ValueError:
                            pass # If bracket mapping isn't standard, safely ignore


                elif data["type"] == "STATUS_UPDATE":
                    fight = session.query(FightModel).filter(FightModel.id == data["matchId"]).first()
                    if fight:
                        # Validation: Prevent finishing if participants are TBD
                        if data["status"] == "finished" and (not fight.participant1_id or not fight.participant2_id):
                            return

                        fight.status = data["status"]

                        # If finished, determine the winner.
                        if data["status"] == "finished":
                            try:
                                s1 = int(fight.score1 or 0)
                                s2 = int(fight.score2 or 0)
                            except (ValueError, TypeError):
                                s1, s2 = 0, 0

                            winner_id = None
                            loser_id = None

                            if s1 > s2:
                                winner_id = fight.participant1_id
                                loser_id = fight.participant2_id
                            elif s2 > s1:
                                winner_id = fight.participant2_id
                                loser_id = fight.participant1_id

                            if winner_id:
                                fight.winner_id = winner_id
                                session.commit()

                                # ADVANCEMENT LOGIC
                                try:
                                    bracket = session.query(BracketModel).filter(BracketModel.id == fight.bracket_id).first()
                                    if bracket:
                                        # 1. Advance Winner
                                        if fight.round is not None and fight.pos_in_round is not None:
                                            next_coord = BracketManager.get_next_winner_coord(
                                                fight.bracket_id, fight.round, fight.pos_in_round, fight.bracket_phase
                                            )

                                            next_fight = session.query(FightModel).filter(
                                                FightModel.bracket_id == fight.bracket_id,
                                                FightModel.bracket_phase == next_coord["phase"],
                                                FightModel.round == next_coord["round"],
                                                FightModel.pos_in_round == next_coord["pos"]
                                            ).first()

                                            if next_fight:
                                                if next_coord["slot"] == "p1":
                                                    next_fight.participant1_id = winner_id
                                                else:
                                                    next_fight.participant2_id = winner_id
                                                session.commit()

                                                # Broadcast the updated target fight
                                                updated_next = await get_match_dict(next_fight.id, session)
                                                if updated_next:
                                                    await manager.broadcast({"type": "SCORE_SYNC", "matchId": next_fight.id, "match": updated_next})

                                        # 2. Handle Loser (for Double Elimination)
                                        if bracket.bracket_type == "DOUBLE_ELIMINATION" and fight.bracket_phase == "wb" and loser_id:
                                            loser_coord = BracketManager.get_next_loser_coord(
                                                fight.bracket_id, fight.round, fight.pos_in_round, fight.bracket_phase, bracket.bracket_type
                                            )
                                            if loser_coord:
                                                target_lb_fight = session.query(FightModel).filter(
                                                    FightModel.bracket_id == fight.bracket_id,
                                                    FightModel.bracket_phase == loser_coord["phase"],
                                                    FightModel.round == loser_coord["round"],
                                                    FightModel.pos_in_round == loser_coord["pos"]
                                                ).first()

                                                if target_lb_fight:
                                                    # Put loser in LB
                                                    if loser_coord["slot"] == "p1":
                                                        target_lb_fight.participant1_id = loser_id
                                                    else:
                                                        target_lb_fight.participant2_id = loser_id
                                                    session.commit()

                                                    # Broadcast the updated target LB fight
                                                    updated_lb = await get_match_dict(target_lb_fight.id, session)
                                                    if updated_lb:
                                                        await manager.broadcast({"type": "SCORE_SYNC", "matchId": target_lb_fight.id, "match": updated_lb})

                                        # 3. Handle Pool Standings
                                        if bracket.bracket_type == "POOL":
                                            all_fights = session.query(FightModel).filter(FightModel.bracket_id == fight.bracket_id).all()

                                            # Get all unique participant IDs from the fights
                                            p_ids = set()
                                            for f in all_fights:
                                                if f.participant1_id:
                                                    p_ids.add(f.participant1_id)
                                                if f.participant2_id:
                                                    p_ids.add(f.participant2_id)

                                            # We need to map GroupParticipant IDs back to Participants for calculate_pool_standings
                                            gps_with_p = session.query(GroupParticipantModel, ParticipantModel).join(
                                                ParticipantModel, GroupParticipantModel.participant_id == ParticipantModel.id
                                            ).filter(GroupParticipantModel.id.in_(list(p_ids))).all()

                                            participant_data = [
                                                {
                                                    "id": gp.id,
                                                    "name": f"{p.first_name} {p.last_name}",
                                                    "club": p.club
                                                }
                                                for gp, p in gps_with_p
                                            ]

                                            standings = BracketManager.calculate_pool_standings(all_fights, participant_data)

                                            await manager.broadcast({
                                                "type": "POOL_STANDINGS",
                                                "bracketId": fight.bracket_id,
                                                "standings": standings
                                            })
                                except Exception as e:
                                    print(f"Advancement error: {e}")

                        session.commit()
                        session.refresh(fight)

                        session.refresh(fight)

                        # Rebuild the dictionary to broadcast
                        match_dict = await get_match_dict(fight.id, session)
                        if match_dict:
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
