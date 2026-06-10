# SPDX-FileCopyrightText: 2026 TOP Team Combat Control
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for main.py — per-mat Ipponboard routing.

Covers:
    • _normalizeIpponboardUrl — scheme prepend
    • _parseIpponboardUrls — tolerant JSON-map parsing
    • _ipponboardUrlForTable — table_id lookup + global fallback
"""

import main as backend

# --------------------------------------------------------------------------- #
# _normalizeIpponboardUrl — scheme handling
# --------------------------------------------------------------------------- #

def test_normalize_prepends_http_to_bare_ip():
    assert backend._normalizeIpponboardUrl("192.168.0.79:8080") == "http://192.168.0.79:8080"


def test_normalize_keeps_http_and_https():
    assert backend._normalizeIpponboardUrl("http://192.168.0.79:8080") == "http://192.168.0.79:8080"
    assert backend._normalizeIpponboardUrl("https://board.local:8080") == "https://board.local:8080"


def test_parse_map_normalizes_bare_values():
    raw = '{"1": "192.168.0.21:8080", "2": "http://192.168.0.22:8080"}'
    assert backend._parseIpponboardUrls(raw) == {
        "1": "http://192.168.0.21:8080",
        "2": "http://192.168.0.22:8080",
    }


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


# --------------------------------------------------------------------------- #
# file-backed mats map (admin-editable) — _loadIpponboardMats / _saveIpponboardMats
# --------------------------------------------------------------------------- #

def test_load_seeds_from_env_when_file_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(backend, "IPPONBOARD_MATS_FILE", str(tmp_path / "nope.json"))
    monkeypatch.setenv("IPPONBOARD_URLS", '{"1": "192.168.0.79:8080"}')
    assert backend._loadIpponboardMats() == {"1": "http://192.168.0.79:8080"}


def test_load_prefers_file_over_env(tmp_path, monkeypatch):
    f = tmp_path / "mats.json"
    f.write_text('{"2": "192.168.0.94:8080"}', encoding="utf-8")
    monkeypatch.setattr(backend, "IPPONBOARD_MATS_FILE", str(f))
    monkeypatch.setenv("IPPONBOARD_URLS", '{"1": "http://other:8080"}')
    assert backend._loadIpponboardMats() == {"2": "http://192.168.0.94:8080"}


def test_load_corrupt_file_falls_back_to_env(tmp_path, monkeypatch):
    f = tmp_path / "mats.json"
    f.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(backend, "IPPONBOARD_MATS_FILE", str(f))
    monkeypatch.setenv("IPPONBOARD_URLS", '{"1": "board:8080"}')
    assert backend._loadIpponboardMats() == {"1": "http://board:8080"}


def test_save_writes_file_normalizes_and_updates_lookup(tmp_path, monkeypatch):
    f = tmp_path / "mats.json"
    monkeypatch.setattr(backend, "IPPONBOARD_MATS_FILE", str(f))
    monkeypatch.setattr(backend, "IPPONBOARD_URL", "http://fallback:8080")
    saved = backend._saveIpponboardMats({"1": "192.168.0.79:8080", "2": "  ", 3: "http://x:8080"})
    # blank dropped, bare normalized, int key coerced
    assert saved == {"1": "http://192.168.0.79:8080", "3": "http://x:8080"}
    # round-trips through the file
    assert backend._loadIpponboardMats() == saved
    # the live lookup now uses the saved map
    assert backend._ipponboardUrlForTable(1) == "http://192.168.0.79:8080"
    assert backend._ipponboardUrlForTable(9) == "http://fallback:8080"
