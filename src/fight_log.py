# SPDX-FileCopyrightText: 2026 Merlin
#
# SPDX-License-Identifier: GPL-3.0-or-later
"""Append-only result log for finished fights.

One JSON object per finished fight is appended to a JSONL file (the canonical
data source); a CSV view is generated on demand from that same file. This module
does pure file I/O and holds NO dependency on main.py / the ORM — callers in
main.py assemble the record dict and hand it here, so the hot WS/webhook path
stays decoupled from disk concerns and this module stays unit-testable.

Tracked per fight (Merlin, 2026-06-10): category (gender/age/weight), mat,
winner, duration. fighter1/fighter2 are carried so the winner is meaningful and
draws (empty winner) remain interpretable.
"""

import csv
import io
import json
import os
import threading

# Canonical column order — shared by the JSONL keys and the CSV header so the two
# encodings never drift. `duration` is last and may be empty (WS/tablet path has
# no Ipponboard time).
FIELDS = [
    "ts",
    "fightId",
    "mat",
    "category",
    "gender",
    "ageGroup",
    "weightClass",
    "bracketType",
    "fighter1",
    "fighter2",
    "winner",
    "duration",
]

_LOCK = threading.Lock()


def log_path() -> str:
    """Resolve the JSONL path (env override `FIGHT_LOG_PATH`, else logs/fights.jsonl
    next to the JudgeFrontend package)."""
    override = os.getenv("FIGHT_LOG_PATH", "").strip()
    if override:
        return override
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "logs", "fights.jsonl")


def append(record: dict) -> None:
    """Append one finished-fight record as a JSON line. Best-effort: never raises
    into the caller (a logging failure must not abort a fight result)."""
    path = log_path()
    line = json.dumps({k: record.get(k, "") for k in FIELDS}, ensure_ascii=False)
    try:
        with _LOCK:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    except OSError:
        # Disk/permission trouble is logged elsewhere; the fight result still stands.
        pass


def export_csv() -> str:
    """Render the JSONL log as a CSV string: `;`-separated, UTF-8-with-BOM friendly,
    mirroring the contestants_*.csv convention so it opens cleanly in German Excel.
    The BOM itself is added by the HTTP layer; this returns the text body."""
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=FIELDS, delimiter=";", extrasaction="ignore")
    writer.writeheader()
    path = log_path()
    if os.path.exists(path):
        with _LOCK, open(path, encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    rec = json.loads(raw)
                except ValueError:
                    continue
                writer.writerow({k: rec.get(k, "") for k in FIELDS})
    return out.getvalue()
