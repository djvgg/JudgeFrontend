from src.database import (
    BracketModel,
    FightModel,
    GroupModel,
    ParticipantModel,
)


def format_fight_response(
    fight, session, fight_lookup=None, category_names=None, participants=None
):
    if participants is not None:
        p1_obj = participants.get(fight.participant1_id)
        p2_obj = participants.get(fight.participant2_id)
    else:
        p1_obj = (
            session.query(ParticipantModel)
            .filter(ParticipantModel.id == fight.participant1_id)
            .first()
            if fight.participant1_id
            else None
        )
        p2_obj = (
            session.query(ParticipantModel)
            .filter(ParticipantModel.id == fight.participant2_id)
            .first()
            if fight.participant2_id
            else None
        )

    # Compute next match in bracket tree dynamically
    next_round = (fight.round or 0) + 1
    next_pos = (fight.pos_in_round or 0) // 2

    if fight_lookup is not None:
        next_key = (fight.bracket_id, fight.bracket_phase, next_round, next_pos)
        next_match_id = fight_lookup.get(next_key)
    else:
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

    next_match_pos = "p1" if (fight.pos_in_round or 0) % 2 == 0 else "p2"

    if category_names is not None:
        category = (
            category_names.get(fight.bracket_id, f"Bracket {fight.bracket_id}")
            if fight.bracket_id
            else "Unknown Category"
        )
    else:
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

    table_id = (
        str(fight.table_id)
        if getattr(fight, "table_id", None)
        else str((fight.fight_number or fight.id) % 4 + 1)
    )

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
            "score": {"points": fight.score1 if fight.score1 is not None else 0},
        },
        "p2": {
            "id": str(p2_obj.id) if p2_obj else "WAIT",
            "firstName": p2_obj.first_name if p2_obj else "",
            "lastName": p2_obj.last_name if p2_obj else "TBD",
            "club": p2_obj.club if p2_obj else "",
            "score": {"points": fight.score2 if fight.score2 is not None else 0},
        },
        "status": "finished" if fight.status == "completed" else (fight.status or "upcoming"),
        "order": fight.fight_number or fight.id,
        "restTimeMin": 0,
        "phase": fight.bracket_phase,
        "poolIndex": fight.pool_index,
        "winnerId": str(fight.winner_id) if fight.winner_id else None,
        "nextMatchId": next_match_id,
        "nextMatchPos": next_match_pos if next_match_id else None,
    }
