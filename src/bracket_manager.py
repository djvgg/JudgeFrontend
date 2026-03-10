# SPDX-FileCopyrightText: 2026 TOP Team Combat Control
# SPDX-License-Identifier: GPL-3.0-or-later

"""
Bracket Manager - Handles tournament progression and standings.
Encapsulates rules for Winner/Loser advancement and Pool calculations.
"""

class BracketManager:
    @staticmethod
    def get_next_winner_coord(_bracket_id, round_num, pos_in_round, phase):
        """
        Calculates the (round, pos) for the winner of the current match.
        Standard binary tree advancement: next_pos = pos // 2
        """
        return {
            "round": round_num + 1,
            "pos": pos_in_round // 2,
            "phase": phase,
            "slot": "p1" if pos_in_round % 2 == 0 else "p2"
        }

    @staticmethod
    def get_next_loser_coord(_bracket_id, round_num, pos_in_round, phase, bracket_type):
        """
        Calculates where the loser goes.
        Mainly used for Double Elimination (Doppel-KO) to drop to Loser Bracket.
        """
        if bracket_type == "DOUBLE_ELIMINATION" and phase == "wb":
            if round_num == 1:
                # Round 1 WB losers drop to LB Round 1
                # next_pos = pos // 2, slot = p1 for even, p2 for odd
                return {
                    "round": 1,
                    "pos": pos_in_round // 2,
                    "phase": "lb",
                    "slot": "p1" if pos_in_round % 2 == 0 else "p2"
                }
            # For round 2 and further, the logic depends on the specific bracket size.
            # Defaulting to a simple drop-down for now.
            return {
                "round": round_num,
                "pos": pos_in_round,
                "phase": "lb",
                "slot": "p2" # Usually drops to p2 for odd rounds
            }
        return None

    @staticmethod
    def calculate_pool_standings(fights, participant_data):
        """
        Calculates standings for a Round Robin pool.
        participant_data: List of dicts with {'id': <GroupParticipantID>, 'name': ..., 'club': ...}
        """
        standings = []
        for p in participant_data:
            stats = {
                "id": p["id"],
                "name": p["name"],
                "club": p["club"],
                "wins": 0,
                "points": 0,
                "fights_count": 0
            }

            for f in fights:
                if f.status != "completed":
                    continue
    
                # Check if participant was in this fight
                is_p1 = f.participant1_id == p["id"]
                is_p2 = f.participant2_id == p["id"]
    
                if not (is_p1 or is_p2):
                    continue
    
                stats["fights_count"] += 1

                # Winner check
                if f.winner_id == p["id"]:
                    stats["wins"] += 1
                    # Points are the scores obtained
                    stats["points"] += (f.score1 if is_p1 else f.score2) or 0

            standings.append(stats)
            
        # Sort standings: Wins DESC, Points DESC
        standings.sort(key=lambda x: (x["wins"], x["points"]), reverse=True)
        return standings
