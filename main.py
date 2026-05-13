import contextlib
import os
from contextlib import asynccontextmanager

import requests
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from src.database import MatchModel, SessionLocal, init_db

IPPONBOARD_URL = os.getenv("IPPONBOARD_URL", "http://localhost:8080")

# Tracks the most recent match pushed to Ipponboard so the /api/ippon-score
# callback can attribute the incoming result (Ipponboard's webhook payload
# only carries names + "winner": "fighter1"|"fighter2"|"").
last_pushed_match_id: int | None = None


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

# --- Fighter resolution helpers ---

def _resolve_participants(session, gp_ids: set[int]) -> dict[int, dict]:
    """Resolve a set of group_participants.id values to their real
    participant data. Returns {gp_id: {gpId, participantId, firstName, lastName, club}}."""
    from src.database import GroupParticipantModel, ParticipantModel
    if not gp_ids:
        return {}
    rows = (
        session.query(GroupParticipantModel, ParticipantModel)
        .join(ParticipantModel, ParticipantModel.id == GroupParticipantModel.participant_id)
        .filter(GroupParticipantModel.id.in_(gp_ids))
        .all()
    )
    return {
        gp.id: {
            "gpId": gp.id,
            "participantId": p.id,
            "firstName": p.first_name or "",
            "lastName": p.last_name or "",
            "club": p.club or "",
        }
        for gp, p in rows
    }


def _build_match_dict(session, fight, fight_lookup: dict | None = None) -> dict:
    """Build the canonical match dict for /api/matches and SCORE_SYNC payloads.
    fight_lookup: optional {(bracket_id, phase, round, pos): fight_id} for batch use."""
    from src.database import FightModel

    gp_ids = {gp for gp in (fight.participant1_id, fight.participant2_id, fight.winner_id) if gp}
    resolved = _resolve_participants(session, gp_ids)

    def fighter_payload(gp_id, score):
        info = resolved.get(gp_id) if gp_id else None
        return {
            "id": str(gp_id) if gp_id else "WAIT",
            "gpId": gp_id,
            "participantId": info["participantId"] if info else None,
            "firstName": info["firstName"] if info else "",
            "lastName": info["lastName"] if info else "TBD",
            "club": info["club"] if info else "",
            "score": {"points": score if score is not None else 0},
        }

    winner_info = resolved.get(fight.winner_id) if fight.winner_id else None
    winner_name = (
        f"{winner_info['firstName']} {winner_info['lastName']}".strip()
        if winner_info else ""
    )

    next_round = (fight.round or 0) + 1
    next_pos = (fight.pos_in_round or 0) // 2
    next_key = (fight.bracket_id, fight.bracket_phase, next_round, next_pos)
    if fight_lookup is not None:
        next_match_id = fight_lookup.get(next_key)
    else:
        nxt = session.query(FightModel).filter(
            FightModel.bracket_id == fight.bracket_id,
            FightModel.bracket_phase == fight.bracket_phase,
            FightModel.round == next_round,
            FightModel.pos_in_round == next_pos,
        ).first()
        next_match_id = nxt.id if nxt else None
    next_match_pos = "p1" if (fight.pos_in_round or 0) % 2 == 0 else "p2"
    table_id = fight.table_id

    return {
        "matchId": fight.id,
        "tableId": table_id,
        "fightNr": fight.fight_number or fight.id,
        "category": f"Bracket {fight.bracket_id}" if fight.bracket_id else "Unknown Category",
        "round": (fight.round or 0) + 1,
        "posInRound": fight.pos_in_round or 0,
        "p1": fighter_payload(fight.participant1_id, fight.score1),
        "p2": fighter_payload(fight.participant2_id, fight.score2),
        "status": "finished" if fight.status == "completed" else (fight.status or "upcoming"),
        "order": fight.fight_number or fight.id,
        "restTimeMin": 0,
        "phase": fight.bracket_phase,
        "nextMatchId": next_match_id,
        "nextMatchPos": next_match_pos if next_match_id else None,
        "winnerId": fight.winner_id,
        "winnerName": winner_name,
    }

# --- API & WebSockets ---

@app.get("/api/matches")
def get_matches():
    from src.database import FightModel
    with SessionLocal() as session:
        fights = session.query(FightModel).order_by(FightModel.fight_number).all()
        fight_lookup = {
            (f.bracket_id, f.bracket_phase, f.round, f.pos_in_round): f.id
            for f in fights
        }
        match_list = [_build_match_dict(session, f, fight_lookup) for f in fights]
        return {
            "tournamentName": "Automated Tournament",
            "matches": match_list,
            "currentMatchId": last_pushed_match_id,
        }

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_json()

            with SessionLocal() as session:
                from src.database import FightModel
                if data["type"] == "SCORE_UPDATE":
                    fight = session.query(FightModel).filter(FightModel.id == data["matchId"]).first()
                    if fight:
                        if data["playerNum"] == 1:
                            fight.score1 = data["value"]
                        else:
                            fight.score2 = data["value"]
                        session.commit()
                        session.refresh(fight)
                        match_dict = _build_match_dict(session, fight)
                        await manager.broadcast({"type": "SCORE_SYNC", "matchId": data["matchId"], "match": match_dict})

                elif data["type"] == "STATUS_UPDATE":
                    fight = session.query(FightModel).filter(FightModel.id == data["matchId"]).first()
                    if fight:
                        fight.status = data["status"]
                        if data["status"] == "finished":
                            s1 = fight.score1 or 0
                            s2 = fight.score2 or 0
                            if s1 > s2:
                                fight.winner_id = fight.participant1_id
                            elif s2 > s1:
                                fight.winner_id = fight.participant2_id
                            else:
                                fight.winner_id = None
                        session.commit()
                        session.refresh(fight)
                        match_dict = _build_match_dict(session, fight)
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

@app.post("/api/push-to-ipponboard/{match_id}")
async def push_to_ipponboard(match_id: int):
    from src.database import FightModel

    with SessionLocal() as session:
        fight = session.query(FightModel).filter(FightModel.id == match_id).first()
        if not fight:
            raise HTTPException(status_code=404, detail=f"Fight {match_id} not found")
        if not fight.participant1_id or not fight.participant2_id:
            raise HTTPException(status_code=400, detail="Both participants required for Ipponboard push")

        resolved = _resolve_participants(session, {fight.participant1_id, fight.participant2_id})
        f1 = resolved.get(fight.participant1_id)
        f2 = resolved.get(fight.participant2_id)
        if not f1 or not f2:
            raise HTTPException(status_code=400, detail="Participants not found via group_participants")

        row = session.execute(
            text("SELECT g.gender, g.age_group, g.weight_class "
                 "FROM groups g JOIN brackets b ON b.group_id = g.id "
                 "WHERE b.id = :bid"),
            {"bid": fight.bracket_id},
        ).fetchone()
        gender = row[0] if row else ""
        age_group = row[1] if row else ""
        weight_class = row[2] if row else ""

        def fighter_json(info):
            return {
                "firstname": info["firstName"],
                "lastname": info["lastName"],
                "club": info["club"],
                "gender": gender,
                "agegroup": age_group,
                "weightclass": weight_class,
            }

        payload = {"fighter1": fighter_json(f1), "fighter2": fighter_json(f2)}

    try:
        resp = requests.post(f"{IPPONBOARD_URL}/fighters", json=payload, timeout=3)
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Ipponboard unreachable: {e}") from e

    global last_pushed_match_id
    if resp.ok:
        last_pushed_match_id = match_id
        await manager.broadcast({"type": "CURRENT_MATCH_SET", "matchId": match_id})

    return {
        "status": "ok" if resp.ok else "error",
        "ipponboard_status": resp.status_code,
        "ipponboard_body": resp.text,
        "payload": payload,
    }

@app.post("/api/ippon-score")
async def ippon_score(payload: dict):
    """Webhook from Ipponboard's 'Senden' button: applies the result to the
    last pushed match and broadcasts the update to all WS clients."""
    from src.database import FightModel

    global last_pushed_match_id
    if last_pushed_match_id is None:
        raise HTTPException(status_code=400, detail="No match pushed yet")

    winner = payload.get("winner", "")
    if winner not in ("fighter1", "fighter2"):
        # Ipponboard sends winner="" when no decisive result is set (no Ippon,
        # time not elapsed, no Golden Score lead). That's NOT a draw — leave
        # the match untouched and tell the operator to set a winner first.
        raise HTTPException(
            status_code=400,
            detail="Ipponboard hat keinen Sieger gemeldet. Setze den Kampf im Ipponboard zu Ende (oder nutze 'Ergebnis setzen' im JudgeFrontend für ein Unentschieden).",
        )

    with SessionLocal() as session:
        fight = session.query(FightModel).filter(FightModel.id == last_pushed_match_id).first()
        if not fight:
            raise HTTPException(status_code=404, detail=f"Match {last_pushed_match_id} not found")

        if winner == "fighter1":
            fight.score1, fight.score2 = 1, 0
            fight.winner_id = fight.participant1_id
        else:
            fight.score1, fight.score2 = 0, 1
            fight.winner_id = fight.participant2_id
        fight.status = "finished"
        session.commit()
        session.refresh(fight)

        match_dict = _build_match_dict(session, fight)

    await manager.broadcast({"type": "SCORE_SYNC", "matchId": fight.id, "match": match_dict})

    return {
        "status": "ok",
        "match_id": last_pushed_match_id,
        "winner": winner,
        "winner_name": match_dict["winnerName"],
    }
