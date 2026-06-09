# SPDX-FileCopyrightText: 2026 TOP Team Combat Control
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for main.py — per-mat Ipponboard routing.

Covers:
    • _parseIpponboardUrls — tolerant JSON-map parsing
    • _ipponboardUrlForTable — table_id lookup + global fallback
"""

import main as backend

# --------------------------------------------------------------------------- #
# _parseIpponboardUrls — tolerant parsing
# --------------------------------------------------------------------------- #

def test_parse_valid_map():
    raw = '{"1": "http://192.168.0.21:8080", "2": "http://192.168.0.22:8080"}'
    assert backend._parseIpponboardUrls(raw) == {
        "1": "http://192.168.0.21:8080",
        "2": "http://192.168.0.22:8080",
    }


def test_parse_coerces_int_keys_and_strips_values():
    raw = '{"1": "  http://board:8080  "}'
    assert backend._parseIpponboardUrls(raw) == {"1": "http://board:8080"}


def test_parse_empty_or_blank_returns_empty():
    assert backend._parseIpponboardUrls(None) == {}
    assert backend._parseIpponboardUrls("") == {}
    assert backend._parseIpponboardUrls("   ") == {}


def test_parse_malformed_json_returns_empty():
    # must not raise — keeps the app bootable, fallback stays usable
    assert backend._parseIpponboardUrls("{not json") == {}


def test_parse_non_dict_returns_empty():
    assert backend._parseIpponboardUrls('["a", "b"]') == {}
    assert backend._parseIpponboardUrls('"just a string"') == {}


def test_parse_drops_blank_and_non_str_values():
    raw = '{"1": "", "2": "   ", "3": 8080, "4": "http://ok:8080"}'
    assert backend._parseIpponboardUrls(raw) == {"4": "http://ok:8080"}


# --------------------------------------------------------------------------- #
# _ipponboardUrlForTable — lookup + fallback
# --------------------------------------------------------------------------- #

def test_url_for_mapped_table(monkeypatch):
    monkeypatch.setattr(backend, "IPPONBOARD_URLS", {"1": "http://a:8080", "2": "http://b:8080"})
    monkeypatch.setattr(backend, "IPPONBOARD_URL", "http://fallback:8080")
    assert backend._ipponboardUrlForTable(1) == "http://a:8080"
    assert backend._ipponboardUrlForTable(2) == "http://b:8080"


def test_url_for_unmapped_table_falls_back(monkeypatch):
    monkeypatch.setattr(backend, "IPPONBOARD_URLS", {"1": "http://a:8080"})
    monkeypatch.setattr(backend, "IPPONBOARD_URL", "http://fallback:8080")
    assert backend._ipponboardUrlForTable(9) == "http://fallback:8080"


def test_url_for_none_table_falls_back(monkeypatch):
    monkeypatch.setattr(backend, "IPPONBOARD_URLS", {"1": "http://a:8080"})
    monkeypatch.setattr(backend, "IPPONBOARD_URL", "http://fallback:8080")
    assert backend._ipponboardUrlForTable(None) == "http://fallback:8080"


def test_url_for_empty_map_always_falls_back(monkeypatch):
    monkeypatch.setattr(backend, "IPPONBOARD_URLS", {})
    monkeypatch.setattr(backend, "IPPONBOARD_URL", "http://fallback:8080")
    assert backend._ipponboardUrlForTable(1) == "http://fallback:8080"
