# JudgeFrontend - Tournament Bracket Generator

Desktop GUI application for generating tournament brackets from participant Excel files.

## Features

- **One-Click Workflow**: Load participant .xlsx file → Auto-generate brackets → Auto-open results
- **Multi-Size Brackets**: Automatically generates 8/16/32-person double-elimination brackets
- **Smart Grouping**: Groups participants by gender, age group, and weight class
- **Clean Interface**: Dark-themed GUI matching judgefrontend styling
- **Standalone Executable**: Can be built as a portable .exe file

## Quick Start

### Running the GUI

```bash
python main.py
```

### Running the CLI Script

```bash
python scripts/generate_brackets.py
```

## Project Structure

```
C:\Users\Admin\PycharmProjects\Test\
├── main.py                          # GUI entry point
├── requirements.txt                 # Dependencies
├── README.md                        # This file
│
├── excel_files/                     # Bracket templates (required)
│   ├── 8_Doppel-KO-System.xls
│   ├── 16_Doppel-KO-System.xls
│   └── 32_mod.Doppel-KO-System.xls
│
├── src/                             # Core modules
│   ├── __init__.py
│   ├── bracketData.py              # Age groups, weight classes, seeding
│   ├── xlsxHandler.py              # XLSX parsing and grouping
│   └── excelGenerator.py           # Bracket generation logic
│
├── scripts/                         # Utility scripts
│   ├── buildExe.py                 # Build executable
│   └── generate_brackets.py        # CLI bracket generator
│
└── generated_brackets/              # Output folder (auto-created)
    └── (bracket .xls files)
```

## Installation

1. **Clone or download this repository**

2. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```

3. **Run the GUI**:
   ```bash
   python main.py
   ```

## Dependencies

- `pandas` - Excel file reading
- `openpyxl` - Modern Excel (.xlsx) support
- `xlrd` - Legacy Excel (.xls) reading
- `xlwt` - Excel writing
- `xlutils` - Excel utilities
- `pyinstaller` - Executable building

## Usage

### GUI Application

1. Launch the application: `python main.py`
2. Click "Teilnehmerliste laden & Brackets generieren"
3. Select your participant .xlsx file
4. Brackets are automatically generated and the output folder opens
5. Find your .xls bracket files in `generated_brackets/`

### CLI Script

Edit `scripts/generate_brackets.py` to point to your participant file:

```python
participant_file = r"C:\path\to\your\participant_file.xlsx"
```

Then run:

```bash
python scripts/generate_brackets.py
```

### Building Executable

To create a standalone .exe file:

```bash
python scripts/buildExe.py
```

The executable will be created in `dist/JudgeFrontend.exe`

## Input File Format

The participant .xlsx file should contain these columns (names are auto-detected):

**Required**:
- **Weight** (Gewicht, Gewichtsklasse, Gewicht (kg), Weight)
- **Name** (Name, Nachname, Surname) OR **Vorname** (Vorname, First Name)

**Optional**:
- **ID** (Startnummer, ID, Teilnehmer-ID, Nr)
- **Age** (Alter, Age, Jahrgang)
- **Gender** (Geschlecht, Gender, M/W)
- **Club** (Verein, Club, Verband)

### Example

| Startnummer | Name | Vorname | Alter | Gewicht (kg) | Verein | Geschlecht |
|-------------|------|---------|-------|--------------|--------|------------|
| 1 | Mueller | Hans | 25 | 75 | TSV Berlin | M |
| 2 | Schmidt | Anna | 22 | 63 | JC Hamburg | W |

## Grouping Logic

Participants are automatically grouped by:

1. **Gender**: Male (M), Female (W), or Mixed (Gemischt)
2. **Age Group** (if age data available): U10, U12, U13, U14, U16, U18, Senioren (18+)
3. **Weight Class**: Based on breakpoints [60, 66, 73, 81, 90, 100] kg

Groups with fewer than 2 participants are skipped.

## Output Files

Generated bracket files are named:

```
{size}_Doppel-KO-System_{Gender}_{AgeGroup}_{WeightClass}.xls
```

Examples:
- `8_Doppel-KO-System_Maennlich_Senioren_73-81kg.xls`
- `16_Doppel-KO-System_Weiblich_U18_60-66kg.xls`
- `32_mod.Doppel-KO-System_Maennlich_Senioren_90-100kg.xls`

## Code Style

- **Naming**: lowerCamelCase for variables, functions, and parameters
- **Dynamic Paths**: No hardcoded file paths (except `excel_files/` templates)
- **Modular Design**: Clean separation between XLSX handling, bracket generation, and GUI

## License

SPDX-FileCopyrightText: 2026 TOP Team Combat Control
SPDX-License-Identifier: GPL-3.0-or-later

## Troubleshooting

### "Template not found" error

Make sure the `excel_files/` folder contains all three template files:
- `8_Doppel-KO-System.xls`
- `16_Doppel-KO-System.xls`
- `32_mod.Doppel-KO-System.xls`

### Import errors

Make sure you're in the project root directory when running scripts:

```bash
cd C:\Users\Admin\PycharmProjects\Test
python main.py
```

### Executable doesn't work

1. Check that PyInstaller is installed: `pip install pyinstaller`
2. Ensure all dependencies are installed: `pip install -r requirements.txt`
3. Build with: `python scripts/buildExe.py`

## Contributing

This project is maintained by TOP Team Combat Control.
Author: Noah Beisert <@inf4245@hs-worms.de>
