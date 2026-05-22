# SPDX-FileCopyrightText: 2026 TOP Team Combat Control
# SPDX-License-Identifier: GPL-3.0-or-later

"""Pytest root configuration for the JudgeFrontend test suite.

Adds the repo root to sys.path so test modules can do
    from main import _build_match_dict
without per-file sys.path hacks.

Also stubs the .env-driven DATABASE_URL so importing `main` doesn't try
to talk to the real Postgres instance just to set up SQLAlchemy.
"""

import os
import sys

_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if _root not in sys.path:
    sys.path.insert(0, _root)

# Use a harmless in-memory SQLite URL so importing main.py / src.database
# doesn't require a running Postgres for pure-function tests.
os.environ.setdefault('DATABASE_URL', 'sqlite:///:memory:')
