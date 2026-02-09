<!--
SPDX-FileCopyrightText: 2026 TOP Team Combat Control

SPDX-License-Identifier: GPL-3.0-or-later-->

# JudgeFrontend

16-player single elimination bracket generator for Judo tournaments.  
Reads participants from XLSX → filters by age & weight → exports a printable A3 PDF bracket.

## Quick Start

```bash
pip install -r requirements.txt
python main.py
```

1. Click **"Teilnehmerliste laden"** → select your `.xlsx` file
2. Click **"Bracket PDF exportieren"** → save the PDF
3. Print on A3 and fill in results by hand

## XLSX Format

The app auto-detects columns. Expected:

| Column       | Example        | Required |
|--------------|----------------|----------|
| ID           | JUD006         | optional |
| Name         | Paul Schneider | ✓        |
| Alter        | 14             | ✓        |
| Gewicht (kg) | 56             | ✓        |
| Verein       | Judo Club XY   | optional |

**Filter:** age ≤ 17 and weight 30–70 kg → top 16 by weight selected.

## Project Structure

```
main.py            – Tkinter GUI
bracket_pdf.py     – PDF bracket generator (fpdf2, A3 landscape)
bracket_data.py    – Shared state (eventInfo, fighters, seedingOrder)
xlsx_handler.py    – XLSX reader & participant filter (pandas)
build_exe.py       – PyInstaller packaging script
requirements.txt   – Python dependencies
```

## Build Executable

```bash
python build_exe.py
# → dist/JudgeFrontend.exe
```
