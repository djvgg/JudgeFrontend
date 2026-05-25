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

_BRACKET_TYPE_LABELS = {
    "pools": "Pool",
    "double": "Doppelpool",
    "ko": "Doppel-KO",
    "special": "Spezial",
}


def _resolve_groups(session, bracket_ids: set[int]) -> dict[int, dict]:
    """bracket_id → {gender, age_group, weight_class, bracket_type}."""
    if not bracket_ids:
        return {}
    rows = session.execute(
        text("SELECT b.id, g.gender, g.age_group, g.weight_class, b.bracket_type "
             "FROM brackets b JOIN groups g ON g.id = b.group_id "
             "WHERE b.id IN :bids"),
        {"bids": tuple(bracket_ids)},
    ).fetchall()
    return {
        r[0]: {
            "gender": r[1] or "",
            "age_group": r[2] or "",
            "weight_class": r[3] or "",
            "bracket_type": r[4] or "",
        }
        for r in rows
    }


def _group_label(group_info: dict) -> str:
    """Bracket-level label (no pool suffix): 'U18 w -70kg'."""
    parts = [group_info.get("age_group", ""), group_info.get("gender", ""), group_info.get("weight_class", "")]
    return " ".join(p for p in parts if p)


def _category_label(fight, group_info: dict) -> str:
    """Per-fight label: 'U18 w -70kg' (KO) or 'U18 w -70kg · Pool 1' (pool)."""
    base = _group_label(group_info)
    if fight.bracket_phase == "pool" and fight.pool_index is not None:
        pool = f"Pool {fight.pool_index + 1}"
        return f"{base} · {pool}" if base else pool
    if base:
        return base
    return f"Bracket {fight.bracket_id}" if fight.bracket_id else "Unknown"


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


def _build_match_dict(session, fight, fight_lookup: dict | None = None,
                      group_lookup: dict | None = None) -> dict:
    """Build the canonical match dict for /api/matches and SCORE_SYNC payloads.
    fight_lookup: optional {(bracket_id, phase, round, pos): fight_id} for batch use.
    group_lookup: optional {bracket_id: {gender, age_group, weight_class}}."""
    from src.database import FightModel

    gp_ids = {gp for gp in (fight.participant1_id, fight.participant2_id, fight.winner_id) if gp}
    resolved = _resolve_participants(session, gp_ids)

    if group_lookup is not None:
        group_info = group_lookup.get(fight.bracket_id, {})
    else:
        group_info = _resolve_groups(session, {fight.bracket_id}).get(fight.bracket_id, {}) if fight.bracket_id else {}

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
        "categoryLabel": _category_label(fight, group_info),
        "groupLabel": _group_label(group_info) or (f"Bracket {fight.bracket_id}" if fight.bracket_id else "Unknown"),
        "bracketId": fight.bracket_id,
        "bracketType": group_info.get("bracket_type", ""),
        "bracketTypeLabel": _BRACKET_TYPE_LABELS.get(group_info.get("bracket_type", ""), group_info.get("bracket_type", "")),
        "gender": group_info.get("gender", ""),
        "ageGroup": group_info.get("age_group", ""),
        "weightClass": group_info.get("weight_class", ""),
        "poolIndex": fight.pool_index,
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
        group_lookup = _resolve_groups(session, {f.bracket_id for f in fights if f.bracket_id})
        match_list = [_build_match_dict(session, f, fight_lookup, group_lookup) for f in fights]
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
            match_id = data.get("matchId")

            with SessionLocal() as session:
                from src.database import FightModel
                if data["type"] == "SCORE_UPDATE":
                    fight = session.query(FightModel).filter(FightModel.id == match_id).first()
                    if not fight:
                        await _send_unknown_match_error(websocket, match_id, "SCORE_UPDATE")
                        continue
                    value = data["value"]
                    # Schema is INTEGER; cast defensively in case a client sends a string.
                    if isinstance(value, str):
                        value = int(value) if value.strip() else None
                    if data["playerNum"] == 1:
                        fight.score1 = value
                    else:
                        fight.score2 = value
                    session.commit()
                    session.refresh(fight)
                    match_dict = _build_match_dict(session, fight)
                    await manager.broadcast({"type": "SCORE_SYNC", "matchId": match_id, "match": match_dict})

                elif data["type"] == "STATUS_UPDATE":
                    fight = session.query(FightModel).filter(FightModel.id == match_id).first()
                    if not fight:
                        await _send_unknown_match_error(websocket, match_id, "STATUS_UPDATE")
                        continue
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
                    propagated_fight = None
                    pool_completion = None
                    double_pool_final = None
                    if data["status"] == "finished" and fight.winner_id is not None:
                        if fight.bracket_phase in ("wb", "lb"):
                            propagated_fight = _propagate_winner(session, fight)
                            # Welle 2B.2: Wenn das gerade beendete Fight ein Doppelpool-Finale war,
                            # haben wir keinen Folge-Fight (propagated_fight=None) — dann Placements setzen.
                            if propagated_fight is None:
                                double_pool_final = _finalize_double_pool_bracket(session, fight)
                        elif fight.bracket_phase == "pool":
                            pool_completion = _finalize_pool_bracket_if_complete(session, fight)
                    match_dict = _build_match_dict(session, fight)
                    await manager.broadcast({"type": "SCORE_SYNC", "matchId": match_id, "match": match_dict})
                    if propagated_fight is not None:
                        next_dict = _build_match_dict(session, propagated_fight)
                        await manager.broadcast({
                            "type": "SCORE_SYNC",
                            "matchId": propagated_fight.id,
                            "match": next_dict,
                        })
                    if pool_completion is not None:
                        await manager.broadcast({
                            "type": pool_completion["event"],
                            "bracketId": pool_completion["bracket_id"],
                            **({"placements": pool_completion["placements"]}
                               if "placements" in pool_completion else {}),
                            **({"newFightIds": pool_completion["new_fight_ids"]}
                               if "new_fight_ids" in pool_completion else {}),
                        })
                    if double_pool_final is not None:
                        await manager.broadcast({
                            "type": "BRACKET_COMPLETED",
                            "bracketId": double_pool_final["bracket_id"],
                            "placements": double_pool_final["placements"],
                        })

                elif data["type"] == "REORDER":
                    for m_id, order in data["orders"].items():
                        session.query(FightModel).filter(FightModel.id == int(m_id)).update({"fight_number": order})
                    session.commit()
                    await manager.broadcast({"type": "REFRESH_LIST"})

                elif data["type"] == "SIGNAL":
                    await manager.broadcast(data)

    except WebSocketDisconnect:
        manager.disconnect(websocket)
    except RuntimeError as e:
        # `receive_json()` on a half-closed socket raises RuntimeError
        # ("WebSocket is not connected"). Treat as a disconnect.
        if "not connected" in str(e):
            manager.disconnect(websocket)
        else:
            raise


def _propagate_winner(session, fight):
    """Welle 2A: KO-Tree-Propagation. Setzt den winner als Teilnehmer im
    direkten Folge-Fight (round+1, pos_in_round//2; Slot p1/p2 je nach
    pos_in_round%2). Nur fuer KO-artige Phasen ('wb', 'lb').

    Returns das aktualisierte Folge-Fight-Objekt oder None (kein Folge-Fight
    vorgesehen oder noch nicht angelegt).
    """
    import logging

    from src.database import FightModel as _FightModel

    if fight.bracket_phase not in ("wb", "lb"):
        # Pool-Fights haben keinen Folge-Fight in dieser Welle (round=NULL,
        # Pool->KO ist Welle 2B).
        return None
    if fight.round is None or fight.pos_in_round is None:
        return None

    next_round = fight.round + 1
    next_pos = fight.pos_in_round // 2
    slot_attr = "participant1_id" if fight.pos_in_round % 2 == 0 else "participant2_id"

    next_fight = session.query(_FightModel).filter(
        _FightModel.bracket_id == fight.bracket_id,
        _FightModel.bracket_phase == fight.bracket_phase,
        _FightModel.round == next_round,
        _FightModel.pos_in_round == next_pos,
    ).first()

    if next_fight is None:
        logging.getLogger("uvicorn.error").warning(
            "KO-Propagation: kein Folge-Fight bei (bracket=%s, phase=%s, round=%s, pos=%s) "
            "fuer winner aus fight #%s",
            fight.bracket_id, fight.bracket_phase, next_round, next_pos, fight.id,
        )
        return None

    setattr(next_fight, slot_attr, fight.winner_id)
    session.commit()
    session.refresh(next_fight)
    return next_fight


def _compute_pool_standings(session, bracket_id, pool_index=None):
    """Welle 2B.1+2B.2: Sortiere alle GroupParticipants eines Pools nach DJB-Hierarchie.

    Hierarchie (hoeher gewinnt):
      1. Anzahl Siege (winner_id == gp_id ueber finished/bye Fights).
      2. Direkter Vergleich bei 2-Personen-Gleichstand: Sieger des
         Head-to-Head-Pool-Fights kommt vorne.
      3. Pluspunkt-Differenz (Σ eigene Scores − Σ Gegner-Scores).
      4. Stabile gp_id-Sortierung als deterministischer Pseudo-Zufall.

    Referenz: Libraries/wichtigedocs/20763-DJB_Regeln_und_Ordnungen_Platzierungen_im_Pool_2023.pdf.
    Spiegel-Funktion in edv (tournament_service.compute_pool_standings).

    Args:
        pool_index: Wenn gesetzt, nur Fights mit diesem pool_index (fuer Doppelpool
                    pro Pool getrennt). None = alle Pool-Fights des Brackets.

    Returns: geordnete Liste der gp_ids, [1.Platz, 2.Platz, ...].
    """
    from src.database import FightModel as _FightModel

    q = session.query(_FightModel).filter(
        _FightModel.bracket_id == bracket_id,
        _FightModel.bracket_phase == "pool",
    )
    if pool_index is not None:
        q = q.filter(_FightModel.pool_index == pool_index)
    fights = q.all()

    # GP-Set sammeln (in p1/p2 jedes Fights)
    gp_ids: set[int] = set()
    for f in fights:
        if f.participant1_id is not None:
            gp_ids.add(f.participant1_id)
        if f.participant2_id is not None and f.participant2_id != f.participant1_id:
            gp_ids.add(f.participant2_id)

    # Wins zaehlen
    wins: dict[int, int] = {gp: 0 for gp in gp_ids}
    plus: dict[int, int] = {gp: 0 for gp in gp_ids}  # Σ eigene Scores
    minus: dict[int, int] = {gp: 0 for gp in gp_ids}  # Σ Gegner-Scores
    head_to_head: dict[tuple[int, int], int] = {}     # (gp_a, gp_b) -> winner_gp

    for f in fights:
        if f.status not in ("finished", "bye"):
            continue
        if f.participant1_id is None or f.participant2_id is None:
            continue
        s1 = f.score1 or 0
        s2 = f.score2 or 0
        plus[f.participant1_id] = plus.get(f.participant1_id, 0) + s1
        minus[f.participant1_id] = minus.get(f.participant1_id, 0) + s2
        # bye: p1 == p2 (Konvention bis Welle 4); duplikate Buchung vermeiden
        if f.participant2_id != f.participant1_id:
            plus[f.participant2_id] = plus.get(f.participant2_id, 0) + s2
            minus[f.participant2_id] = minus.get(f.participant2_id, 0) + s1
        if f.winner_id is not None:
            wins[f.winner_id] = wins.get(f.winner_id, 0) + 1
            if f.participant2_id != f.participant1_id:
                key = tuple(sorted([f.participant1_id, f.participant2_id]))
                head_to_head[key] = f.winner_id

    def sort_key(gp: int):
        # Negativ fuer absteigend bei reverse=False
        return (
            -wins.get(gp, 0),
            -(plus.get(gp, 0) - minus.get(gp, 0)),
            gp,
        )

    ordered = sorted(gp_ids, key=sort_key)

    # H2H-Tie-Break bei exakt 2-Personen-Gleichstand auf der Wins-Stufe
    # (Pluspunkt-Diff laesst's evtl. trotzdem auseinander; wenn auch das
    # gleich ist, entscheidet H2H ueber die alphabetische gp-id-Sortierung).
    i = 0
    while i < len(ordered) - 1:
        a, b = ordered[i], ordered[i + 1]
        if wins.get(a, 0) == wins.get(b, 0) and (plus.get(a, 0) - minus.get(a, 0)) == (plus.get(b, 0) - minus.get(b, 0)):
            key = tuple(sorted([a, b]))
            h2h_winner = head_to_head.get(key)
            if h2h_winner == b:
                ordered[i], ordered[i + 1] = b, a
        i += 1

    return ordered


def _finalize_pool_bracket_if_complete(session, fight):
    """Welle 2B.1 (Single-Pool) + 2B.2 (Doppelpool-Trigger):
    Wenn alle Pool-Fights eines Brackets durch sind:
      - 'pools' (Single): Standings persistieren + bracket.status='completed'.
      - 'double' (Doppelpool): KO-Stage anlegen (eager); Bracket bleibt pending.
    Idempotent.

    Returns: dict mit Event-Daten oder None.
    """
    from src.database import BracketModel, FightModel as _FightModel

    if fight.bracket_phase != "pool":
        return None

    bracket = session.query(BracketModel).filter(BracketModel.id == fight.bracket_id).first()
    if bracket is None:
        return None
    if bracket.status == "completed":
        return None

    open_fights = session.query(_FightModel).filter(
        _FightModel.bracket_id == fight.bracket_id,
        _FightModel.bracket_phase == "pool",
        ~_FightModel.status.in_(["finished", "bye"]),
    ).count()
    if open_fights > 0:
        return None

    # Doppelpool: KO-Stage eager anlegen, Bracket bleibt pending.
    if bracket.bracket_type == "double":
        # Idempotent: wenn KO-Stage schon existiert, nichts tun.
        existing_ko = session.query(_FightModel).filter(
            _FightModel.bracket_id == fight.bracket_id,
            _FightModel.bracket_phase == "wb",
        ).count()
        if existing_ko > 0:
            return None
        ko_fights = _initialize_double_pool_ko_stage(session, bracket)
        if not ko_fights:
            return None
        return {
            "event": "DOUBLE_POOL_KO_STAGE_CREATED",
            "bracket_id": bracket.id,
            "new_fight_ids": [f.id for f in ko_fights],
        }

    # Single-Pool: Standings persistieren + completed.
    standings = _compute_pool_standings(session, fight.bracket_id)
    bracket.first_place = standings[0] if len(standings) >= 1 else None
    bracket.second_place = standings[1] if len(standings) >= 2 else None
    bracket.third_place_1 = standings[2] if len(standings) >= 3 else None
    bracket.status = "completed"
    session.commit()

    return {
        "event": "BRACKET_COMPLETED",
        "bracket_id": bracket.id,
        "placements": {
            "first": bracket.first_place,
            "second": bracket.second_place,
            "third_1": bracket.third_place_1,
        },
    }


def _initialize_double_pool_ko_stage(session, bracket):
    """Welle 2B.2: Lege 3 KO-Stage-Fights an (HF1, HF2, Finale) basierend
    auf den Pool-Standings beider Pools. Crossover: A1 vs B2, A2 vs B1.

    Returns: Liste der 3 neuen Fight-Objekte oder [] bei Fehler.
    """
    import logging
    from src.database import FightModel as _FightModel

    a_standings = _compute_pool_standings(session, bracket.id, pool_index=0)
    b_standings = _compute_pool_standings(session, bracket.id, pool_index=1)
    if len(a_standings) < 2 or len(b_standings) < 2:
        logging.getLogger("uvicorn.error").warning(
            "Doppelpool bracket=%s: Pool A=%s, Pool B=%s — zu wenige Teilnehmer fuer KO-Stage",
            bracket.id, a_standings, b_standings,
        )
        return []
    a1, a2 = a_standings[0], a_standings[1]
    b1, b2 = b_standings[0], b_standings[1]

    max_fn = session.query(_FightModel).filter(
        _FightModel.bracket_id == bracket.id,
    ).count()  # robust auch wenn fight_number NULL

    # table_id von einem existierenden Pool-Fight uebernehmen, sonst filtert
    # das Frontend (Listenansicht via tableId) die neuen KO-Fights aus.
    sample_pool = session.query(_FightModel).filter(
        _FightModel.bracket_id == bracket.id,
        _FightModel.bracket_phase == "pool",
        _FightModel.table_id.isnot(None),
    ).first()
    inherited_table_id = sample_pool.table_id if sample_pool else None

    hf1 = _FightModel(
        bracket_id=bracket.id, participant1_id=a1, participant2_id=b2,
        fight_number=max_fn + 1, status="pending",
        bracket_phase="wb", round=1, pos_in_round=0, pool_index=None,
        table_id=inherited_table_id,
    )
    hf2 = _FightModel(
        bracket_id=bracket.id, participant1_id=a2, participant2_id=b1,
        fight_number=max_fn + 2, status="pending",
        bracket_phase="wb", round=1, pos_in_round=1, pool_index=None,
        table_id=inherited_table_id,
    )
    final = _FightModel(
        bracket_id=bracket.id, participant1_id=None, participant2_id=None,
        fight_number=max_fn + 3, status="pending",
        bracket_phase="wb", round=2, pos_in_round=0, pool_index=None,
        table_id=inherited_table_id,
    )
    session.add_all([hf1, hf2, final])
    session.commit()
    return [hf1, hf2, final]


def _finalize_double_pool_bracket(session, fight):
    """Welle 2B.2: Nach Finale-Sieg im Doppelpool: setze first/second aus Finale,
    third_place_1/2 aus den HF-Verlierern (kein Bronze-Match: 2 dritte Plaetze).

    Bedingungen: fight ist round=2, pos_in_round=0 im 'wb', bracket_type='double'.
    Idempotent.

    Returns: placements-Dict oder None.
    """
    from src.database import BracketModel, FightModel as _FightModel

    if fight.bracket_phase != "wb":
        return None
    if fight.round != 2 or fight.pos_in_round != 0:
        return None
    if fight.winner_id is None:
        return None

    bracket = session.query(BracketModel).filter(BracketModel.id == fight.bracket_id).first()
    if bracket is None or bracket.bracket_type != "double":
        return None
    if bracket.status == "completed":
        return None

    # Verlierer des Finales
    loser_final = (
        fight.participant1_id if fight.winner_id == fight.participant2_id
        else fight.participant2_id
    )

    # HF1 + HF2 holen
    semis = session.query(_FightModel).filter(
        _FightModel.bracket_id == bracket.id,
        _FightModel.bracket_phase == "wb",
        _FightModel.round == 1,
    ).order_by(_FightModel.pos_in_round).all()
    if len(semis) < 2:
        return None
    hf1, hf2 = semis[0], semis[1]
    hf1_loser = (
        hf1.participant1_id if hf1.winner_id == hf1.participant2_id
        else hf1.participant2_id
    ) if hf1.winner_id else None
    hf2_loser = (
        hf2.participant1_id if hf2.winner_id == hf2.participant2_id
        else hf2.participant2_id
    ) if hf2.winner_id else None

    bracket.first_place = fight.winner_id
    bracket.second_place = loser_final
    bracket.third_place_1 = hf1_loser
    bracket.third_place_2 = hf2_loser
    bracket.status = "completed"
    session.commit()

    return {
        "bracket_id": bracket.id,
        "placements": {
            "first": bracket.first_place,
            "second": bracket.second_place,
            "third_1": bracket.third_place_1,
            "third_2": bracket.third_place_2,
        },
    }


async def _send_unknown_match_error(websocket: WebSocket, match_id, event_type: str) -> None:
    """Surface a previously-silent drop. Client sees the error instead of
    a phantom-success simulation. Server-side log line for ops."""
    import logging
    logging.getLogger("uvicorn.error").warning(
        "WS %s: unknown matchId=%r — dropping message", event_type, match_id
    )
    with contextlib.suppress(Exception):
        await websocket.send_json({
            "type": "ERROR",
            "code": "unknown_match",
            "matchId": match_id,
            "event": event_type,
        })

@app.post("/api/reconcile-brackets")
async def reconcile_brackets():
    """Welle 2B.2 Catchup: bei direktem DB-Write (z.B. via edv) laeuft der
    WS-Handler nicht, also bleibt KO-Stage / Placement-Trigger aus. Diese
    Route holt das nach: pro Bracket pruefen ob Pool-Phase durch ist und
    ggf. KO-Stage anlegen bzw. Single-Pool-Standings setzen, plus nach
    Doppelpool-Finale die Placements eintragen.

    Idempotent. Sicher mehrfach aufrufbar.
    """
    from src.database import BracketModel, FightModel as _FightModel
    actions = []
    with SessionLocal() as session:
        brackets = session.query(BracketModel).filter(
            BracketModel.status != "completed",
        ).all()
        for b in brackets:
            if b.bracket_type not in ("pools", "double"):
                continue
            pool_total = session.query(_FightModel).filter(
                _FightModel.bracket_id == b.id,
                _FightModel.bracket_phase == "pool",
            ).count()
            if pool_total == 0:
                continue
            pool_open = session.query(_FightModel).filter(
                _FightModel.bracket_id == b.id,
                _FightModel.bracket_phase == "pool",
                ~_FightModel.status.in_(["finished", "bye"]),
            ).count()
            if pool_open > 0:
                continue
            if b.bracket_type == "double":
                wb_existing = session.query(_FightModel).filter(
                    _FightModel.bracket_id == b.id,
                    _FightModel.bracket_phase == "wb",
                ).count()
                if wb_existing > 0:
                    # KO-Stage existiert; pruefe ob Finale finished -> Placements
                    final = session.query(_FightModel).filter(
                        _FightModel.bracket_id == b.id,
                        _FightModel.bracket_phase == "wb",
                        _FightModel.round == 2,
                        _FightModel.pos_in_round == 0,
                    ).first()
                    if final and final.status == "finished" and final.winner_id is not None:
                        result = _finalize_double_pool_bracket(session, final)
                        if result:
                            actions.append({"bracket_id": b.id, "action": "completed", "placements": result["placements"]})
                    continue
                ko = _initialize_double_pool_ko_stage(session, b)
                if ko:
                    actions.append({"bracket_id": b.id, "action": "ko_stage_created", "new_fight_ids": [f.id for f in ko]})
            else:
                # bracket_type == 'pools'
                # imitiere die Logik von _finalize_pool_bracket_if_complete
                # (ohne ein Fight-Argument; wir nehmen irgendeinen Pool-Fight)
                any_pool_fight = session.query(_FightModel).filter(
                    _FightModel.bracket_id == b.id,
                    _FightModel.bracket_phase == "pool",
                ).first()
                if any_pool_fight is not None:
                    result = _finalize_pool_bracket_if_complete(session, any_pool_fight)
                    if result:
                        actions.append({"bracket_id": b.id, "action": result["event"], **{k: v for k, v in result.items() if k not in ("event", "bracket_id")}})
    # Broadcast die Events an alle WS-Clients (damit das UI updated)
    for a in actions:
        msg = {"type": a["action"], "bracketId": a["bracket_id"]}
        if "placements" in a:
            msg["placements"] = a["placements"]
        if "new_fight_ids" in a:
            msg["newFightIds"] = a["new_fight_ids"]
        await manager.broadcast(msg)
    return {"status": "ok", "actions": actions}


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

        group_info = _resolve_groups(session, {fight.bracket_id}).get(fight.bracket_id, {}) if fight.bracket_id else {}
        gender = group_info.get("gender", "")
        age_group = group_info.get("age_group", "")
        weight_class = group_info.get("weight_class", "")

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

@app.post("/api/reopen-match/{match_id}")
async def reopen_match(match_id: int):
    from src.database import FightModel

    with SessionLocal() as session:
        fight = session.query(FightModel).filter(FightModel.id == match_id).first()
        if not fight:
            raise HTTPException(status_code=404, detail=f"Match {match_id} not found")
        if fight.status == "bye":
            raise HTTPException(status_code=400, detail="Freilose können nicht wieder gestartet werden")

        fight.status = "pending"
        fight.winner_id = None
        fight.score1 = None
        fight.score2 = None
        session.commit()
        session.refresh(fight)
        match_dict = _build_match_dict(session, fight)

    await manager.broadcast({"type": "SCORE_SYNC", "matchId": match_id, "match": match_dict})
    return {"status": "ok", "matchId": match_id}

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
