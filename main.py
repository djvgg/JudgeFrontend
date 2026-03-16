import asyncio as _asyncio
import json as _json
import logging as _logging
import math as _math
import os as _os
import sys as _sys
from contextlib import asynccontextmanager, suppress
from datetime import datetime

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.bracket_manager import BracketManager
from backend.database import (
    BracketModel,
    FightModel,
    GroupModel,
    GroupParticipantModel,
    ParticipantModel,
    SessionLocal,
    init_db,
)

# --- LOGGING SETUP ---
_logging.basicConfig(
    level=_logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        _logging.FileHandler("tournament.log", encoding="utf-8"),
        _logging.StreamHandler(_sys.stdout),
    ],
)
logger = _logging.getLogger("JudoApp")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting up Judo Real-Time API...")
    init_db()
    with SessionLocal() as session:
        # Pre-create WB R1+ and all LB fight shells for every double-elimination
        # bracket so the full tree is visible and scorable immediately.
        # Exclude pool brackets (bracket_type='double' there means double-pool,
        # not double-elimination — they use single-elimination KO after pools).
        pool_bracket_ids_startup = {
            f.bracket_id
            for f in session.query(FightModel.bracket_id)
            .filter(FightModel.bracket_phase == "pool")
            .distinct()
            .all()
        }
        double_brackets = (
            session.query(BracketModel)
            .filter(BracketModel.bracket_type.in_(["ko", "double", "DOUBLE_ELIMINATION"]))
            .all()
        )
        for b in double_brackets:
            if b.id in pool_bracket_ids_startup:
                continue  # pool→KO bracket: no LB
            generate_wb_shells(b.id, session)
            generate_lb_fights(b.id, session)
        session.commit()
        # Catch up with any fights finished externally while the server was down
        logger.info("Healing bracket progressions...")
        heal_bracket_progressions(session)
    yield


app = FastAPI(title="Judo Real-Time API", lifespan=lifespan)

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_requests(request, call_next):
    start_time = datetime.now()
    response = await call_next(request)
    duration = (datetime.now() - start_time).total_seconds()
    logger.info(
        f"API {request.method} {request.url.path} - Status: {response.status_code} - {duration:.3f}s"
    )
    return response


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    logger.error(f"Global Error: {request.method} {request.url.path} - {str(exc)}", exc_info=True)
    from fastapi.responses import JSONResponse

    return JSONResponse(status_code=500, content={"detail": f"Serverfehler: {str(exc)}"})


# --- WebSocket Manager ---


class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"New client connected. Total connections: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info(f"Client disconnected. Total connections: {len(self.active_connections)}")

    async def broadcast(self, message: dict):
        logger.debug(f"Broadcasting message: {message.get('type')}")
        for connection in self.active_connections:
            with suppress(Exception):
                await connection.send_json(message)


manager = ConnectionManager()


def get_match_dict(fight_id: int, session):
    fight = session.query(FightModel).filter(FightModel.id == fight_id).first()
    if not fight:
        return None

    # fight.participant1_id / participant2_id are group_participant IDs, not
    # participant IDs.  Resolve the correct name+club via GroupParticipantModel.
    gp_ids = [pid for pid in [fight.participant1_id, fight.participant2_id] if pid]
    gp_rows = (
        {
            gp.id: gp
            for gp in session.query(GroupParticipantModel)
            .filter(GroupParticipantModel.id.in_(gp_ids))
            .all()
        }
        if gp_ids
        else {}
    )
    actual_p_ids = list({gp.participant_id for gp in gp_rows.values() if gp.participant_id})
    p_objs = (
        {
            p.id: p
            for p in session.query(ParticipantModel)
            .filter(ParticipantModel.id.in_(actual_p_ids))
            .all()
        }
        if actual_p_ids
        else {}
    )

    def _resolve_p(gp_id):
        gp = gp_rows.get(gp_id)
        return p_objs.get(gp.participant_id) if gp else None

    p1_obj = _resolve_p(fight.participant1_id)
    p2_obj = _resolve_p(fight.participant2_id)

    # Next fight in bracket tree (LB uses injection/reduction alternation)
    lr = fight.round or 0
    pos = fight.pos_in_round or 0
    if fight.bracket_phase == "lb":
        if lr % 2 == 1:  # injection → next is reduction
            next_round, next_pos = lr + 1, pos // 2
            next_match_pos = "p1" if pos % 2 == 0 else "p2"
        else:  # reduction/initial → next is injection
            next_round, next_pos = lr + 1, pos
            next_match_pos = "p1"
    else:
        next_round, next_pos = lr + 1, pos // 2
        next_match_pos = "p1" if pos % 2 == 0 else "p2"
    next_fight = (
        session.query(FightModel)
        .filter(
            FightModel.bracket_id == fight.bracket_id,
            FightModel.bracket_phase == fight.bracket_phase,
            FightModel.round == next_round,
            FightModel.pos_in_round == next_pos,
        )
        .first()
    )
    next_match_id = next_fight.id if next_fight else None

    # Category name + mat_id from bracket
    b_info = (
        session.query(BracketModel, GroupModel)
        .join(GroupModel, BracketModel.group_id == GroupModel.id)
        .filter(BracketModel.id == fight.bracket_id)
        .first()
    )
    category = (
        f"{b_info[1].age_group} {b_info[1].weight_class}"
        if b_info
        else f"Bracket {fight.bracket_id}"
    )

    # table_id: fight-level override → bracket mat_id → computed fallback
    if fight.table_id:
        table_id = str(fight.table_id)
    elif b_info and b_info[0].mat_id:
        table_id = str(b_info[0].mat_id)
    else:
        table_id = "0"  # no mat assigned — hidden from all table filters

    return {
        "matchId": fight.id,
        "tableId": table_id,
        "fightNr": fight.fight_number or fight.id,
        "category": category,
        "bracketId": str(fight.bracket_id) if fight.bracket_id else "",
        "round": (fight.round or 0) + 1,
        "posInRound": fight.pos_in_round or 0,
        "p1": {
            "id": str(fight.participant1_id) if fight.participant1_id else "WAIT",
            "firstName": p1_obj.first_name if p1_obj else "",
            "lastName": p1_obj.last_name if p1_obj else "TBD",
            "club": p1_obj.club if p1_obj else "",
            "score": {"points": fight.score1 if fight.score1 is not None else 0},
        },
        "p2": {
            "id": str(fight.participant2_id) if fight.participant2_id else "WAIT",
            "firstName": p2_obj.first_name if p2_obj else "",
            "lastName": p2_obj.last_name if p2_obj else "TBD",
            "club": p2_obj.club if p2_obj else "",
            "score": {"points": fight.score2 if fight.score2 is not None else 0},
        },
        "status": "finished" if fight.status == "completed" else (fight.status or "upcoming"),
        "order": fight.fight_number or fight.id,
        "restTimeMin": 0,
        "duration": fight.duration,
        "phase": fight.bracket_phase,
        "poolIndex": fight.pool_index,
        "bracketType": b_info[0].bracket_type if b_info else None,
        "gender": {"m": "M", "w": "F"}.get((b_info[1].gender or "").lower(), b_info[1].gender)
        if b_info
        else None,
        "ageGroup": b_info[1].age_group if b_info else None,
        "weightClass": b_info[1].weight_class if b_info else None,
        "winnerId": str(fight.winner_id) if fight.winner_id else None,
        "nextMatchId": next_match_id,
        "nextMatchPos": next_match_pos if next_match_id else None,
    }


# --- Bracket Progression Helpers ---


def _effective_winner(fight: FightModel) -> int | None:
    """Return the effective winner_id, including auto-win for bye (p1==p2)."""
    if fight.winner_id:
        return fight.winner_id
    if fight.participant1_id and fight.participant1_id == fight.participant2_id:
        return fight.participant1_id
    return None


def _make_fight(bracket_id, phase, round_num, pos, table_id, session) -> FightModel:
    """Find or lazily create a fight at the given coordinates."""
    f = (
        session.query(FightModel)
        .filter(
            FightModel.bracket_id == bracket_id,
            FightModel.bracket_phase == phase,
            FightModel.round == round_num,
            FightModel.pos_in_round == pos,
        )
        .first()
    )
    if not f:
        f = FightModel(
            bracket_id=bracket_id,
            bracket_phase=phase,
            round=round_num,
            pos_in_round=pos,
            status="upcoming",
            table_id=table_id,
        )
        session.add(f)
        session.flush()
    return f


def _fill_slot(fight: FightModel, slot: str, participant_id: int, session) -> None:
    """Fill p1 or p2 slot if empty; auto-resolve bye and cascade if both slots match."""
    if slot == "p1" and not fight.participant1_id:
        fight.participant1_id = participant_id
    elif slot == "p2" and not fight.participant2_id:
        fight.participant2_id = participant_id

    if (
        fight.participant1_id
        and fight.participant2_id
        and fight.participant1_id == fight.participant2_id
    ):
        fight.status = "bye"
        fight.winner_id = fight.participant1_id
        session.flush()
        _advance_winner(fight, session)


def _advance_winner(fight: FightModel, session) -> FightModel | None:
    """
    Ensure the winner of `fight` is placed in the correct slot of the next fight.
    Handles both WB (binary tree) and LB (alternating injection/reduction).
    Creates the next fight row lazily. Returns next fight or None if this is the final.
    """
    if fight.round is None or fight.pos_in_round is None:
        return None
    winner_id = _effective_winner(fight)
    if not winner_id:
        return None

    lr = fight.round
    pos = fight.pos_in_round

    # ── Loser Bracket ────────────────────────────────────────────────────────
    if fight.bracket_phase == "lb":
        # LB rounds alternate:
        #   Even (0, 2, 4, …): reduction round  → winner keeps same lane → next pos = pos, slot p1
        #   Odd  (1, 3, 5, …): injection round  → two winners collapse   → next pos = pos//2
        #
        # End-of-bracket: an odd injection round whose sibling doesn't exist
        # is the 3rd-place match — nothing to create beyond it.
        if lr % 2 == 1:  # injection round
            sibling = (
                session.query(FightModel)
                .filter(
                    FightModel.bracket_id == fight.bracket_id,
                    FightModel.bracket_phase == "lb",
                    FightModel.round == lr,
                    FightModel.pos_in_round == (pos ^ 1),
                )
                .first()
            )
            if not sibling:
                return None  # 3rd-place match — no further advancement
            # Don't advance into the final LB reduction round — both survivors share 3rd place
            wb_r0_count = (
                session.query(FightModel)
                .filter(
                    FightModel.bracket_id == fight.bracket_id,
                    FightModel.bracket_phase == "wb",
                    FightModel.round == 0,
                )
                .count()
            )
            if wb_r0_count >= 2:
                lb_rounds_total = 2 * int(_math.log2(wb_r0_count)) - 1
                if lr + 1 >= lb_rounds_total - 1:
                    return None  # Both reach 3rd place — no fight needed
            next_pos = pos // 2
            next_slot = "p1" if pos % 2 == 0 else "p2"
        else:  # reduction round (even)
            wb_r_upcoming = (lr + 2) // 2
            # Use WB R0 count to structurally determine the WB final round.
            # The WB final is round log2(wb_r0_count). If wb_r_upcoming reaches
            # the final, no LB injection round follows (WB final loser = runner-up).
            wb_r0_count = (
                session.query(FightModel)
                .filter(
                    FightModel.bracket_id == fight.bracket_id,
                    FightModel.bracket_phase == "wb",
                    FightModel.round == 0,
                )
                .count()
            )
            wb_final_round = int(_math.log2(wb_r0_count)) if wb_r0_count >= 2 else 1
            if wb_r_upcoming >= wb_final_round:
                return None  # Next WB round is the final — no LB injection follows
            next_pos = pos
            next_slot = "p1"

        next_fight = _make_fight(fight.bracket_id, "lb", lr + 1, next_pos, fight.table_id, session)
        _fill_slot(next_fight, next_slot, winner_id, session)
        logger.info(
            f"Advancing Winner {winner_id} from Fight {fight.id} to LB Fight {next_fight.id} ({next_slot})"
        )
        return next_fight

    # ── Winners Bracket ───────────────────────────────────────────────────────
    # Sibling check: the WB final has no sibling → stop.
    sibling_pos = pos ^ 1
    sibling = (
        session.query(FightModel)
        .filter(
            FightModel.bracket_id == fight.bracket_id,
            FightModel.bracket_phase == "wb",
            FightModel.round == lr,
            FightModel.pos_in_round == sibling_pos,
        )
        .first()
    )
    if not sibling:
        return None

    next_coord = BracketManager.get_next_winner_coord(
        fight.bracket_id, lr, pos, fight.bracket_phase
    )
    next_fight = _make_fight(
        fight.bracket_id,
        next_coord["phase"],
        next_coord["round"],
        next_coord["pos"],
        fight.table_id,
        session,
    )
    _fill_slot(next_fight, next_coord["slot"], winner_id, session)
    logger.info(
        f"Advancing Winner {winner_id} from Fight {fight.id} to {next_coord['phase'].upper()} Fight {next_fight.id} ({next_coord['slot']})"
    )
    return next_fight


def _resolve_lb_injection_bye(wb_fight: FightModel, session) -> None:
    """
    Called when a WB fight (R1+) is a bye so no real loser will ever be injected
    into the corresponding LB injection slot.  If p1 of that LB fight is already
    filled (the LB reduction winner arrived), fill p2 with the same participant
    so _fill_slot auto-resolves it as an LB bye and cascades the winner forward.
    """
    if wb_fight.round == 0:
        return  # WB R0 byes are handled inside _advance_loser itself
    lb_round = 2 * wb_fight.round - 1
    lb_pos = wb_fight.pos_in_round
    lb_fight = (
        session.query(FightModel)
        .filter(
            FightModel.bracket_id == wb_fight.bracket_id,
            FightModel.bracket_phase == "lb",
            FightModel.round == lb_round,
            FightModel.pos_in_round == lb_pos,
        )
        .first()
    )
    if not lb_fight:
        return
    # Only act when the LB reduction winner (p1) is already waiting for an
    # opponent that will never come (p2 still empty).
    if lb_fight.participant1_id and not lb_fight.participant2_id:
        _fill_slot(lb_fight, "p2", lb_fight.participant1_id, session)


def _advance_loser(fight: FightModel, session) -> FightModel | None:
    """
    For double-elimination WB fights: place the loser into the correct LB fight.
    WB round 0 losers pair with each other in LB R0.
    WB round r≥1 losers inject into LB round 2r-1 as p2.
    WB final loser → 2nd place (skipped).
    WB bye fights have no real loser — if the WB R0 sibling was a bye, the LB
    fight becomes a bye so the real loser auto-advances to LB R1.
    """
    if fight.bracket_phase != "wb":
        return None

    # Skip WB bye fights — the "loser" equals the winner, no real opponent.
    # For R1+ byes: check whether the LB injection fight is already waiting.
    if fight.participant1_id and fight.participant1_id == fight.participant2_id:
        _resolve_lb_injection_bye(fight, session)
        return None

    winner_id = _effective_winner(fight)
    if not winner_id:
        return None
    loser_id = (
        fight.participant2_id if fight.participant1_id == winner_id else fight.participant1_id
    )
    if not loser_id:
        return None

    # Skip WB final (no sibling = final)
    sibling_pos = fight.pos_in_round ^ 1
    sibling = (
        session.query(FightModel)
        .filter(
            FightModel.bracket_id == fight.bracket_id,
            FightModel.bracket_phase == "wb",
            FightModel.round == fight.round,
            FightModel.pos_in_round == sibling_pos,
        )
        .first()
    )
    if not sibling:
        return None

    wb_r = fight.round
    pos = fight.pos_in_round

    if wb_r == 0:
        # WB R0 losers pair: pos 0,1 → LB R0 pos 0; pos 2,3 → LB R0 pos 1 …
        lb_round = 0
        lb_pos = pos // 2
        lb_slot = "p1" if pos % 2 == 0 else "p2"
        lb_other = "p2" if lb_slot == "p1" else "p1"
        sibling_bye = sibling.status == "bye"
    else:
        # WB Rr (r≥1) inject into LB round 2r-1 as p2 (same lane)
        lb_round = 2 * wb_r - 1
        lb_pos = pos
        lb_slot = "p2"
        lb_other = None
        sibling_bye = False

    lb_fight = _make_fight(fight.bracket_id, "lb", lb_round, lb_pos, fight.table_id, session)
    _fill_slot(lb_fight, lb_slot, loser_id, session)
    logger.info(
        f"Advancing Loser {loser_id} from WB Fight {fight.id} to LB Fight {lb_fight.id} ({lb_slot})"
    )

    # If the adjacent WB R0 fight was a bye (Freilos), no real opponent will
    # ever fill the other LB R0 slot.  Fill it with the same participant so
    # _fill_slot auto-resolves the LB fight as a bye → winner cascades to LB R1.
    if (
        sibling_bye
        and lb_other
        and not getattr(lb_fight, f"participant{'2' if lb_other == 'p2' else '1'}_id")
    ):
        _fill_slot(lb_fight, lb_other, loser_id, session)

    return lb_fight


def heal_bracket_progressions(session) -> bool:
    """
    Scan every finished fight and ensure next-round fights exist with correct slots.
    Also propagates WB losers into LB for double-elimination brackets.
    Loops until stable (handles cascading byes). Returns True if any fights created.
    """
    # Build set of double-elimination bracket IDs once.
    # Exclude brackets that have pool fights — those use a pool→KO format
    # (single elimination after pools) and must NOT get a loser bracket.
    pool_bracket_ids = {
        f.bracket_id
        for f in session.query(FightModel.bracket_id)
        .filter(FightModel.bracket_phase == "pool")
        .distinct()
        .all()
    }
    double_bracket_ids = {
        b.id
        for b in session.query(BracketModel)
        .filter(BracketModel.bracket_type.in_(["ko", "double", "DOUBLE_ELIMINATION"]))
        .all()
    } - pool_bracket_ids

    total_created = False
    while True:
        finished = (
            session.query(FightModel)
            .filter(
                FightModel.winner_id.isnot(None),
                FightModel.round.isnot(None),
                FightModel.pos_in_round.isnot(None),
            )
            .order_by(FightModel.round, FightModel.pos_in_round)
            .all()
        )

        count_before = session.query(FightModel).count()
        for fight in finished:
            _advance_winner(fight, session)
            if fight.bracket_phase == "wb" and fight.bracket_id in double_bracket_ids:
                _advance_loser(fight, session)

        # Safety net: any LB injection fight where p1 is waiting and the WB
        # source fight is already finished (bye or otherwise) gets resolved.
        lb_injection_waiting = (
            session.query(FightModel)
            .filter(
                FightModel.bracket_phase == "lb",
                FightModel.winner_id.is_(None),
                FightModel.participant1_id.isnot(None),
                FightModel.participant2_id.is_(None),
            )
            .all()
        )
        for lbf in lb_injection_waiting:
            if lbf.round is None or lbf.round % 2 == 0:
                continue  # only injection rounds (odd DB round) need this check
            wb_r = (lbf.round + 1) // 2
            wb_pos = lbf.pos_in_round
            wb_src = (
                session.query(FightModel)
                .filter(
                    FightModel.bracket_id == lbf.bracket_id,
                    FightModel.bracket_phase == "wb",
                    FightModel.round == wb_r,
                    FightModel.pos_in_round == wb_pos,
                )
                .first()
            )
            if wb_src and wb_src.winner_id:
                # WB source is done but no loser was placed → create LB bye
                _fill_slot(lbf, "p2", lbf.participant1_id, session)

        session.flush()
        count_after = session.query(FightModel).count()

        if count_after <= count_before:
            break
        total_created = True

    # Generate double-pool KO phases for any fully-completed pool bracket
    double_pool_bracket_ids = {
        f.bracket_id
        for f in session.query(FightModel)
        .filter(FightModel.bracket_phase == "pool")
        .distinct(FightModel.bracket_id)
        .all()
    }
    for bid in double_pool_bracket_ids:
        new_ko = _generate_double_pool_ko(bid, session)
        if new_ko:
            session.flush()
            total_created = True

    session.commit()
    return total_created


def _pool_standings_for_index(pool_fights: list, pool_index: int) -> list:
    """Return participant IDs sorted by wins desc, then Ubw (score difference) desc."""
    pf = [f for f in pool_fights if f.pool_index == pool_index]
    fighter_ids: set = set()
    for f in pf:
        if f.participant1_id:
            fighter_ids.add(f.participant1_id)
        if f.participant2_id:
            fighter_ids.add(f.participant2_id)

    stats: dict = {fid: {"wins": 0, "ubw": 0} for fid in fighter_ids}
    for f in pf:
        # Include all decided fights (even draws where winner_id is null)
        if f.status not in ("finished", "completed", "bye"):
            continue
        for fid in fighter_ids:
            is_p1 = f.participant1_id == fid
            is_p2 = f.participant2_id == fid
            if not is_p1 and not is_p2:
                continue
            own = int((f.score1 if is_p1 else f.score2) or 0)
            opp = int((f.score2 if is_p1 else f.score1) or 0)
            stats[fid]["ubw"] += max(0, own - opp)
            if f.winner_id == fid:
                stats[fid]["wins"] += 1

    return sorted(fighter_ids, key=lambda fid: (-stats[fid]["wins"], -stats[fid]["ubw"]))


def _generate_double_pool_ko(bracket_id: int, session) -> list:
    """
    After both pools of a double-pool bracket are complete, create the KO phase:
      SF1 (wb R0 pos0): Pool A 1st  vs Pool B 2nd
      SF2 (wb R0 pos1): Pool B 1st  vs Pool A 2nd
      Final (wb R1 pos0): winner SF1 vs winner SF2  (shell only)
    Returns list of newly created FightModel objects.
    """
    logger.info(f"Generating Double-Pool KO phase for Bracket {bracket_id}")
    pool_fights = (
        session.query(FightModel)
        .filter(
            FightModel.bracket_id == bracket_id,
            FightModel.bracket_phase == "pool",
        )
        .all()
    )

    if not pool_fights:
        return []

    # Need both pool_index 0 and 1, and all fights must be decided (wins or draws)
    indices = {f.pool_index for f in pool_fights if f.pool_index is not None}
    if 0 not in indices or 1 not in indices:
        return []  # not a double pool
    if any(f.status not in ("finished", "completed", "bye") for f in pool_fights):
        return []  # pools not done yet

    # KO fights already exist?
    if (
        session.query(FightModel)
        .filter(
            FightModel.bracket_id == bracket_id,
            FightModel.bracket_phase == "wb",
        )
        .first()
    ):
        return []  # already generated

    a = _pool_standings_for_index(pool_fights, 0)
    b = _pool_standings_for_index(pool_fights, 1)
    if len(a) < 2 or len(b) < 2:
        return []

    table_id = pool_fights[0].table_id
    created = []

    sf1 = _make_fight(bracket_id, "wb", 0, 0, table_id, session)
    _fill_slot(sf1, "p1", a[0], session)  # Pool A 1st
    _fill_slot(sf1, "p2", b[1], session)  # Pool B 2nd
    created.append(sf1)

    sf2 = _make_fight(bracket_id, "wb", 0, 1, table_id, session)
    _fill_slot(sf2, "p1", b[0], session)  # Pool B 1st
    _fill_slot(sf2, "p2", a[1], session)  # Pool A 2nd
    created.append(sf2)

    # Final shell — participants filled by _advance_winner when SFs complete
    final = _make_fight(bracket_id, "wb", 1, 0, table_id, session)
    created.append(final)

    session.flush()
    return created


def generate_wb_shells(bracket_id: int, session) -> int:
    """
    Pre-create WB R1+ fight shells for a bracket so all future rounds exist in
    the DB before WB R0 fights complete.  Participants are filled lazily by
    _advance_winner as each round finishes.  Returns number of new fights created.
    """
    logger.info(f"Generating WB shells for Bracket {bracket_id}")
    wb_r0 = (
        session.query(FightModel)
        .filter(
            FightModel.bracket_id == bracket_id,
            FightModel.bracket_phase == "wb",
            FightModel.round == 0,
        )
        .all()
    )
    if not wb_r0:
        return 0
    n = len(wb_r0)
    if n < 2:
        return 0
    table_id = wb_r0[0].table_id
    total_rounds = int(_math.log2(n)) + 1  # e.g. n=8 → 4 rounds (R0–R3)
    created = 0
    for wb_round in range(1, total_rounds):  # skip R0 (already exists)
        fight_count = n // (2**wb_round)
        for pos in range(fight_count):
            existing = (
                session.query(FightModel)
                .filter(
                    FightModel.bracket_id == bracket_id,
                    FightModel.bracket_phase == "wb",
                    FightModel.round == wb_round,
                    FightModel.pos_in_round == pos,
                )
                .first()
            )
            if not existing:
                f = FightModel(
                    bracket_id=bracket_id,
                    bracket_phase="wb",
                    round=wb_round,
                    pos_in_round=pos,
                    status="upcoming",
                    table_id=table_id,
                )
                session.add(f)
                created += 1
    session.flush()
    return created


def generate_lb_fights(bracket_id: int, session) -> int:
    """
    Pre-create all LB fight shells for a double-elimination bracket.

    Uses the WB R0 fight count to compute the full LB structure
    (alternating reduction/injection rounds) and creates empty fight rows
    so that _advance_loser / _advance_winner can fill participants lazily.
    Returns the number of new fights created.
    """
    logger.info(f"Generating LB fights for Bracket {bracket_id}")
    wb_r0 = (
        session.query(FightModel)
        .filter(
            FightModel.bracket_id == bracket_id,
            FightModel.bracket_phase == "wb",
            FightModel.round == 0,
        )
        .all()
    )
    if not wb_r0:
        return 0

    n = len(wb_r0)  # must be a power of 2 (byes already included)
    if n < 2:
        return 0

    table_id = wb_r0[0].table_id

    # Total LB rounds = 2 * log2(n) - 1
    # e.g. n=8  → 5 LB rounds (DB 0-4), 13 fights
    #      n=4  → 3 LB rounds (DB 0-2),  5 fights
    #      n=16 → 7 LB rounds (DB 0-6), 29 fights
    lb_rounds_total = 2 * int(_math.log2(n)) - 1
    created = 0
    fight_count = n // 2  # LB R0 starts with N/2 fights

    for lb_round in range(lb_rounds_total - 1):  # skip last reduction round (both survivors = 3rd place)
        for pos in range(fight_count):
            existing = (
                session.query(FightModel)
                .filter(
                    FightModel.bracket_id == bracket_id,
                    FightModel.bracket_phase == "lb",
                    FightModel.round == lb_round,
                    FightModel.pos_in_round == pos,
                )
                .first()
            )
            if not existing:
                f = FightModel(
                    bracket_id=bracket_id,
                    bracket_phase="lb",
                    round=lb_round,
                    pos_in_round=pos,
                    status="upcoming",
                    table_id=table_id,
                )
                session.add(f)
                created += 1

        # Advance to next round:
        # Even DB round = reduction → next is injection (same count)
        # Odd DB round  = injection → next is reduction (half count)
        if lb_round % 2 == 1:  # injection round just done → halve
            fight_count //= 2

    session.flush()
    return created


# --- API & WebSockets ---


@app.post("/api/generate-lb")
def api_generate_lb():
    """Pre-create all LB fight shells for every double-elimination bracket."""
    with SessionLocal() as session:
        brackets = (
            session.query(BracketModel)
            .filter(BracketModel.bracket_type.in_(["ko", "double", "DOUBLE_ELIMINATION"]))
            .all()
        )
        total = 0
        for b in brackets:
            total += generate_lb_fights(b.id, session)
        session.commit()
    return {"created": total}


@app.post("/api/heal")
def api_heal():
    """Manually trigger bracket progression healing (e.g. after edv_backend completes fights)."""
    with SessionLocal() as session:
        created = heal_bracket_progressions(session)
    return {"healed": created}


@app.get("/api/matches")
def get_matches():
    with SessionLocal() as session:
        fights = session.query(FightModel).order_by(FightModel.fight_number).all()

        # Build lookup: (bracket_id, phase, round, pos_in_round) → fight.id
        fight_lookup = {
            (f.bracket_id, f.bracket_phase, f.round, f.pos_in_round): f.id for f in fights
        }

        # Single query for all bracket+group data (replaces N per-bracket queries)
        bracket_ids = {f.bracket_id for f in fights if f.bracket_id}
        bracket_info = {
            bracket.id: (bracket, group)
            for bracket, group in session.query(BracketModel, GroupModel)
            .join(GroupModel, BracketModel.group_id == GroupModel.id)
            .filter(BracketModel.id.in_(list(bracket_ids)))
            .all()
        }

        # Batch-fetch all participant names by routing gp_id → participant_id.
        # fight.participant1/2_id are group_participant IDs, not participant IDs.
        gp_ids = {pid for f in fights for pid in [f.participant1_id, f.participant2_id] if pid}
        gp_rows = (
            {
                gp.id: gp
                for gp in session.query(GroupParticipantModel)
                .filter(GroupParticipantModel.id.in_(list(gp_ids)))
                .all()
            }
            if gp_ids
            else {}
        )
        actual_p_ids = list({gp.participant_id for gp in gp_rows.values() if gp.participant_id})
        participants = (
            {
                p.id: p
                for p in session.query(ParticipantModel)
                .filter(ParticipantModel.id.in_(actual_p_ids))
                .all()
            }
            if actual_p_ids
            else {}
        )

        def _p(gp_id):
            gp = gp_rows.get(gp_id)
            return participants.get(gp.participant_id) if gp else None

        match_list = []
        for f in fights:
            p1_obj = _p(f.participant1_id)
            p2_obj = _p(f.participant2_id)

            _lr = f.round or 0
            _pos = f.pos_in_round or 0
            if f.bracket_phase == "lb":
                if _lr % 2 == 1:  # injection → reduction
                    _nr, _np = _lr + 1, _pos // 2
                    next_match_pos = "p1" if _pos % 2 == 0 else "p2"
                else:  # reduction/initial → injection
                    _nr, _np = _lr + 1, _pos
                    next_match_pos = "p1"
            else:
                _nr, _np = _lr + 1, _pos // 2
                next_match_pos = "p1" if _pos % 2 == 0 else "p2"
            next_key = (f.bracket_id, f.bracket_phase, _nr, _np)
            next_match_id = fight_lookup.get(next_key)

            b_info = bracket_info.get(f.bracket_id)
            category = (
                f"{b_info[1].age_group} {b_info[1].weight_class}"
                if b_info
                else f"Bracket {f.bracket_id}"
            )

            # table_id: fight-level override → bracket mat_id → computed fallback
            if f.table_id:
                table_id = str(f.table_id)
            elif b_info and b_info[0].mat_id:
                table_id = str(b_info[0].mat_id)
            else:
                table_id = "0"  # no mat assigned — hidden from all table filters

            match_list.append(
                {
                    "matchId": f.id,
                    "tableId": table_id,
                    "fightNr": f.fight_number or f.id,
                    "category": category,
                    "bracketId": str(f.bracket_id) if f.bracket_id else "",
                    "round": (f.round or 0) + 1,
                    "posInRound": f.pos_in_round or 0,
                    "p1": {
                        "id": str(f.participant1_id) if f.participant1_id else "WAIT",
                        "firstName": p1_obj.first_name if p1_obj else "",
                        "lastName": p1_obj.last_name if p1_obj else "TBD",
                        "club": p1_obj.club if p1_obj else "",
                        "score": {"points": f.score1 if f.score1 is not None else 0},
                    },
                    "p2": {
                        "id": str(f.participant2_id) if f.participant2_id else "WAIT",
                        "firstName": p2_obj.first_name if p2_obj else "",
                        "lastName": p2_obj.last_name if p2_obj else "TBD",
                        "club": p2_obj.club if p2_obj else "",
                        "score": {"points": f.score2 if f.score2 is not None else 0},
                    },
                    "status": "finished" if f.status == "completed" else (f.status or "upcoming"),
                    "order": f.fight_number or f.id,
                    "restTimeMin": 0,
                    "duration": f.duration,
                    "phase": f.bracket_phase,
                    "poolIndex": f.pool_index,
                    "bracketType": b_info[0].bracket_type if b_info else None,
                    "gender": {"m": "M", "w": "F"}.get(
                        (b_info[1].gender or "").lower(), b_info[1].gender
                    )
                    if b_info
                    else None,
                    "ageGroup": b_info[1].age_group if b_info else None,
                    "weightClass": b_info[1].weight_class if b_info else None,
                    "winnerId": str(f.winner_id) if f.winner_id else None,
                    "nextMatchId": next_match_id,
                    "nextMatchPos": next_match_pos if next_match_id else None,
                }
            )

        return {"tournamentName": "Automated Tournament", "matches": match_list}


async def _handle_score_update(data: dict, session) -> None:
    fight = session.query(FightModel).filter(FightModel.id == data["matchId"]).first()
    if not fight:
        return
    logger.info(f"Score Update: Match {fight.id}, P{data['playerNum']} -> {data['value']}")
    if not fight.participant1_id or not fight.participant2_id:
        return
    if data["playerNum"] == 1:
        fight.score1 = data["value"]
    else:
        fight.score2 = data["value"]
    session.commit()
    match_dict = get_match_dict(fight.id, session)
    if match_dict:
        await manager.broadcast(
            {"type": "SCORE_SYNC", "matchId": data["matchId"], "match": match_dict}
        )


async def _handle_reassign_bracket(data: dict, session) -> None:
    b_id_str = data.get("bracketId")
    new_table = data.get("newTableId")
    if not b_id_str or new_table is None:
        return
    try:
        bracket_id_int = (
            int(b_id_str.replace("Bracket ", "")) if "Bracket" in b_id_str else int(b_id_str)
        )
        logger.info(f"Reassigning Bracket {bracket_id_int} to Table {new_table}")
        fights = session.query(FightModel).filter(FightModel.bracket_id == bracket_id_int).all()
        for f in fights:
            if f.status not in ["completed", "bye"]:
                f.table_id = str(new_table)
        session.commit()
        await manager.broadcast({"type": "REFRESH_LIST"})
    except ValueError:
        pass


async def _handle_reorder(data: dict, session) -> None:
    logger.info(f"Reordering {len(data['orders'])} fights")
    for m_id, order in data["orders"].items():
        session.query(FightModel).filter(FightModel.id == int(m_id)).update({"fight_number": order})
    session.commit()
    await manager.broadcast({"type": "REFRESH_LIST"})


async def _handle_status_update(data: dict, session) -> None:
    fight = session.query(FightModel).filter(FightModel.id == data["matchId"]).first()
    if not fight:
        return
    if data["status"] == "finished" and (not fight.participant1_id or not fight.participant2_id):
        return

    logger.info(f"Status Update: Match {fight.id} -> {data['status']}")
    fight.status = data["status"]

    if "duration" in data and not fight.duration:
        fight.duration = data["duration"]

    if data["status"] == "finished":
        winner_id = None

        is_bye = fight.participant1_id and fight.participant1_id == fight.participant2_id
        if is_bye:
            winner_id = fight.participant1_id
            fight.status = "bye"
        else:
            try:
                s1 = int(fight.score1 or 0)
                s2 = int(fight.score2 or 0)
            except (ValueError, TypeError):
                s1, s2 = 0, 0
            if s1 > s2:
                winner_id = fight.participant1_id
            elif s2 > s1:
                winner_id = fight.participant2_id

        if winner_id:
            fight.winner_id = winner_id
            logger.info(
                f"MATCH_FINISHED: Fight {fight.id}, Winner: {winner_id}, Duration: {fight.duration}s"
            )
            session.commit()
            try:
                bracket = (
                    session.query(BracketModel).filter(BracketModel.id == fight.bracket_id).first()
                )
                if bracket:
                    # WB/KO winner advancement (lazy creation + sibling guard)
                    next_fight = _advance_winner(fight, session)
                    if next_fight:
                        session.commit()
                        updated_next = get_match_dict(next_fight.id, session)
                        if updated_next:
                            await manager.broadcast(
                                {
                                    "type": "SCORE_SYNC",
                                    "matchId": next_fight.id,
                                    "match": updated_next,
                                }
                            )

                    # Double-elimination: send WB loser to LB.
                    # Skip brackets that have pool fights — those use pool→KO (single elim).
                    is_pool_ko = (
                        session.query(FightModel)
                        .filter(
                            FightModel.bracket_id == fight.bracket_id,
                            FightModel.bracket_phase == "pool",
                        )
                        .first()
                        is not None
                    )
                    if (
                        bracket.bracket_type in ("ko", "double", "DOUBLE_ELIMINATION")
                        and fight.bracket_phase == "wb"
                        and not is_pool_ko
                    ):
                        lb_fight = _advance_loser(fight, session)
                        if lb_fight:
                            session.commit()
                            updated_lb = get_match_dict(lb_fight.id, session)
                            if updated_lb:
                                await manager.broadcast(
                                    {
                                        "type": "SCORE_SYNC",
                                        "matchId": lb_fight.id,
                                        "match": updated_lb,
                                    }
                                )
                        # After placing (or skipping) the loser, resolve any LB
                        # injection fights whose WB source is now done but has no
                        # real loser (bye) — the waiting p1 gets a bye to advance.
                        if (
                            fight.bracket_phase == "wb"
                            and fight.round is not None
                            and fight.round >= 1
                        ):
                            lb_round = 2 * fight.round - 1
                            lb_pos = fight.pos_in_round
                            lbf = (
                                session.query(FightModel)
                                .filter(
                                    FightModel.bracket_id == fight.bracket_id,
                                    FightModel.bracket_phase == "lb",
                                    FightModel.round == lb_round,
                                    FightModel.pos_in_round == lb_pos,
                                )
                                .first()
                            )
                            if (
                                lbf
                                and lbf.participant1_id
                                and not lbf.participant2_id
                                and not lbf.winner_id
                            ):
                                _fill_slot(lbf, "p2", lbf.participant1_id, session)
                                session.commit()
                                updated_lbf = get_match_dict(lbf.id, session)
                                if updated_lbf:
                                    await manager.broadcast(
                                        {
                                            "type": "SCORE_SYNC",
                                            "matchId": lbf.id,
                                            "match": updated_lbf,
                                        }
                                    )

                    if fight.bracket_phase == "pool":
                        # When all pool fights are done, create the double-pool KO phase
                        new_ko = _generate_double_pool_ko(fight.bracket_id, session)
                        if new_ko:
                            session.commit()
                            for kof in new_ko:
                                kd = get_match_dict(kof.id, session)
                                if kd:
                                    await manager.broadcast(
                                        {"type": "SCORE_SYNC", "matchId": kof.id, "match": kd}
                                    )
                            await manager.broadcast({"type": "REFRESH_LIST"})

                    if bracket.bracket_type == "POOL":
                        all_fights = (
                            session.query(FightModel)
                            .filter(FightModel.bracket_id == fight.bracket_id)
                            .all()
                        )
                        p_ids = set()
                        for f in all_fights:
                            if f.participant1_id:
                                p_ids.add(f.participant1_id)
                            if f.participant2_id:
                                p_ids.add(f.participant2_id)
                        gps_with_p = (
                            session.query(GroupParticipantModel, ParticipantModel)
                            .join(
                                ParticipantModel,
                                GroupParticipantModel.participant_id == ParticipantModel.id,
                            )
                            .filter(GroupParticipantModel.participant_id.in_(list(p_ids)))
                            .all()
                        )
                        participant_data = [
                            {"id": gp.id, "name": f"{p.first_name} {p.last_name}", "club": p.club}
                            for gp, p in gps_with_p
                        ]
                        standings = BracketManager.calculate_pool_standings(
                            all_fights, participant_data
                        )
                        await manager.broadcast(
                            {
                                "type": "POOL_STANDINGS",
                                "bracketId": fight.bracket_id,
                                "standings": standings,
                            }
                        )
            except Exception as e:
                print(f"Advancement error: {e}")

    session.commit()
    session.refresh(fight)
    match_dict = get_match_dict(fight.id, session)
    if match_dict:
        await manager.broadcast(
            {"type": "SCORE_SYNC", "matchId": data["matchId"], "match": match_dict}
        )

    # Broadcast list refresh to show newly created fights
    await manager.broadcast({"type": "REFRESH_LIST"})


# ---------------------------------------------------------------------------
# Ippon Board TCP Bridge
# ---------------------------------------------------------------------------

IPPON_HOST = _os.getenv("IPPON_HOST", "172.17.192.62")
IPPON_PORT = int(_os.getenv("IPPON_PORT", "8080"))
OUR_HOST = _os.getenv("OUR_HOST", "localhost")


@app.get("/api/ippon-config")
def get_ippon_config():
    return {"host": IPPON_HOST, "port": IPPON_PORT}


@app.post("/api/ippon-config")
def set_ippon_config(body: dict):
    global IPPON_HOST, IPPON_PORT
    if "host" in body:
        IPPON_HOST = str(body["host"])
    if "port" in body:
        IPPON_PORT = int(body["port"])
    return {"host": IPPON_HOST, "port": IPPON_PORT}


@app.post("/api/ippon-score")
async def ippon_score_callback(request: Request):
    """Receive live score callback from the Ippon board and auto-finish the fight on a winner."""
    global _current_ippon_match_id
    try:
        data = await request.json()
    except Exception:
        return {"ok": False, "error": "invalid JSON"}

    print(f"Ippon callback: {data}")
    await manager.broadcast({"type": "IPPON_UPDATE", "scores": data})

    winner = data.get("winner", "none")
    if winner not in ("fighter1", "fighter2") or _current_ippon_match_id is None:
        return {"ok": True}

    player_num = 1 if winner == "fighter1" else 2

    # Board sends elapsed time (e.g. "3:30") → store directly as seconds
    duration_seconds = None
    try:
        parts = data.get("time", "").split(":")
        duration_seconds = int(parts[0]) * 60 + int(parts[1])
    except Exception:
        pass

    with SessionLocal() as session:
        fight = session.query(FightModel).filter(FightModel.id == _current_ippon_match_id).first()
        if not fight or fight.status in ("completed", "finished", "bye"):
            return {"ok": True}
        if player_num == 1:
            fight.score1 = 10
            fight.winner_id = fight.participant1_id
        else:
            fight.score2 = 10
            fight.winner_id = fight.participant2_id
        fight.status = "finished"
        if duration_seconds is not None:
            fight.duration = duration_seconds
        session.commit()

    _current_ippon_match_id = None
    return {"ok": True}


_current_ippon_match_id: int | None = None


def _post_fighter_sync(url: str, body: dict) -> None:
    """Blocking HTTP POST via urllib (requests not available)."""
    import urllib.request as _urllib_req
    import json as _json_sync

    try:
        data = _json_sync.dumps(body).encode()
        req = _urllib_req.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
        with _urllib_req.urlopen(req, timeout=3):
            pass
    except Exception as e:
        print(f"Ippon Board POST /fighters failed: {e}")


async def _ippon_start(match_id: int, match_dict: dict) -> None:
    """POST both fighters to the Ippon Board with our callback URL for score updates."""
    global _current_ippon_match_id
    _current_ippon_match_id = match_id

    gender = match_dict.get("gender")
    age_group = match_dict.get("ageGroup")
    weight_class = match_dict.get("weightClass")

    def _fighter(p: dict) -> dict:
        return {
            "firstname": p.get("firstName", ""),
            "lastname": p.get("lastName", ""),
            "weightclass": weight_class,
            "gender": gender,
            "agegroup": age_group,
        }

    fighters_url = f"http://{IPPON_HOST}:{IPPON_PORT}/fighters"
    callback_url = f"http://{OUR_HOST}:5001/api/ippon-score"
    p1 = match_dict.get("p1", {})
    p2 = match_dict.get("p2", {})
    if p1.get("id") and p1.get("id") != "WAIT" and p2.get("id") and p2.get("id") != "WAIT":
        await _asyncio.to_thread(
            _post_fighter_sync,
            fighters_url,
            {
                "fighter1": _fighter(p1),
                "fighter2": _fighter(p2),
                "callback": callback_url,
            },
        )


async def _handle_manual_override(data: dict, session) -> None:
    fight = session.query(FightModel).filter(FightModel.id == data["matchId"]).first()
    if not fight:
        return
    logger.info(f"MANUAL_OVERRIDE: Match {fight.id}, Data: {data}")

    if "p1Score" in data:
        fight.score1 = data["p1Score"]
    if "p2Score" in data:
        fight.score2 = data["p2Score"]
    if "duration" in data:
        fight.duration = data["duration"]

    session.commit()

    # If the fight was already finished, we might need to re-evaluate the winner
    if fight.status in ["completed", "finished", "bye"]:
        try:
            s1 = int(fight.score1 or 0)
            s2 = int(fight.score2 or 0)
        except (ValueError, TypeError):
            s1, s2 = 0, 0
        if s1 > s2:
            fight.winner_id = fight.participant1_id
        elif s2 > s1:
            fight.winner_id = fight.participant2_id
        else:
            fight.winner_id = None  # Tie
        session.commit()

    match_dict = get_match_dict(fight.id, session)
    if match_dict:
        await manager.broadcast(
            {"type": "SCORE_SYNC", "matchId": data["matchId"], "match": match_dict}
        )


async def _ippon_stop(match_id: int) -> None:
    """No-op: score updates now arrive via webhook callback, nothing to close."""
    pass


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")

            # Ippon Board messages don't need a DB session
            if msg_type == "IPPON_START":
                match_id = data.get("matchId")
                logger.info(f"IPPON START requested for Match {match_id}")
                if match_id:
                    with SessionLocal() as session:
                        md = get_match_dict(match_id, session)
                        if md:
                            _asyncio.create_task(_ippon_start(match_id, md))
                        else:
                            logger.error(
                                f"IPPON START failed: match_dict not found for Match {match_id}"
                            )
                continue
            if msg_type == "IPPON_STOP":
                match_id = data.get("matchId")
                if match_id:
                    _asyncio.create_task(_ippon_stop(match_id))
                continue

            with SessionLocal() as session:
                try:
                    if msg_type == "SCORE_UPDATE":
                        await _handle_score_update(data, session)
                    elif msg_type == "REASSIGN_BRACKET":
                        await _handle_reassign_bracket(data, session)
                    elif msg_type == "REORDER":
                        await _handle_reorder(data, session)
                    elif msg_type == "STATUS_UPDATE":
                        await _handle_status_update(data, session)
                    elif msg_type == "MANUAL_OVERRIDE":
                        await _handle_manual_override(data, session)
                    elif msg_type == "SIGNAL":
                        await manager.broadcast(data)
                except Exception as e:
                    logger.error(f"WebSocket Task Error ({msg_type}): {e}", exc_info=True)
                    await websocket.send_json(
                        {"type": "ERROR", "message": f"Operation fehlgeschlagen: {str(e)}"}
                    )
    except WebSocketDisconnect:
        manager.disconnect(websocket)


# Serve frontend static files — must be last so API routes take priority
app.mount("/", StaticFiles(directory="frontend", html=True), name="static")
