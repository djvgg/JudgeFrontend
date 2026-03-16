# SPDX-FileCopyrightText: 2026 TOP Team Combat Control
#
# SPDX-License-Identifier: GPL-3.0-or-later

import re
from collections import defaultdict

import pandas as pd

from backend.bracket_data import ageGroups, getNextPowerOf2, weightBreakpoints

columnCandidates = {
    "id": ["Startnummer", "ID", "Id", "Teilnehmer-ID", "TeilnehmerID", "Nr", "Nummer"],
    "name": ["Name", "Nachname", "Surname"],
    "vorname": ["Vorname", "First Name", "Firstname"],
    "age": ["Alter", "Age", "Jahrgang"],
    "weight": ["Gewichtsklasse", "Gewicht (kg)", "Gewicht", "Gewicht(kg)", "Weight", "Weight (kg)"],
    "club": ["Verein", "Club", "Verband"],
    "gender": ["Geschlecht", "Gender", "M/W", "Sex", "m/w", "Ges"],
}


def findColumn(df, candidates):
    lowerMap = {c.lower().strip(): c for c in df.columns}
    for candidate in candidates:
        if candidate.lower() in lowerMap:
            return lowerMap[candidate.lower()]
    return None


def detectColumns(df):
    detected = {}
    for role, candidates in columnCandidates.items():
        detected[role] = findColumn(df, candidates)
    return detected


def parseGender(value):
    val = str(value).strip().upper()
    if val in ("M", "MALE", "MAENNLICH", "MÄNNLICH", "H", "HERREN", "JUNGEN", "J"):
        return "M"
    if val in ("W", "F", "FEMALE", "WEIBLICH", "D", "DAMEN", "MÄDCHEN", "MAEDCHEN"):
        return "W"
    return "Gemischt"


def getAgeGroup(age):
    if age is None:
        return None
    for label, minAge, maxAge in ageGroups:
        if minAge <= age <= maxAge:
            return label
    return "Sonstige"


def getWeightClass(weight):
    breakpoints = weightBreakpoints
    if weight <= breakpoints[0]:
        return f"bis {breakpoints[0]}kg"
    for i in range(len(breakpoints) - 1):
        if breakpoints[i] < weight <= breakpoints[i + 1]:
            return f"{breakpoints[i]}-{breakpoints[i + 1]}kg"
    return f"{breakpoints[-1]}kg+"


def readXlsx(filepath):
    try:
        return pd.read_excel(filepath)
    except Exception as e:
        print(f"[ERROR] Could not read XLSX: {e}")
        return None


def parseParticipants(df):
    """
    Parses DataFrame rows into participant dicts.
    Supports both combined Name column and separate Vorname/Nachname columns.
    Age column is optional - if missing, age grouping is skipped.
    """
    cols = detectColumns(df)

    if not cols["weight"]:
        print("[ERROR] Required column not found: weight")
        return []

    if not cols["name"] and not cols["vorname"]:
        print("[ERROR] No name column found (need Name, Nachname, or Vorname)")
        return []

    hasAge = cols["age"] is not None

    print(
        f"  Detected columns -> "
        f"ID: {cols['id']}, Nachname: {cols['name']}, Vorname: {cols['vorname']}, "
        f"Alter: {cols['age']}, Gewicht: {cols['weight']}, Verein: {cols['club']}, Geschlecht: {cols['gender']}"
    )

    participants = []

    for _, row in df.iterrows():
        age = None
        if hasAge:
            try:
                age = int(row.get(cols["age"], 0))
            except (ValueError, TypeError):
                age = None

        try:
            weight = float(row.get(cols["weight"], 0))
        except (ValueError, TypeError):
            continue

        if cols["gender"]:
            gender = parseGender(row.get(cols["gender"], ""))
        else:
            gender = "Gemischt"

        if cols["vorname"] and cols["name"]:
            vorname = str(row.get(cols["vorname"], "")).strip()
            nachname = str(row.get(cols["name"], "")).strip()
        elif cols["name"]:
            fullName = str(row.get(cols["name"], "")).strip()
            parts = fullName.split()
            vorname = parts[0] if len(parts) >= 2 else ""
            nachname = " ".join(parts[1:]) if len(parts) >= 2 else fullName
        else:
            vorname = str(row.get(cols["vorname"], "")).strip()
            nachname = ""

        rawId = str(row.get(cols["id"], "")) if cols["id"] else ""
        numericId = re.sub(r"[^0-9]", "", rawId)

        club = str(row.get(cols["club"], "")) if cols["club"] else ""

        participants.append(
            {
                "id": numericId,
                "name": nachname,
                "vorname": vorname,
                "alter": age,
                "gewicht": weight,
                "verein": club,
                "geschlecht": gender,
            }
        )

    return participants


def groupParticipants(participants, minAge=18):
    """
    Groups participants by gender -> age group -> weight class.
    Participants younger than minAge are filtered out.
    Groups with <2 fighters are skipped.
    """
    groups = defaultdict(list)
    skippedAge = 0

    for participant in participants:
        age = participant["alter"]
        if age is not None and age < minAge:
            skippedAge += 1
            continue

        gender = participant["geschlecht"]
        ageGroup = getAgeGroup(age) if age is not None else None
        weightClass = getWeightClass(participant["gewicht"])

        key = (gender, ageGroup, weightClass)
        groups[key].append(participant)

    if skippedAge:
        print(f"  [FILTER] Skipped {skippedAge} participant(s) under age {minAge}")

    result = []
    for (gender, ageGroup, weightClass), fighters in sorted(
        groups.items(), key=lambda kv: (kv[0][0] or "", kv[0][1] or "", kv[0][2])
    ):
        if len(fighters) < 2:
            groupLabel = f"{gender} / {ageGroup or '-'} / {weightClass}"
            print(f"  [SKIP] {groupLabel}: only {len(fighters)} fighter(s), need at least 2")
            continue

        fightersSorted = sorted(fighters, key=lambda f: f["gewicht"])
        for i, fighter in enumerate(fightersSorted, start=1):
            fighter["los"] = i

        genderLabel = {"M": "Maennlich", "W": "Weiblich"}.get(gender, gender)

        if ageGroup:
            label = f"{genderLabel} / {ageGroup} / {weightClass}"
        else:
            label = f"{genderLabel} / {weightClass}"

        result.append(
            {
                "label": label,
                "gender": gender,
                "ageGroup": ageGroup,
                "weightClass": weightClass,
                "fighters": fightersSorted,
            }
        )

    return result


def processXlsx(filepath):
    """Main entry point: reads XLSX -> parses participants -> groups by gender/age/weight"""
    df = readXlsx(filepath)
    if df is None:
        return None

    print(f"  XLSX columns: {list(df.columns)}")

    participants = parseParticipants(df)
    print(f"  Total participants: {len(participants)}")

    if not participants:
        print("  [ERROR] No valid participants found")
        return None

    groups = groupParticipants(participants)
    print(f"  Generated {len(groups)} bracket group(s):")
    for group in groups:
        numFighters = len(group["fighters"])
        bracketSize = getNextPowerOf2(numFighters)
        print(f"    {group['label']}: {numFighters} fighters -> {bracketSize}-er bracket")

    if not groups:
        print("  [ERROR] No groups with 2+ fighters")
        return None

    return groups
