# SPDX-FileCopyrightText: 2026 TOP Team Combat Control
#
# SPDX-License-Identifier: CC0-1.0

"""
Central data store for the 16-player bracket.
Fighters list is populated dynamically from XLSX upload.
"""

eventInfo = {
    "title": "Doppel-KO-System / 16 Teilnehmer",
    "weightClass": "",
    "art": "Freistil",
    "ort": "",
    "datum": "",
    "sportlLtg": "",
}

# Populated from XLSX via xlsxHandler.processXlsx()
fighters = []

# Maps bracket position (0-15) to Los number (1-16)
seedingOrder = [1, 9, 5, 13, 3, 11, 7, 15, 2, 10, 6, 14, 4, 12, 8, 16]

totalFights = 15  # 8 (R1) + 4 (R2) + 2 (QF) + 1 (SF)
