# JudgeFrontend – Chat Instructions / Project Context

## Project Overview
JudgeFrontend is a bracket generator for Judo / combat sports tournaments. It reads a participant list from XLSX, filters by age & weight, selects 16 fighters, and exports a printable single-elimination bracket as PDF (A3 landscape). The GUI is built with Tkinter.

## Tech Stack

| Purpose              | Library       | Notes                        |
|----------------------|---------------|------------------------------|
| XLSX reading         | pandas        | Auto-detects column names    |
| PDF bracket export   | fpdf2         | A3 landscape, elbow connectors |
| Excel file support   | openpyxl      | Engine for pandas read_excel |
| GUI                  | tkinter       | Built-in                     |
| Packaging            | pyinstaller   | `build_exe.py` → `dist/`    |

## Environment
- **Python:** 3.13 (Microsoft Store)
- **OS:** Windows 11

## Project Files
```
judgefrontend/
├── main.py              # Tkinter GUI (load XLSX → export PDF)
├── bracket_data.py      # Shared state: eventInfo, fighters, seedingOrder
├── bracket_pdf.py       # PDF generator (BracketPdf class)
├── xlsx_handler.py      # XLSX reader, filter & select 16 fighters
├── build_exe.py         # PyInstaller build script
├── JudgeFrontend.spec   # PyInstaller spec
├── requirements.txt     # pip dependencies
├── README.md            # Project readme
├── chatinstructions.md  # This file
└── .git/                # Version control
```

## Naming Convention
- **lowerCamelCase** for variables, functions, parameters
- Module-level shared state in `bracket_data.py`: `eventInfo`, `fighters`, `seedingOrder`

## How It Works

### Data Flow
1. User clicks "Teilnehmerliste laden" → file dialog opens
2. `xlsx_handler.processXlsx(filepath)` reads the XLSX
3. Columns auto-detected (Name, Alter, Gewicht (kg), ID, Verein)
4. Filtered: age ≤ 17, weight 30–70 kg
5. Sorted by weight, top 16 selected, assigned Los 1–16
6. Fighter ID extracted from XLSX ID column (e.g. `JUD006` → `006`)
7. Stored in `bracket_data.fighters`

### PDF Bracket
- **Round 1:** 8 fights, 16 fighters with `(ID) Nachname, Vorname` labels
- **Round 2 → QF → SF:** Empty boxes (for handwriting results)
- **Winner:** Yellow "SIEGER" box
- **Results table:** 1.–8. Platz with empty fields
- **Connectors:** L-shaped elbows connecting outer fighters between rounds
- **Seeding:** `[1, 9, 5, 13, 3, 11, 7, 15, 2, 10, 6, 14, 4, 12, 8, 16]`

### Key Functions
| Function | File | Purpose |
|----------|------|---------|
| `runGui()` | main.py | Main window with 2 action buttons |
| `processXlsx(path)` | xlsx_handler.py | Full pipeline: read → filter → select 16 |
| `filterParticipants(df)` | xlsx_handler.py | Age/weight filter with auto column detection |
| `generateBracketPdf(path, eventInfo, fighters)` | bracket_pdf.py | Renders the full A3 bracket |
| `_drawNextRound(prevX, prevPos, count)` | bracket_pdf.py | Reusable round drawer with elbows |
