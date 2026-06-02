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


def _pool_label(fight) -> str:
    """'Pool N' (1-based) for pool fights, '' otherwise — sent to Ipponboard verbatim."""
    if fight.bracket_phase == "pool" and fight.pool_index is not None:
        return f"Pool {fight.pool_index + 1}"
    return ""


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


def _stage_label(bracket_type, num_rounds, phase, rnd, pos):
    """Anzeige-Label fuer Endkaempfe ('Finale' / 'Kampf um Platz 3'), sonst None.
    Eine Quelle fuer Baum-Badge und Kampfliste. Topologie wie _LB_STRUCTURE /
    _initialize_double_pool_ko_stage — keine eigene.
      * Doppel-KO (ko) 8er/16er: WB-Finale (wb r=num_rounds-1) + die zwei
        Bronze-Matches (lb r=bronze_round).
      * Doppelpool (double): nur Finale (wb r2 p0); 3. Plaetze = HF-Verlierer direkt.
      * 32er (ko, num_rounds=5): nur Finale (lb r7 p0 = Kf61); keine dedizierten
        Bronze-Matches (3. = Medaillen-Round-Verlierer)."""
    if bracket_type == "double":
        return "Finale" if (phase == "wb" and rnd == 2 and pos == 0) else None
    if bracket_type != "ko":
        return None
    if num_rounds in (3, 4):
        if phase == "wb" and rnd == num_rounds - 1:
            return "Finale"
        struct = _LB_STRUCTURE.get(num_rounds)
        if struct is not None and phase == "lb" and rnd == struct["bronze_round"]:
            return "Kampf um Platz 3"
    elif num_rounds == 5 and phase == "lb" and rnd == 7 and pos == 0:
        return "Finale"
    return None


def _build_match_dict(session, fight, fight_lookup: dict | None = None,
                      group_lookup: dict | None = None,
                      num_rounds_lookup: dict | None = None) -> dict:
    """Build the canonical match dict for /api/matches and SCORE_SYNC payloads.
    fight_lookup: optional {(bracket_id, phase, round, pos): fight_id} for batch use.
    group_lookup: optional {bracket_id: {gender, age_group, weight_class}}.
    num_rounds_lookup: optional {bracket_id: wb_num_rounds} so the batch path needn't
    re-count per fight (drives the 32er nextMatch wiring)."""
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

    # nextMatchId/-Pos = wohin der SIEGER vorrückt (renderKoTree baut den Baum daraus).
    # MUSS der echten Sieger-Kante folgen, NICHT blind dem Binärbaum:
    #   - WB (alle Größen): Binärbaum (round+1, pos//2) ist korrekt.
    #   - LB (Trostrunde, alle Größen): `_lb_winner_target` — 8/16 via `_LB_STRUCTURE`
    #     (pos-erhaltend vs Binärbaum-pos//2 ⇒ sonst Bronze-Feeder vertauscht!), 32er
    #     via `_KO32_CONSUMERS`; None am Bronze/Endkampf.
    #   - 32er WB-Halbfinale: HF-Sieger → Medaillen-Round (LB), Cross-Kante aus
    #     `_KO32_CONSUMERS` (Binärbaum zeigte auf nicht-existentes wb r4 ⇒ Waise).
    next_key = None
    next_match_pos = None
    node = (fight.bracket_phase, fight.round, fight.pos_in_round)
    num_rounds = None
    if fight.bracket_id and fight.bracket_phase in ("wb", "lb"):
        if num_rounds_lookup is not None:
            num_rounds = num_rounds_lookup.get(fight.bracket_id)
        elif session is not None:
            num_rounds = _wb_num_rounds(session, fight.bracket_id)
    if fight.bracket_phase == "lb" and num_rounds in (3, 4, 5) and fight.round is not None:
        tgt = _lb_winner_target(num_rounds, fight.round, fight.pos_in_round)
        if tgt is not None:
            lb_round, lb_pos, slot = tgt
            next_key = (fight.bracket_id, "lb", lb_round, lb_pos)
            next_match_pos = "p1" if slot == 1 else "p2"
    elif num_rounds == 5 and node in _KO32_CONSUMERS:
        w_edge = next(((dst, slot) for dst, slot, kind in _KO32_CONSUMERS[node]
                       if kind == "W"), None)
        if w_edge is not None:
            (w_phase, w_round, w_pos), w_slot = w_edge
            next_key = (fight.bracket_id, w_phase, w_round, w_pos)
            next_match_pos = "p1" if w_slot == 1 else "p2"
    elif fight.round is not None:
        next_round = fight.round + 1
        next_pos = (fight.pos_in_round or 0) // 2
        next_key = (fight.bracket_id, fight.bracket_phase, next_round, next_pos)
        next_match_pos = "p1" if (fight.pos_in_round or 0) % 2 == 0 else "p2"
    if next_key is None:
        next_match_id = None
    elif fight_lookup is not None:
        next_match_id = fight_lookup.get(next_key)
    else:
        nxt = session.query(FightModel).filter(
            FightModel.bracket_id == next_key[0],
            FightModel.bracket_phase == next_key[1],
            FightModel.round == next_key[2],
            FightModel.pos_in_round == next_key[3],
        ).first()
        next_match_id = nxt.id if nxt else None
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
        "stageLabel": _stage_label(group_info.get("bracket_type", ""), num_rounds,
                                   fight.bracket_phase, fight.round, fight.pos_in_round),
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
        # Herkunft je Slot ("Sieger/Verlierer aus #N") — in get_matches gefuellt,
        # wo die turnierweite running_nr bekannt ist; sonst None.
        "p1From": None,
        "p2From": None,
    }

# --- API & WebSockets ---

@app.get("/api/matches")
def get_matches():
    from src.database import FightModel
    with SessionLocal() as session:
        # Erst den kompletten KO/Doppel-KO-Baum vorab anlegen (TBD), DANN Freilos-Sieger
        # propagieren (Byes fuellen die WB-r1-Slots der eben angelegten Zeilen). Beide
        # idempotent — Steady-State schreibt nichts.
        _ensure_ko_tree_materialized(session)
        _resolve_pending_byes(session)
        # WB-Freilose hinterlassen tote LB-Slots → als Walkover-Freilos aufloesen,
        # sonst kommt der Trostrunden-Kampf nie zustande.
        _resolve_lb_byes(session)
        fights = session.query(FightModel).order_by(FightModel.fight_number).all()
        fight_lookup = {
            (f.bracket_id, f.bracket_phase, f.round, f.pos_in_round): f.id
            for f in fights
        }
        group_lookup = _resolve_groups(session, {f.bracket_id for f in fights if f.bracket_id})
        # Per-bracket WB round count (once), so _build_match_dict can wire the 32er
        # nextMatch via the feeder graph without re-counting per fight.
        num_rounds_lookup = {
            bid: _wb_num_rounds(session, bid)
            for bid in {f.bracket_id for f in fights if f.bracket_id}
        }
        # Stable, tournament-wide running fight number (1..N) by creation order, so
        # every fight has its own unique identifier independent of the per-bracket
        # fight_number / the reorderable list position.
        running_nr = {f.id: i + 1 for i, f in enumerate(sorted(fights, key=lambda x: x.id))}
        # Herkunft je Slot ("Sieger/Verlierer aus #N"); N = dieselbe running_nr.
        slot_sources = _compute_slot_sources(session, fight_lookup)
        match_list = []
        for f in fights:
            d = _build_match_dict(session, f, fight_lookup, group_lookup, num_rounds_lookup)
            d["fightNr"] = running_nr[f.id]
            src = slot_sources.get((f.bracket_id, f.bracket_phase, f.round, f.pos_in_round))
            if src:
                for slot, key in ((1, "p1From"), (2, "p2From")):
                    s = src.get(slot)
                    if s and s["fightId"] in running_nr:
                        d[key] = {"kind": s["kind"], "fightNr": running_nr[s["fightId"]]}
            match_list.append(d)
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
                    doppel_ko_final = None
                    lb_touched = False
                    if data["status"] == "finished" and fight.winner_id is not None:
                        _btype = _num_rounds = None
                        if fight.bracket_phase in ("wb", "lb"):
                            from src.database import BracketModel as _BracketModel
                            _bracket = session.query(_BracketModel).filter(
                                _BracketModel.id == fight.bracket_id).first()
                            _btype = _bracket.bracket_type if _bracket else None
                            _num_rounds = (_wb_num_rounds(session, fight.bracket_id)
                                           if _btype == "ko" else 0)
                        if _btype == "ko" and _num_rounds == 5:
                            # 32er: abweichende Medaillen-Topologie über den Feeder-Graphen.
                            # Ein Fight schiebt Sieger UND Verlierer (Drop + Vorrücken in einem).
                            lb_touched = bool(_apply_ko_graph_result(session, fight))
                            doppel_ko_final = _finalize_doppel_ko_bracket(session, fight)
                        elif fight.bracket_phase == "wb":
                            propagated_fight = _propagate_winner(session, fight)
                            if _btype == "ko":
                                # Doppel-KO: Verlierer in die Trostrunde droppen.
                                lb_touched = _drop_loser_to_lb(session, fight) is not None
                            if propagated_fight is None:
                                # Kein Folge-Fight = Finale durch.
                                if _btype == "double":
                                    double_pool_final = _finalize_double_pool_bracket(session, fight)
                                elif _btype == "ko":
                                    doppel_ko_final = _finalize_doppel_ko_bracket(session, fight)
                        elif fight.bracket_phase == "lb":
                            # Within-LB pos-erhaltend vorrücken; am Bronze-Match → finalisieren.
                            propagated_fight = _advance_lb_winner(session, fight)
                            lb_touched = True
                            if propagated_fight is None:
                                doppel_ko_final = _finalize_doppel_ko_bracket(session, fight)
                        elif fight.bracket_phase == "pool":
                            pool_completion = _finalize_pool_bracket_if_complete(session, fight)
                        # Frisch gedroppter realer Verlierer, dessen Geschwister-LB-Slot
                        # tot ist (WB-Freilos) ⇒ sofort als Walkover-Freilos auflösen.
                        if _btype == "ko" and _resolve_lb_byes(session) > 0:
                            lb_touched = True
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
                    if doppel_ko_final is not None:
                        await manager.broadcast({
                            "type": "BRACKET_COMPLETED",
                            "bracketId": doppel_ko_final["bracket_id"],
                            "placements": doppel_ko_final["placements"],
                        })
                    if lb_touched:
                        # Neue/gefüllte lb-Fights → Frontend neu laden lassen.
                        await manager.broadcast({"type": "REFRESH_LIST"})

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
        # Lazy-create the next-round WB fight on demand: edv only seeds 'wb' round 0
        # for standalone KO brackets, so rounds 1+ don't exist until reached. Only
        # within the bracket's own tree (never behind the final) and never for
        # double-pool brackets — those have no 'wb' round 0, so num_rounds==0 here
        # and we fall through to the old "no follow-up" behaviour (→ finalize hook).
        num_rounds = _wb_num_rounds(session, fight.bracket_id) if fight.bracket_phase == "wb" else 0
        if num_rounds and next_round <= num_rounds - 1:
            next_fight = _FightModel(
                bracket_id=fight.bracket_id, bracket_phase=fight.bracket_phase,
                round=next_round, pos_in_round=next_pos, status="pending",
                fight_number=_next_fight_number(session, fight.bracket_id),
                table_id=fight.table_id,
            )
            setattr(next_fight, slot_attr, fight.winner_id)
            session.add(next_fight)
            session.commit()
            session.refresh(next_fight)
            return next_fight
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


def _resolve_pending_byes(session):
    """Propagiert bereits entschiedene WB-Freilose (Byes) in ihren Folge-Kampf.

    edv seedet 'wb' Runde 0 inkl. Freilose (als p1==p2, status='bye', winner_id
    gesetzt). Diese laufen NIE durch den STATUS_UPDATE-finished-Handler, also wird
    `_propagate_winner` fuer sie nie aufgerufen → der Freilos-Sieger erreicht die
    naechste Runde nicht und der Folge-Kampf bleibt unstartbar (ein Slot NULL).

    Loest das einmalig und idempotent beim Laden/Import auf: jeder offene Bye wird
    durch `_propagate_winner` geschickt (legt den Folge-Kampf lazy an und setzt den
    Sieger in den richtigen Slot). Ein Freilos hat KEINEN echten Verlierer (p1==p2)
    → es wird NIE in die Trostrunde (`_drop_loser_to_lb`) gedroppt. Zwei benachbarte
    Freilose treffen sich dadurch korrekt im selben Folge-Kampf (Runde 1).

    Nur fuer bracket_type='ko'. Returns die Anzahl tatsaechlich propagierter Byes.
    """
    from src.database import BracketModel as _BracketModel
    from src.database import FightModel as _FightModel

    ko_ids = {b.id for b in session.query(_BracketModel).filter(
        _BracketModel.bracket_type == "ko").all()}
    if not ko_ids:
        return 0

    byes = session.query(_FightModel).filter(
        _FightModel.bracket_phase == "wb",
        _FightModel.status == "bye",
        _FightModel.winner_id.isnot(None),
        _FightModel.bracket_id.in_(ko_ids),
    ).all()

    resolved = 0
    for bye in byes:
        if bye.round is None or bye.pos_in_round is None:
            continue
        # Idempotenz: ueberspringen, wenn der Ziel-Slot den Sieger schon traegt.
        next_round = bye.round + 1
        next_pos = bye.pos_in_round // 2
        slot_attr = "participant1_id" if bye.pos_in_round % 2 == 0 else "participant2_id"
        nxt = session.query(_FightModel).filter(
            _FightModel.bracket_id == bye.bracket_id,
            _FightModel.bracket_phase == "wb",
            _FightModel.round == next_round,
            _FightModel.pos_in_round == next_pos,
        ).first()
        if nxt is not None and getattr(nxt, slot_attr) == bye.winner_id:
            continue
        if _propagate_winner(session, bye) is not None:
            resolved += 1
    return resolved


def _wb_loser_target(num_rounds, wb_round, wb_pos):
    """LB-Ziel (lb_round, lb_pos, slot), in den der VERLIERER eines WB-Fights droppt —
    aus derselben Topologie wie `_drop_loser_to_lb`. None, wenn kein Drop (z.B. Finale)."""
    if num_rounds == 5:
        for (_phase, rnd, pos), slot, kind in _KO32_CONSUMERS.get(("wb", wb_round, wb_pos), []):
            if kind == "L":
                return (rnd, pos, slot)
        return None
    struct = _LB_STRUCTURE.get(num_rounds)
    if struct is None:
        return None
    drop = struct["wb_drop"].get(wb_round)
    if drop is None:
        return None
    lb_round, lb_pos, slot = drop(wb_pos)
    return (lb_round, lb_pos, slot)


def _lb_winner_target(num_rounds, lb_round, lb_pos):
    """LB-Ziel (lb_round, lb_pos, slot), in das der SIEGER eines LB-Fights vorrückt —
    aus derselben Topologie wie `_advance_lb_winner`/`_apply_ko_graph_result`. None am
    Endkampf/Bronze (kein Folge-LB-Slot)."""
    if num_rounds == 5:
        for (_phase, rnd, pos), slot, kind in _KO32_CONSUMERS.get(("lb", lb_round, lb_pos), []):
            if kind == "W":
                return (rnd, pos, slot)
        return None
    struct = _LB_STRUCTURE.get(num_rounds)
    if struct is None:
        return None
    adv = struct["lb_advance"].get(lb_round)
    if adv is None:
        return None
    nxt_round, nxt_pos, slot = adv(lb_pos)
    return (nxt_round, nxt_pos, slot)


def _resolve_lb_byes(session):
    """LB-Gegenstueck zu `_resolve_pending_byes`: ein WB-Freilos hat keinen Verlierer,
    also bleibt sein LB-Ziel-Slot dauerhaft tot und der LB-Kampf nie startbar.

    Loest tote LB-Slots per Fixpunkt auf (Topologie aus `_LB_STRUCTURE` bzw.
    `_KO32_CONSUMERS`, KEINE neue Quelle):
      * GENAU EIN toter Slot + gefuellter Live-Slot ⇒ Walkover-Freilos (Form wie
        WB-Byes: p1==p2=Kaempfer, status='bye', winner_id gesetzt) + Vorruecken via
        `_advance_lb_winner` (8/16) bzw. `_apply_ko_graph_result` (32er).
      * ZWEI tote Slots ⇒ Kampf selbst tot (status='bye', winner_id=NULL, Slots NULL),
        vererbt die Leere an seinen Folge-LB-Slot (Kaskade).
      * EIN toter Slot, Live-Slot noch leer ⇒ bleibt pending (loest auf, sobald der
        reale Geschwister-WB-Verlierer gedroppt ist).
    Idempotent (der dead-Set wird je Aufruf neu aus den WB-Byes abgeleitet). Returns
    die Anzahl neu aufgeloester LB-Fights."""
    from src.database import BracketModel as _BracketModel
    from src.database import FightModel as _FightModel

    ko_ids = [b.id for b in session.query(_BracketModel).filter(
        _BracketModel.bracket_type == "ko").all()]
    resolved = 0
    for bid in ko_ids:
        num_rounds = _wb_num_rounds(session, bid)
        if num_rounds == 0 or (num_rounds != 5 and _LB_STRUCTURE.get(num_rounds) is None):
            continue   # keine verdrahtete LB-Topologie (z.B. nicht-codifizierte Groesse)

        lb_fights = {(f.round, f.pos_in_round): f for f in session.query(_FightModel).filter(
            _FightModel.bracket_id == bid, _FightModel.bracket_phase == "lb").all()}
        if not lb_fights:
            continue

        # Tote Slots aus den WB-Freilosen seeden.
        dead = set()   # (lb_round, lb_pos, slot)
        wb_byes = session.query(_FightModel).filter(
            _FightModel.bracket_id == bid, _FightModel.bracket_phase == "wb",
            _FightModel.status == "bye").all()
        for bye in wb_byes:
            if bye.round is None or bye.pos_in_round is None or _loser_id(bye) is not None:
                continue   # nur echte Freilose (kein realer Verlierer)
            tgt = _wb_loser_target(num_rounds, bye.round, bye.pos_in_round)
            if tgt is not None:
                dead.add(tgt)

        done = set()
        changed = True
        while changed:
            changed = False
            for (rnd, pos), f in sorted(lb_fights.items()):
                if (rnd, pos) in done:
                    continue
                s1_dead = (rnd, pos, 1) in dead
                s2_dead = (rnd, pos, 2) in dead
                n_dead = s1_dead + s2_dead
                if n_dead == 2:
                    # Voll tot: vererbt die Leere an den Folge-LB-Slot.
                    tgt = _lb_winner_target(num_rounds, rnd, pos)
                    if tgt is not None:
                        dead.add(tgt)
                    if not (f.status == "bye" and f.winner_id is None):
                        f.participant1_id = None
                        f.participant2_id = None
                        f.status = "bye"
                        f.winner_id = None
                        resolved += 1
                    done.add((rnd, pos))
                    changed = True
                elif n_dead == 1:
                    live_id = f.participant2_id if s1_dead else f.participant1_id
                    if live_id is not None:
                        if not (f.status == "bye" and f.winner_id == live_id):
                            f.participant1_id = live_id
                            f.participant2_id = live_id
                            f.status = "bye"
                            f.winner_id = live_id
                            session.flush()
                            if num_rounds == 5:
                                _apply_ko_graph_result(session, f)
                            else:
                                _advance_lb_winner(session, f)
                            resolved += 1
                        done.add((rnd, pos))
                        changed = True
                    # Live-Slot noch leer ⇒ pending lassen (kein done/changed).
        session.commit()
    return resolved


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


def _best_of_three_decided(session, bracket_id):
    """True wenn ein Zweier-Pool (Best-of-three) bereits entschieden ist.

    Ein Zweier-Pool besteht aus genau zwei Teilnehmern, die dreimal gegeneinander
    kaempfen (siehe edv pool_renderer._generate_fight_schedule(2)). Sobald einer
    zwei Siege hat, ist der dritte Kampf bedeutungslos — das Bracket darf ohne ihn
    abgeschlossen werden.

    Returns: (decided: bool, open_fights: list[FightModel]) — die noch offenen
    Pool-Fights, damit der Aufrufer sie als 'bye' schliessen kann.
    """
    from src.database import FightModel as _FightModel

    fights = session.query(_FightModel).filter(
        _FightModel.bracket_id == bracket_id,
        _FightModel.bracket_phase == "pool",
    ).all()

    gp_ids: set[int] = set()
    wins: dict[int, int] = {}
    open_fights = []
    for f in fights:
        if f.participant1_id is not None:
            gp_ids.add(f.participant1_id)
        if f.participant2_id is not None:
            gp_ids.add(f.participant2_id)
        if f.status in ("finished", "bye"):
            if f.winner_id is not None:
                wins[f.winner_id] = wins.get(f.winner_id, 0) + 1
        else:
            open_fights.append(f)

    decided = len(gp_ids) == 2 and max(wins.values(), default=0) >= 2
    return decided, open_fights


def _finalize_pool_bracket_if_complete(session, fight):
    """Welle 2B.1 (Single-Pool) + 2B.2 (Doppelpool-Trigger):
    Wenn alle Pool-Fights eines Brackets durch sind:
      - 'pools' (Single): Standings persistieren + bracket.status='completed'.
      - 'double' (Doppelpool): KO-Stage anlegen (eager); Bracket bleibt pending.
    Idempotent.

    Returns: dict mit Event-Daten oder None.
    """
    from src.database import BracketModel
    from src.database import FightModel as _FightModel

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
        # Best-of-three (Zweier-Pool): bei 2-0/2-1 ist der dritte Kampf moot.
        # Frueh abschliessen und den toten Rest-Kampf als 'bye' schliessen, damit
        # er nicht offen haengen bleibt. Doppelpools spielen immer alle Fights.
        decided, leftover = (False, [])
        if bracket.bracket_type != "double":
            decided, leftover = _best_of_three_decided(session, fight.bracket_id)
        if not decided:
            return None
        for f in leftover:
            f.status = "bye"   # winner_id bleibt None -> zaehlt nicht als Sieg

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
    # Pool mit MEHR ALS DREI Teilnehmern => zwei dritte Plaetze (Rang 3 + Rang 4,
    # DJB-Konvention). Genau-3-Pool => nur ein Dritter; 2er-Pool => kein Dritter.
    bracket.third_place_2 = standings[3] if len(standings) > 3 else None
    bracket.status = "completed"
    session.commit()

    return {
        "event": "BRACKET_COMPLETED",
        "bracket_id": bracket.id,
        "placements": {
            "first": bracket.first_place,
            "second": bracket.second_place,
            "third_1": bracket.third_place_1,
            "third_2": bracket.third_place_2,
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
    from src.database import BracketModel
    from src.database import FightModel as _FightModel

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


# ── Doppel-KO Trostrunde (Loser-Bracket) ──────────────────────────────────────
# Modifizierte Judo-Trostrunde, halbseitig, 2 Bronze (Decision 2026-06-01, siehe
# WSP/CLAUDE.md). JF besitzt das LB live. Struktur GEKREUZT aus ko_8/16.xls
# dekodiert (8er crosst am Bronze, 16er am QF-Eintritt). 32er noch offen.
#
# _LB_STRUCTURE[num_rounds] beschreibt das LB pro Draw-Größe (num_rounds=log2(n)):
#   wb_drop[wb_round](pos)   → (lb_round, lb_pos, slot)  wohin der WB-VERLIERER droppt
#   lb_advance[lb_round](pos)→ (lb_round, lb_pos, slot)  wohin der LB-SIEGER vorrückt
#   bronze_round = letzte lb-Runde (2 Bronze-Matches → third_place_1/2)
# slot: 1=participant1_id, 2=participant2_id. Explizite Tabellen pro Größe
# (KEINE Allgemein-Formel — die Cross-Tiefe ist je Größe irregulär).

_LB_STRUCTURE = {
    3: {  # 8er: WB R0(4)/SF(2)/Finale(1) → lb r0(2), lb r1=Bronze(2)
        "bronze_round": 1,
        "wb_drop": {
            0: lambda p: (0, p // 2, 1 if p % 2 == 0 else 2),  # R0-Verlierer → lb r0
            1: lambda p: (1, 1 - p, 2),                        # SF-Verlierer → Bronze, CROSS
        },
        "lb_advance": {
            0: lambda p: (1, p, 1),                            # lb r0-Sieger → Bronze, slot1
        },
    },
    4: {  # 16er: WB R0(8)/QF(4)/SF(2)/Finale(1) → lb r0(4),r1(4),r2(2),r3=Bronze(2)
        "bronze_round": 3,
        "wb_drop": {
            0: lambda p: (0, p // 2, 1 if p % 2 == 0 else 2),  # R0-Verlierer → lb r0
            1: lambda p: (1, p ^ 2, 2),                        # QF-Verlierer → lb r1, CROSS (Halbtausch)
            2: lambda p: (3, p, 2),                            # SF-Verlierer → Bronze, kein Cross
        },
        "lb_advance": {
            0: lambda p: (1, p, 1),                            # lb r0-Sieger → lb r1, slot1
            1: lambda p: (2, p // 2, 1 if p % 2 == 0 else 2),  # lb r1-Sieger → lb r2 (Merge)
            2: lambda p: (3, p, 1),                            # lb r2-Sieger → Bronze, slot1
        },
    },
    # 5: 32er — bewusst NICHT verdrahtet (ko_32.xls braucht Merlins Bogen-Abgleich).
}

# Anzahl lb-Fights je lb-Runde — spiegelt _LB_STRUCTURE (für die Eager-Anlage des
# kompletten Baums). 8er: lb r0=2, r1(Bronze)=2. 16er: lb r0..r3(Bronze)=4,4,2,2.
# Nicht gelistete Größen (32er) ⇒ KEIN LB eager anlegen (nur WB-Baum).
_LB_ROUND_SIZES = {
    3: (2, 2),
    4: (4, 4, 2, 2),
}

# ── 32er Doppel-KO: deklarativer Feeder-Graph (eingefrorene Kopie CANONICAL_32) ─
# Der 32er hat eine ABWEICHENDE Medaillen-Topologie (partielles Double-Elimination
# mit Repechage-Rückkreuzung): die zwei Trostrunde-Champions falten in eine
# Medaillen-Round zurück (Sieger HF × Sieger Trostrunde), das Finale zieht aus
# DEREN Siegern, und Bronze sind die Medaillen-Round-VERLIERER (nicht LB-
# Finalsieger). Das passt NICHT in die wb_drop/lb_advance/bronze_round-Abstraktion
# von _LB_STRUCTURE (die 8er/16er bleiben dort) — deshalb fahren wir den 32er über
# diesen Graphen. Quelle/Oracle: edv/tests/test_ko_form_order.py `CANONICAL_32`
# (gegen ko_32.xls geprüft; Decision 2026-06-02-2, WSP/CLAUDE.md). Feeder =
# ('los', n) Seed | ('W', kf) Sieger aus Kampf kf | ('L', kf) Verlierer aus kf.
# top → participant1 (slot 1), bottom → participant2 (slot 2).
_CANONICAL_32 = {
    1: (("los", 1), ("los", 17)), 2: (("los", 9), ("los", 25)),
    3: (("los", 5), ("los", 21)), 4: (("los", 13), ("los", 29)),
    5: (("los", 3), ("los", 19)), 6: (("los", 11), ("los", 27)),
    7: (("los", 7), ("los", 23)), 8: (("los", 15), ("los", 31)),
    9: (("los", 2), ("los", 18)), 10: (("los", 10), ("los", 26)),
    11: (("los", 6), ("los", 22)), 12: (("los", 14), ("los", 30)),
    13: (("los", 4), ("los", 20)), 14: (("los", 12), ("los", 28)),
    15: (("los", 8), ("los", 24)), 16: (("los", 16), ("los", 32)),
    17: (("W", 1), ("W", 2)), 18: (("W", 3), ("W", 4)),
    19: (("W", 5), ("W", 6)), 20: (("W", 7), ("W", 8)),
    21: (("W", 9), ("W", 10)), 22: (("W", 11), ("W", 12)),
    23: (("W", 13), ("W", 14)), 24: (("W", 15), ("W", 16)),
    25: (("L", 13), ("L", 14)), 26: (("L", 15), ("L", 16)),   # LB r0: R0-Verlierer
    27: (("L", 9), ("L", 10)), 28: (("L", 11), ("L", 12)),
    29: (("L", 5), ("L", 6)), 30: (("L", 7), ("L", 8)),
    31: (("L", 1), ("L", 2)), 32: (("L", 3), ("L", 4)),
    33: (("W", 17), ("W", 18)), 34: (("W", 19), ("W", 20)),   # WB Viertelfinale
    35: (("W", 21), ("W", 22)), 36: (("W", 23), ("W", 24)),
    37: (("W", 25), ("L", 21)), 38: (("W", 26), ("L", 22)),   # LB r1: R1-Verlierer, Cross
    39: (("W", 27), ("L", 23)), 40: (("W", 28), ("L", 24)),
    41: (("W", 29), ("L", 17)), 42: (("W", 30), ("L", 18)),
    43: (("W", 31), ("L", 19)), 44: (("W", 32), ("L", 20)),
    45: (("W", 33), ("W", 34)), 46: (("W", 35), ("W", 36)),   # WB Halbfinale
    47: (("W", 37), ("W", 38)), 48: (("W", 39), ("W", 40)),   # LB r2: Merge
    49: (("W", 41), ("W", 42)), 50: (("W", 43), ("W", 44)),
    51: (("W", 47), ("L", 33)), 52: (("W", 48), ("L", 34)),   # LB r3: VF-Verlierer, Cross
    53: (("W", 49), ("L", 35)), 54: (("W", 50), ("L", 36)),
    55: (("W", 51), ("W", 52)), 56: (("W", 53), ("W", 54)),   # LB r4: Merge
    57: (("W", 55), ("L", 46)), 58: (("L", 45), ("W", 56)),   # LB-Halbfinale: HF-Verlierer, Cross
    59: (("W", 45), ("W", 57)), 60: (("W", 46), ("W", 58)),   # Medaillen-Round: HF-Sieger × TR-Champ
    61: (("W", 59), ("W", 60)),                               # Finale
}

# Kampffolge-Nr → (phase, round, pos). WB rundet 0–3 (Finale entfällt — der
# 32er-„Endkampf" ist die Medaillen-Round im LB), LB rundet 0–7 (r6 = Medaillen-
# Round Kf59/60, r7 = Finale Kf61). WB-pos folgt dem Binärbaum (pos//2-Merge), so
# dass WB-Sieger-Propagation + Byes mit _propagate_winner/_resolve_pending_byes
# konsistent bleiben.
_KF32_RANGES = (
    (1, 16, "wb", 0), (17, 24, "wb", 1), (33, 36, "wb", 2), (45, 46, "wb", 3),
    (25, 32, "lb", 0), (37, 44, "lb", 1), (47, 50, "lb", 2), (51, 54, "lb", 3),
    (55, 56, "lb", 4), (57, 58, "lb", 5), (59, 60, "lb", 6), (61, 61, "lb", 7),
)


def _kf_to_node_32(kf):
    for lo, hi, phase, rnd in _KF32_RANGES:
        if lo <= kf <= hi:
            return (phase, rnd, kf - lo)
    raise KeyError(kf)


# Vorwärts-Konsumenten: src-Node → [(dst-Node, slot, kind)] mit kind 'W'|'L'.
# Plus der Knoten-Satz (für die Eager-Anlage) und die Terminal-Knoten (Finale +
# die zwei Medaillen-Round-Fights, deren VERLIERER Bronze sind). Einmal beim
# Import abgeleitet aus _CANONICAL_32.
_KO32_NODES = set()
_KO32_CONSUMERS = {}
for _kf, (_fa, _fb) in _CANONICAL_32.items():
    _dst = _kf_to_node_32(_kf)
    _KO32_NODES.add(_dst)
    for _slot, _feeder in ((1, _fa), (2, _fb)):
        if _feeder is None:
            continue
        _kind, _ref = _feeder
        if _kind == "los":
            continue
        _src = _kf_to_node_32(_ref)
        _KO32_NODES.add(_src)
        _KO32_CONSUMERS.setdefault(_src, []).append((_dst, _slot, _kind))
_KO32_FINAL_NODE = _kf_to_node_32(61)          # ('lb', 7, 0)
_KO32_MEDAL_NODES = (_kf_to_node_32(59), _kf_to_node_32(60))  # Verlierer = Bronze
del _kf, _fa, _fb, _dst, _slot, _feeder, _kind, _ref, _src


def _next_fight_number(session, bracket_id):
    from sqlalchemy import func

    from src.database import FightModel as _FightModel
    mx = session.query(func.max(_FightModel.fight_number)).filter(
        _FightModel.bracket_id == bracket_id).scalar()
    return (mx or 0) + 1


def _wb_num_rounds(session, bracket_id):
    """Anzahl WB-Runden aus den Round-0-Fights (size = 2*r0, rounds = log2(size)).
    0 wenn keine 'wb' round-0-Fights existieren (z.B. Doppelpool-Brackets)."""
    from src.database import FightModel as _FightModel
    r0 = session.query(_FightModel).filter(
        _FightModel.bracket_id == bracket_id,
        _FightModel.bracket_phase == "wb",
        _FightModel.round == 0,
    ).count()
    return r0.bit_length() if r0 >= 1 else 0   # 4→3 (8er), 8→4 (16er), 16→5 (32er)


def _loser_id(fight):
    """Verlierer-GP-ID; None bei Bye/Phantom (p1==p2 oder fehlender Teilnehmer)."""
    if fight.winner_id is None:
        return None
    if fight.participant1_id is None or fight.participant2_id is None:
        return None
    if fight.participant1_id == fight.participant2_id:
        return None
    return (fight.participant1_id if fight.winner_id == fight.participant2_id
            else fight.participant2_id)


def _find_or_create_lb_fight(session, bracket_id, lb_round, lb_pos, table_id):
    from src.database import FightModel as _FightModel
    f = session.query(_FightModel).filter(
        _FightModel.bracket_id == bracket_id,
        _FightModel.bracket_phase == "lb",
        _FightModel.round == lb_round,
        _FightModel.pos_in_round == lb_pos,
    ).first()
    if f is None:
        f = _FightModel(
            bracket_id=bracket_id, bracket_phase="lb",
            round=lb_round, pos_in_round=lb_pos, status="pending",
            participant1_id=None, participant2_id=None,
            fight_number=_next_fight_number(session, bracket_id),
            table_id=table_id,
        )
        session.add(f)
        session.flush()
    return f


def _find_or_create_ko_fight(session, bracket_id, phase, rnd, pos, table_id):
    """Phase-generisches find-or-create (wb ODER lb), für die 32er-Graph-Engine."""
    from src.database import FightModel as _FightModel
    f = session.query(_FightModel).filter(
        _FightModel.bracket_id == bracket_id,
        _FightModel.bracket_phase == phase,
        _FightModel.round == rnd,
        _FightModel.pos_in_round == pos,
    ).first()
    if f is None:
        f = _FightModel(
            bracket_id=bracket_id, bracket_phase=phase,
            round=rnd, pos_in_round=pos, status="pending",
            participant1_id=None, participant2_id=None,
            fight_number=_next_fight_number(session, bracket_id),
            table_id=table_id,
        )
        session.add(f)
        session.flush()
    return f


def _create_ko_fight_if_missing(session, bracket_id, phase, rnd, pos, table_id):
    """Legt einen leeren TBD-Fight (beide participant NULL) an, falls (bracket, phase,
    round, pos) noch nicht existiert. Returns das neue Fight oder None (existierte schon).
    Fuer die Eager-Materialisierung des kompletten KO/Doppel-KO-Baums."""
    from src.database import FightModel as _FightModel
    exists = session.query(_FightModel.id).filter(
        _FightModel.bracket_id == bracket_id,
        _FightModel.bracket_phase == phase,
        _FightModel.round == rnd,
        _FightModel.pos_in_round == pos,
    ).first()
    if exists is not None:
        return None
    f = _FightModel(
        bracket_id=bracket_id, bracket_phase=phase, round=rnd, pos_in_round=pos,
        participant1_id=None, participant2_id=None, status="pending",
        fight_number=_next_fight_number(session, bracket_id), table_id=table_id,
    )
    session.add(f)
    session.flush()
    return f


def _ensure_ko_tree_materialized(session):
    """Legt den kompletten KO/Doppel-KO-Baum (alle WB-Runden 1+ sowie das gesamte LB
    inkl. Bronze) vorab als TBD-Zeilen an, damit der Turnierbaum von Anfang an die
    ganze Struktur zeigt. Topologie-Quelle: WB-Binaerbaum (Runde r hat 2^(R-1-r)
    Fights) + _LB_ROUND_SIZES/_LB_STRUCTURE. Idempotent (find-or-create je
    (bracket,phase,round,pos)); nur bracket_type='ko'. Nicht verdrahtete Groessen
    (32er, kein _LB_ROUND_SIZES-Eintrag) ⇒ nur WB-Baum, KEIN halbes LB (graceful).
    Returns die Anzahl neu angelegter Fights."""
    from src.database import BracketModel as _BracketModel
    from src.database import FightModel as _FightModel

    ko_ids = [b.id for b in session.query(_BracketModel).filter(
        _BracketModel.bracket_type == "ko").all()]
    created = 0
    for bid in ko_ids:
        num_rounds = _wb_num_rounds(session, bid)
        if num_rounds == 0:
            continue   # keine wb-Runde-0 (z.B. Doppelpool) → nicht zustaendig
        # 32er: expliziter Knoten-Satz aus dem Feeder-Graphen (WB nur bis HF, das
        # gesamte LB inkl. Medaillen-Round). Sonst: WB-Binärbaum + _LB_ROUND_SIZES.
        if num_rounds == 5:
            nodes = _KO32_NODES
            expected = len(_KO32_NODES)
        else:
            nodes = None
            expected = (2 ** num_rounds - 1) + sum(_LB_ROUND_SIZES.get(num_rounds, ()))
        have = session.query(_FightModel).filter(_FightModel.bracket_id == bid).count()
        if have >= expected:
            continue   # bereits vollstaendig materialisiert → kein Re-Write

        # table_id von wb Runde 0 erben (eine ko-Kategorie laeuft auf einer Matte);
        # _propagate_winner zieht table_id auf existierenden Zeilen NICHT nach.
        r0 = session.query(_FightModel).filter(
            _FightModel.bracket_id == bid, _FightModel.bracket_phase == "wb",
            _FightModel.round == 0).first()
        table_id = r0.table_id if r0 else None

        if nodes is not None:
            # 32er: jeden Graph-Knoten ausser wb Runde 0 (kommt von edv) anlegen.
            for phase, rnd, pos in sorted(nodes):
                if phase == "wb" and rnd == 0:
                    continue
                if _create_ko_fight_if_missing(session, bid, phase, rnd, pos, table_id):
                    created += 1
            continue
        # WB-Runden 1..R-1 (Runde 0 kommt von edv).
        for rnd in range(1, num_rounds):
            for pos in range(2 ** (num_rounds - 1 - rnd)):
                if _create_ko_fight_if_missing(session, bid, "wb", rnd, pos, table_id):
                    created += 1
        # LB-Runden gemaess _LB_ROUND_SIZES (leer ⇒ nichts).
        for lb_round, size in enumerate(_LB_ROUND_SIZES.get(num_rounds, ())):
            for pos in range(size):
                if _create_ko_fight_if_missing(session, bid, "lb", lb_round, pos, table_id):
                    created += 1
    if created:
        session.commit()
    return created


def _compute_slot_sources(session, fight_lookup):
    """Herkunft je TBD-Slot: Map (bracket_id, phase, round, pos) → {1: src, 2: src}
    mit src = {"kind": "winner"|"loser", "fightId": <Quell-Fight-Id>}.

    Abgeleitet aus DENSELBEN Forward-Kanten wie Propagation/Drop (KEINE eigene
    Topologie): WB-Binaerbaum (slot p1 ← Sieger WB(r-1,2p), slot p2 ← Sieger
    WB(r-1,2p+1)) + _LB_STRUCTURE-Kanten (wb_drop ⇒ "Verlierer aus WB", lb_advance
    ⇒ "Sieger aus LB") + Doppelpool-Finale (← HF-Sieger). 32er (struct None) ⇒
    nur WB-Quellen. `fight_lookup`: {(bracket, phase, round, pos): fight_id}."""
    from src.database import BracketModel as _BracketModel

    sources = {}

    def set_src(bid, phase, rnd, pos, slot, kind, src_coord):
        src_id = fight_lookup.get(src_coord)
        if src_id is None:
            return
        sources.setdefault((bid, phase, rnd, pos), {})[slot] = {"kind": kind, "fightId": src_id}

    ko_ids = [b.id for b in session.query(_BracketModel).filter(
        _BracketModel.bracket_type == "ko").all()]
    for bid in ko_ids:
        num_rounds = _wb_num_rounds(session, bid)
        if num_rounds == 0:
            continue
        if num_rounds == 5:
            # 32er: expliziter Feeder-Graph (inkl. WB + Medaillen-Round-Rückkreuzung).
            # Dieselbe Quelle wie die Live-Propagation (_KO32_CONSUMERS), nichts dupliziert.
            for src_node, consumers in _KO32_CONSUMERS.items():
                for dst_node, slot, kind in consumers:
                    set_src(bid, dst_node[0], dst_node[1], dst_node[2], slot,
                            "winner" if kind == "W" else "loser",
                            (bid, src_node[0], src_node[1], src_node[2]))
            continue
        # WB-Baum: jeder Slot kommt vom Sieger des darunterliegenden Fights.
        for rnd in range(1, num_rounds):
            for pos in range(2 ** (num_rounds - 1 - rnd)):
                set_src(bid, "wb", rnd, pos, 1, "winner", (bid, "wb", rnd - 1, 2 * pos))
                set_src(bid, "wb", rnd, pos, 2, "winner", (bid, "wb", rnd - 1, 2 * pos + 1))
        struct = _LB_STRUCTURE.get(num_rounds)
        if struct is None:
            continue   # nicht verdrahtete Groesse: nur WB-Quellen
        # WB-Verlierer → LB-Slot ("Verlierer aus #WB").
        for wb_round, fn in struct["wb_drop"].items():
            for pos in range(2 ** (num_rounds - 1 - wb_round)):
                lb_round, lb_pos, slot = fn(pos)
                set_src(bid, "lb", lb_round, lb_pos, slot, "loser", (bid, "wb", wb_round, pos))
        # LB-Sieger → LB-Slot ("Sieger aus #LB").
        lb_sizes = _LB_ROUND_SIZES.get(num_rounds, ())
        for lb_round_src, fn in struct["lb_advance"].items():
            n = lb_sizes[lb_round_src] if lb_round_src < len(lb_sizes) else 0
            for pos in range(n):
                lb_round, lb_pos, slot = fn(pos)
                set_src(bid, "lb", lb_round, lb_pos, slot, "winner", (bid, "lb", lb_round_src, pos))

    # Doppelpool-KO-Stage (_initialize_double_pool_ko_stage): HF1 (wb r1 p0) und
    # HF2 (wb r1 p1) haben reale Pool-Platzierungen (kein TBD), das Finale (wb r2
    # p0) ist TBD und kommt von den beiden HF-Siegern. Dieselbe Forward-Kante wie
    # _propagate_winner (Binaerbaum) — keine eigene Topologie. So zeigt der Baum
    # "Sieger aus #HF1/#HF2" statt "TBD". (Die 3. Plaetze sind die HF-Verlierer
    # direkt, kein Bronze-Match → nichts zu beschriften.)
    double_ids = [b.id for b in session.query(_BracketModel).filter(
        _BracketModel.bracket_type == "double").all()]
    for bid in double_ids:
        set_src(bid, "wb", 2, 0, 1, "winner", (bid, "wb", 1, 0))
        set_src(bid, "wb", 2, 0, 2, "winner", (bid, "wb", 1, 1))
    return sources


def _drop_loser_to_lb(session, wb_fight):
    """Droppt den WB-Verlierer in den passenden lb-Slot (find-or-create) gemaess
    _LB_STRUCTURE. Nur fuer bracket_type='ko'; Aufrufer gated. Returns das lb-Fight
    oder None (Bye, WB-Finale, oder nicht-verdrahtete Groesse wie 32er)."""
    import logging
    if wb_fight.bracket_phase != "wb" or wb_fight.round is None or wb_fight.pos_in_round is None:
        return None
    loser = _loser_id(wb_fight)
    if loser is None:
        return None

    num_rounds = _wb_num_rounds(session, wb_fight.bracket_id)
    struct = _LB_STRUCTURE.get(num_rounds)
    if struct is None:
        logging.getLogger("uvicorn.error").warning(
            "Trostrunde: LB-Seeding fuer %d WB-Runden (bracket %s) noch nicht verdrahtet "
            "(z.B. 32er) — Verlierer aus fight #%s nicht gedroppt.",
            num_rounds, wb_fight.bracket_id, wb_fight.id)
        return None

    drop = struct["wb_drop"].get(wb_fight.round)
    if drop is None:
        return None   # WB-Finale o.ae. — kein Loser-Drop
    lb_round, lb_pos, slot = drop(wb_fight.pos_in_round)
    lb = _find_or_create_lb_fight(session, wb_fight.bracket_id, lb_round, lb_pos, wb_fight.table_id)
    setattr(lb, "participant1_id" if slot == 1 else "participant2_id", loser)
    session.commit()
    session.refresh(lb)
    return lb


def _advance_lb_winner(session, lb_fight):
    """Within-LB-Vorrücken gemaess _LB_STRUCTURE (Mix aus pos-erhaltend bei Treffen
    auf einen frisch gedroppten WB-Verlierer und pos//2-Merge bei Paarung zweier
    lb-Sieger). Returns das Folge-Fight oder None, wenn lb_fight schon das
    Bronze-Match ist (→ Aufrufer finalisiert)."""
    if lb_fight.bracket_phase != "lb" or lb_fight.winner_id is None:
        return None
    if lb_fight.round is None or lb_fight.pos_in_round is None:
        return None
    struct = _LB_STRUCTURE.get(_wb_num_rounds(session, lb_fight.bracket_id))
    if struct is None or lb_fight.round >= struct["bronze_round"]:
        return None   # bereits Bronze-Match (oder nicht verdrahtet)
    adv = struct["lb_advance"].get(lb_fight.round)
    if adv is None:
        return None
    nxt_round, nxt_pos, slot = adv(lb_fight.pos_in_round)
    nxt = _find_or_create_lb_fight(session, lb_fight.bracket_id, nxt_round, nxt_pos, lb_fight.table_id)
    setattr(nxt, "participant1_id" if slot == 1 else "participant2_id", lb_fight.winner_id)
    session.commit()
    session.refresh(nxt)
    return nxt


def _apply_ko_graph_result(session, fight):
    """32er-Doppel-KO: schiebt Sieger UND Verlierer eines beendeten Fights gemäß dem
    eingefrorenen Feeder-Graphen (_KO32_CONSUMERS) in ihre Folge-Slots (find-or-
    create). Anders als 8er/16er propagiert hier EIN Fight oft beide Resultate
    (z.B. HF: Sieger → Medaillen-Round, Verlierer → LB-Halbfinale). Returns die
    Liste der berührten Folge-Fights (für Broadcast)."""
    if fight.bracket_phase not in ("wb", "lb") or fight.round is None or fight.pos_in_round is None:
        return []
    if _wb_num_rounds(session, fight.bracket_id) != 5:
        return []   # nur 32er — der Feeder-Graph gilt sonst nicht (Knoten-Kollision)
    edges = _KO32_CONSUMERS.get((fight.bracket_phase, fight.round, fight.pos_in_round))
    if not edges:
        return []
    winner = fight.winner_id
    loser = _loser_id(fight)
    touched = []
    for (phase, rnd, pos), slot, kind in edges:
        val = winner if kind == "W" else loser
        if val is None:
            continue   # Bye/Phantom hat keinen Verlierer → nichts zu schieben
        nxt = _find_or_create_ko_fight(session, fight.bracket_id, phase, rnd, pos, fight.table_id)
        setattr(nxt, "participant1_id" if slot == 1 else "participant2_id", val)
        touched.append(nxt)
    if touched:
        session.commit()
        for t in touched:
            session.refresh(t)
    return touched


def _finalize_32er_bracket(session, bracket):
    """32er-Abschluss: 1./2. aus dem Finale (lb r7), 3./3. aus den VERLIERERN der
    zwei Medaillen-Round-Fights (lb r6). Die zwei 5. Plätze (Verlierer der LB-
    Halbfinals) werden bewusst NICHT persistiert — es gibt kein fifth_place-Feld
    (Decision 2026-06-02-2). Idempotent."""
    from src.database import FightModel as _FightModel

    def node_fight(node):
        phase, rnd, pos = node
        return session.query(_FightModel).filter(
            _FightModel.bracket_id == bracket.id, _FightModel.bracket_phase == phase,
            _FightModel.round == rnd, _FightModel.pos_in_round == pos,
        ).first()

    final = node_fight(_KO32_FINAL_NODE)
    medals = [node_fight(n) for n in _KO32_MEDAL_NODES]
    if final is None or final.winner_id is None:
        return None
    if any(m is None for m in medals):
        return None
    # Ein per Kaskade voll-toter Medaillen-Kampf (status='bye', winner NULL) blockiert
    # nicht → sein _loser_id ist None ⇒ die betroffene Bronze bleibt None.
    if any(m.winner_id is None and m.status != "bye" for m in medals):
        return None

    bracket.first_place = final.winner_id
    bracket.second_place = (final.participant1_id if final.winner_id == final.participant2_id
                            else final.participant2_id)
    bracket.third_place_1 = _loser_id(medals[0])
    bracket.third_place_2 = _loser_id(medals[1])
    bracket.status = "completed"
    session.commit()
    return {
        "bracket_id": bracket.id,
        "placements": {
            "first": bracket.first_place, "second": bracket.second_place,
            "third_1": bracket.third_place_1, "third_2": bracket.third_place_2,
        },
    }


def _finalize_doppel_ko_bracket(session, fight):
    """Standalone Doppel-KO: 1./2. aus WB-Finale, 3./3. aus den zwei Bronze-Matches.
    status='completed' erst wenn Finale + beide Bronze durch sind. Idempotent.
    Der 32er hat eine abweichende Medaillen-Topologie → _finalize_32er_bracket."""
    from src.database import BracketModel
    from src.database import FightModel as _FightModel

    bracket = session.query(BracketModel).filter(BracketModel.id == fight.bracket_id).first()
    if bracket is None or bracket.bracket_type != "ko" or bracket.status == "completed":
        return None
    num_rounds = _wb_num_rounds(session, bracket.id)
    if num_rounds == 5:
        return _finalize_32er_bracket(session, bracket)
    struct = _LB_STRUCTURE.get(num_rounds)
    if num_rounds < 2 or struct is None:
        return None
    final_round, bronze_round = num_rounds - 1, struct["bronze_round"]

    wb_final = session.query(_FightModel).filter(
        _FightModel.bracket_id == bracket.id, _FightModel.bracket_phase == "wb",
        _FightModel.round == final_round, _FightModel.pos_in_round == 0,
    ).first()
    bronzes = session.query(_FightModel).filter(
        _FightModel.bracket_id == bracket.id, _FightModel.bracket_phase == "lb",
        _FightModel.round == bronze_round,
    ).order_by(_FightModel.pos_in_round).all()

    if wb_final is None or wb_final.winner_id is None:
        return None
    # Ein per Kaskade voll-toter Bronze-Kampf (status='bye', winner NULL = kein
    # Kämpfer) blockiert den Abschluss NICHT → er liefert third_place=None.
    unresolved = [b for b in bronzes if b.winner_id is None and b.status != "bye"]
    if len(bronzes) < 2 or unresolved:
        return None

    bracket.first_place = wb_final.winner_id
    bracket.second_place = (wb_final.participant1_id if wb_final.winner_id == wb_final.participant2_id
                            else wb_final.participant2_id)
    bracket.third_place_1 = bronzes[0].winner_id
    bracket.third_place_2 = bronzes[1].winner_id
    bracket.status = "completed"
    session.commit()
    return {
        "bracket_id": bracket.id,
        "placements": {
            "first": bracket.first_place, "second": bracket.second_place,
            "third_1": bracket.third_place_1, "third_2": bracket.third_place_2,
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
    from src.database import BracketModel
    from src.database import FightModel as _FightModel
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
        # Direkt nach dem Import: vollen Baum materialisieren, dann Freilose aufloesen
        # (beide idempotent), damit Struktur + Bye-Folgekaempfe sofort bereitstehen.
        _ensure_ko_tree_materialized(session)
        _resolve_pending_byes(session)
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

        # optional pool label, top-level sibling of fighter1/fighter2 (empty for non-pool fights)
        payload = {"fighter1": fighter_json(f1), "fighter2": fighter_json(f2), "pool": _pool_label(fight)}

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

    global last_pushed_match_id

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

    # Reopening the currently-pushed match makes its assignment stale — drop the pointer
    # so a pending Ipponboard callback for it is rejected rather than mis-applied.
    if last_pushed_match_id == match_id:
        last_pushed_match_id = None
        await manager.broadcast({"type": "CURRENT_MATCH_SET", "matchId": None})

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
        appliedId = fight.id

    await manager.broadcast({"type": "SCORE_SYNC", "matchId": appliedId, "match": match_dict})

    # Clear the pushed-match pointer so a late/duplicate Ipponboard callback can't be
    # applied to whatever match got pushed next. Next callback hits the "No match pushed
    # yet" guard until the operator pushes again.
    last_pushed_match_id = None
    await manager.broadcast({"type": "CURRENT_MATCH_SET", "matchId": None})

    return {
        "status": "ok",
        "match_id": appliedId,
        "winner": winner,
        "winner_name": match_dict["winnerName"],
    }
