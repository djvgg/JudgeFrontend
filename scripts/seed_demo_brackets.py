from backend.database import (
    BracketModel,
    FightModel,
    GroupModel,
    GroupParticipantModel,
    ParticipantModel,
    SessionLocal,
)


def seed():
    session = SessionLocal()
    try:
        # 1. Use an existing group (U13 -28kg)
        group = session.query(GroupModel).filter_by(age_group="U13", weight_class="-28kg").first()
        if not group:
            print("Target group not found. Creating one...")
            group = GroupModel(gender="m", age_group="U13", weight_class="-28kg")
            session.add(group)
            session.flush()

        print(f"Using Group ID: {group.id}")

        # 2. Create a Pool Bracket
        bracket_pool = session.query(BracketModel).filter_by(group_id=group.id, bracket_type="POOL").first()
        if not bracket_pool:
            bracket_pool = BracketModel(group_id=group.id, bracket_type="POOL", status="ongoing")
            session.add(bracket_pool)
            session.flush()

        print(f"Pool Bracket ID: {bracket_pool.id}")

        # 3. Create participants for Pool
        p_names = [("Alice", "Pool"), ("Bob", "Pool"), ("Charlie", "Pool"), ("David", "Pool")]
        gps = []
        for first, last in p_names:
            p = session.query(ParticipantModel).filter_by(first_name=first, last_name=last).first()
            if not p:
                p = ParticipantModel(first_name=first, last_name=last, club="Club Demo")
                session.add(p)
                session.flush()

            gp = session.query(GroupParticipantModel).filter_by(group_id=group.id, participant_id=p.id).first()
            if not gp:
                gp = GroupParticipantModel(group_id=group.id, participant_id=p.id)
                session.add(gp)
                session.flush()
            gps.append(gp)

        # 4. Generate Pool Fights (6 fights for 4 people)
        existing_fights_count = session.query(FightModel).filter_by(bracket_id=bracket_pool.id).count()
        if existing_fights_count == 0:
            pairs = [(gps[0], gps[1]), (gps[2], gps[3]), (gps[0], gps[2]), (gps[1], gps[3]), (gps[0], gps[3]), (gps[1], gps[2])]
            for i, (p1, p2) in enumerate(pairs):
                f = FightModel(
                    bracket_id=bracket_pool.id,
                    participant1_id=p1.id,
                    participant2_id=p2.id,
                    status="upcoming",
                    bracket_phase="pool",
                    pool_index=i
                )
                session.add(f)
            session.flush()
            print("Pool fights created.")
        else:
            print("Pool fights already exist.")

        # 5. Create a Double Elimination Bracket (Group 4: U13 -31kg)
        group_de = session.query(GroupModel).filter_by(age_group="U13", weight_class="-31kg").first()
        if not group_de:
            group_de = GroupModel(gender="m", age_group="U13", weight_class="-31kg")
            session.add(group_de)
            session.flush()

        bracket_de = session.query(BracketModel).filter_by(group_id=group_de.id, bracket_type="DOUBLE_ELIMINATION").first()
        if not bracket_de:
            bracket_de = BracketModel(group_id=group_de.id, bracket_type="DOUBLE_ELIMINATION", status="ongoing")
            session.add(bracket_de)
            session.flush()

        print(f"DE Bracket ID: {bracket_de.id}")

        # 6. Create DE participants
        de_p_names = [("Eve", "DE"), ("Frank", "DE"), ("Grace", "DE"), ("Hans", "DE")]
        de_gps = []
        for first, last in de_p_names:
            p = session.query(ParticipantModel).filter_by(first_name=first, last_name=last).first()
            if not p:
                p = ParticipantModel(first_name=first, last_name=last, club="Club DE")
                session.add(p)
                session.flush()

            gp = session.query(GroupParticipantModel).filter_by(group_id=group_de.id, participant_id=p.id).first()
            if not gp:
                gp = GroupParticipantModel(group_id=group_de.id, participant_id=p.id)
                session.add(gp)
                session.flush()
            de_gps.append(gp)

        # 7. Generate DE Fights
        existing_de_fights = session.query(FightModel).filter_by(bracket_id=bracket_de.id).count()
        if existing_de_fights == 0:
            # WB Round 1
            session.add(FightModel(bracket_id=bracket_de.id, participant1_id=de_gps[0].id, participant2_id=de_gps[1].id, status="upcoming", bracket_phase="wb", round=1, pos_in_round=0))
            session.add(FightModel(bracket_id=bracket_de.id, participant1_id=de_gps[2].id, participant2_id=de_gps[3].id, status="upcoming", bracket_phase="wb", round=1, pos_in_round=1))
            # WB Round 2 (target for winners)
            session.add(FightModel(bracket_id=bracket_de.id, participant1_id=None, participant2_id=None, status="upcoming", bracket_phase="wb", round=2, pos_in_round=0))
            # LB Round 1 (target for losers)
            session.add(FightModel(bracket_id=bracket_de.id, participant1_id=None, participant2_id=None, status="upcoming", bracket_phase="lb", round=1, pos_in_round=0))
            session.flush()
            print("DE fights created.")
        else:
            print("DE fights already exist.")

        session.commit()
        print("Demo data seeded successfully.")
    except Exception as e:
        session.rollback()
        print(f"Error seeding data: {e}")
    finally:
        session.close()

if __name__ == "__main__":
    seed()
