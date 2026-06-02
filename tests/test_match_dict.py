# SPDX-FileCopyrightText: 2026 TOP Team Combat Control
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for main.py — match-dict builder + category labelling.

Covers:
    • _group_label
    • _category_label (Pool / KO / LB / fallback)
    • _build_match_dict — tableId from DB, winnerId, projected fields
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

import main as backend


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

def _fight(**overrides):
    """Build a fake ORM-like Fight object via SimpleNamespace."""
    defaults = dict(
        id=1,
        bracket_id=10,
        bracket_phase='wb',
        round=0,
        pos_in_round=0,
        participant1_id=100,
        participant2_id=101,
        winner_id=None,
        fight_number=1,
        score1=None,
        score2=None,
        pool_index=None,
        table_id='2',
        status='pending',
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #

class TestGroupLabel:
    def test_all_three_parts(self):
        info = {'age_group': 'U18', 'gender': 'w', 'weight_class': '-70kg'}
        assert backend._group_label(info) == 'U18 w -70kg'

    def test_missing_pieces_skipped(self):
        info = {'age_group': '', 'gender': 'w', 'weight_class': '-70kg'}
        assert backend._group_label(info) == 'w -70kg'

    def test_empty_dict(self):
        assert backend._group_label({}) == ''


class TestCategoryLabel:
    def test_ko_uses_base(self):
        fight = _fight(bracket_phase='wb', pool_index=None)
        info = {'age_group': 'U18', 'gender': 'w', 'weight_class': '-70kg'}
        assert backend._category_label(fight, info) == 'U18 w -70kg'

    def test_pool_suffix_added(self):
        fight = _fight(bracket_phase='pool', pool_index=0)
        info = {'age_group': 'U18', 'gender': 'w', 'weight_class': '-70kg'}
        assert backend._category_label(fight, info) == 'U18 w -70kg · Pool 1'

    def test_pool_index_one_becomes_pool_2(self):
        fight = _fight(bracket_phase='pool', pool_index=1)
        info = {'age_group': 'U18', 'gender': 'w', 'weight_class': '-70kg'}
        assert backend._category_label(fight, info).endswith('· Pool 2')

    def test_no_group_info_falls_back_to_bracket_id(self):
        fight = _fight(bracket_id=42)
        assert backend._category_label(fight, {}) == 'Bracket 42'

    def test_no_group_no_bracket_id(self):
        fight = _fight(bracket_id=None)
        assert backend._category_label(fight, {}) == 'Unknown'


class TestPoolLabel:
    """_pool_label feeds the optional top-level 'pool' field of POST /fighters."""

    def test_pool_fight_one_based(self):
        assert backend._pool_label(_fight(bracket_phase='pool', pool_index=0)) == 'Pool 1'
        assert backend._pool_label(_fight(bracket_phase='pool', pool_index=2)) == 'Pool 3'

    def test_ko_fight_empty(self):
        assert backend._pool_label(_fight(bracket_phase='wb', pool_index=None)) == ''

    def test_pool_phase_without_index_empty(self):
        # defensive: phase says pool but index missing -> no label, no crash
        assert backend._pool_label(_fight(bracket_phase='pool', pool_index=None)) == ''


class TestBuildMatchDict:
    """Test the central match dict builder used by REST + WebSocket."""

    @patch('main._resolve_participants')
    def test_tableId_uses_db_value_not_round_robin(self, mock_resolve):
        """Regression — early version assigned table_id by `(id % 4) + 1`."""
        mock_resolve.return_value = {}
        fight = _fight(table_id='3', fight_number=99)  # id%4+1 would be 4
        result = backend._build_match_dict(
            session=None, fight=fight,
            fight_lookup={}, group_lookup={10: {}},
        )
        assert result['tableId'] == '3'

    @patch('main._resolve_participants')
    def test_winnerId_present(self, mock_resolve):
        mock_resolve.return_value = {
            100: {'gpId': 100, 'participantId': 1,
                  'firstName': 'Anna', 'lastName': 'Müller', 'club': 'KC'},
            101: {'gpId': 101, 'participantId': 2,
                  'firstName': 'Lena', 'lastName': 'Schmidt', 'club': 'HSV'},
        }
        fight = _fight(winner_id=100)
        result = backend._build_match_dict(
            session=None, fight=fight, fight_lookup={}, group_lookup={10: {}},
        )
        assert result['winnerId'] == 100
        assert result['winnerName'] == 'Anna Müller'

    @patch('main._resolve_participants')
    def test_gpId_in_p1_and_p2(self, mock_resolve):
        mock_resolve.return_value = {
            100: {'gpId': 100, 'participantId': 1, 'firstName': 'A',
                  'lastName': 'A', 'club': 'C'},
            101: {'gpId': 101, 'participantId': 2, 'firstName': 'B',
                  'lastName': 'B', 'club': 'C'},
        }
        fight = _fight()
        result = backend._build_match_dict(
            session=None, fight=fight, fight_lookup={}, group_lookup={10: {}},
        )
        assert result['p1']['gpId'] == 100
        assert result['p2']['gpId'] == 101

    @patch('main._resolve_participants')
    def test_empty_slot_yields_tbd_name(self, mock_resolve):
        mock_resolve.return_value = {}
        fight = _fight(participant1_id=None, participant2_id=None)
        result = backend._build_match_dict(
            session=None, fight=fight, fight_lookup={}, group_lookup={10: {}},
        )
        assert result['p1']['lastName'] == 'TBD'
        assert result['p2']['lastName'] == 'TBD'
        assert result['p1']['gpId'] is None

    @patch('main._resolve_participants')
    def test_category_label_carries_pool_suffix(self, mock_resolve):
        mock_resolve.return_value = {}
        fight = _fight(bracket_phase='pool', pool_index=0)
        result = backend._build_match_dict(
            session=None, fight=fight, fight_lookup={},
            group_lookup={10: {'age_group': 'U18', 'gender': 'w', 'weight_class': '-70kg'}},
        )
        assert result['categoryLabel'] == 'U18 w -70kg · Pool 1'
        assert result['phase'] == 'pool'
        assert result['poolIndex'] == 0

    @patch('main._resolve_participants')
    def test_score_default_zero(self, mock_resolve):
        mock_resolve.return_value = {}
        fight = _fight(score1=None, score2=None)
        result = backend._build_match_dict(
            session=None, fight=fight, fight_lookup={}, group_lookup={10: {}},
        )
        assert result['p1']['score']['points'] == 0
        assert result['p2']['score']['points'] == 0

    @patch('main._resolve_participants')
    def test_next_match_lookup(self, mock_resolve):
        mock_resolve.return_value = {}
        fight = _fight(round=0, pos_in_round=2)
        # next match key: (bracket_id, phase, round+1, pos//2) = (10, 'wb', 1, 1)
        result = backend._build_match_dict(
            session=None, fight=fight,
            fight_lookup={(10, 'wb', 1, 1): 77},
            group_lookup={10: {}},
        )
        assert result['nextMatchId'] == 77
        # pos_in_round=2 → even → p1
        assert result['nextMatchPos'] == 'p1'
