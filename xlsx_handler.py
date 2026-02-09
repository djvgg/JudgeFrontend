# SPDX-FileCopyrightText: 2026 TOP Team Combat Control
#
# SPDX-License-Identifier: CC0-1.0

"""
XLSX handler: reads a participant spreadsheet, filters by age & weight,
and selects 16 fighters for the bracket.
"""

import re
import pandas as pd


# ── Column detection ─────────────────────────────────────────────────

# Each key maps to a list of possible column names (checked case-insensitively)
_COLUMN_CANDIDATES = {
    "id":     ["ID", "Id", "Teilnehmer-ID", "TeilnehmerID"],
    "name":   ["Name", "Nachname", "Surname"],
    "age":    ["Alter", "Age", "Jahrgang"],
    "weight": ["Gewicht (kg)", "Gewicht", "Gewicht(kg)", "Weight", "Weight (kg)"],
    "club":   ["Verein", "Club", "Verband"],
}


def _findColumn(df, candidates):
    """Return the first matching DataFrame column name, or None."""
    lowerMap = {c.lower().strip(): c for c in df.columns}
    for candidate in candidates:
        if candidate.lower() in lowerMap:
            return lowerMap[candidate.lower()]
    return None


def _detectColumns(df):
    """Auto-detect all relevant columns. Returns dict {role: actualName}."""
    detected = {}
    for role, candidates in _COLUMN_CANDIDATES.items():
        detected[role] = _findColumn(df, candidates)
    return detected


# ── Core functions ───────────────────────────────────────────────────

def readXlsx(filepath):
    """Read an Excel file into a DataFrame, or None on failure."""
    try:
        return pd.read_excel(filepath)
    except Exception as e:
        print(f"[ERROR] Could not read XLSX: {e}")
        return None


def filterParticipants(df, minWeight=30, maxWeight=70, maxAge=17):
    """
    Filter rows by age (≤ maxAge) and weight (minWeight–maxWeight).
    Returns list of fighter dicts with keys: id, name, vorname, alter, gewicht, verein.
    """
    cols = _detectColumns(df)

    for required in ("name", "age", "weight"):
        if not cols[required]:
            print(f"[ERROR] Required column not found: {required}")
            return []

    print(f"  Detected columns → "
          f"ID: {cols['id']}, Name: {cols['name']}, "
          f"Alter: {cols['age']}, Gewicht: {cols['weight']}, "
          f"Verein: {cols['club']}")

    filtered = []

    for _, row in df.iterrows():
        # --- age check ---
        try:
            age = int(row.get(cols["age"], 99))
        except (ValueError, TypeError):
            continue
        if age > maxAge:
            continue

        # --- weight check ---
        try:
            weight = float(row.get(cols["weight"], 0))
        except (ValueError, TypeError):
            continue
        if not (minWeight <= weight <= maxWeight):
            continue

        # --- parse name ("Vorname Nachname") ---
        fullName  = str(row.get(cols["name"], "")).strip()
        parts     = fullName.split()
        vorname   = parts[0] if len(parts) >= 2 else ""
        nachname  = " ".join(parts[1:]) if len(parts) >= 2 else fullName

        # --- extract numeric ID (e.g. "JUD006" → "006") ---
        rawId     = str(row.get(cols["id"], "")) if cols["id"] else ""
        numericId = re.sub(r"[^0-9]", "", rawId)

        # --- club ---
        club = str(row.get(cols["club"], "")) if cols["club"] else ""

        filtered.append({
            "id":      numericId,
            "name":    nachname,
            "vorname": vorname,
            "alter":   age,
            "gewicht": weight,
            "verein":  club,
        })

    return filtered


def selectFighters(participants, count=16):
    """
    Pick the first `count` participants sorted by weight.
    Adds a 'los' key (1-based draw number) to each.
    """
    byWeight = sorted(participants, key=lambda p: p["gewicht"])[:count]
    for i, fighter in enumerate(byWeight, start=1):
        fighter["los"] = i
    return byWeight


# ── Public entry point ───────────────────────────────────────────────

def processXlsx(filepath):
    """
    Full pipeline: read → filter → select 16 fighters.
    Returns list of 16 fighter dicts, or None on failure.
    """
    df = readXlsx(filepath)
    if df is None:
        return None

    print(f"  XLSX columns: {list(df.columns)}")

    participants = filterParticipants(df)
    print(f"  Filtered participants: {len(participants)}")

    if len(participants) < 16:
        print(f"  [WARN] Only {len(participants)} participants found, need 16")
        return None

    fighters = selectFighters(participants)

    print("  Selected 16 fighters:")
    for f in fighters:
        print(f"    Los {f['los']:2d}  (ID {f['id']:>3s})  "
              f"{f['name']:20s} {f['vorname']:15s}  "
              f"Age {f['alter']}  Weight {f['gewicht']}")

    return fighters
