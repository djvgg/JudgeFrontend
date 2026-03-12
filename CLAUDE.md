# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**TOP - Team Combat Control**: A real-time tournament management system for combat sports (likely Judo/Jiu-Jitsu). Judges at different mat tables use a shared web UI to score fights and track bracket progression. The interface is German-language focused.

## Commands

### Development (without Docker)
```bash
# Start backend (serves frontend too at http://localhost:5001)
python -m uvicorn main:app --host 0.0.0.0 --port 5001 --reload

# Run Ruff linter
ruff check .

# Run Alembic migration
alembic upgrade head
```

### Docker-based Development
```bash
make up        # Start app container
make down      # Stop containers
make logs      # Tail container logs
make shell     # Open bash inside container
make migrate   # Run Alembic migrations inside container
make lint      # Run Ruff linter inside container
```

### Database Utilities
```bash
python scripts/check_db.py          # Test DB connectivity
python scripts/diagnose_db.py       # Inspect DB schema
python scripts/seed_data.py         # Load demo participants
python scripts/seed_demo_brackets.py # Create demo tournaments
python scripts/reset_fights.py      # Clear all fight data
python scripts/generate_brackets.py <file.xlsx>  # Import from XLSX
```

### CI/CD (GitLab)
The pipeline runs three stages: `lint` (Ruff), `build` (syntax check), `test` (import smoke tests + integration).

## Architecture

### Single-Process, Single-Port Design
FastAPI at port **5001** serves both the REST/WebSocket API and the frontend static files. There is no separate frontend dev server — `frontend/` is mounted as static files at `/`. Access the app at `http://localhost:5001`.

### Real-Time Coordination via WebSocket
`main.py` contains a `ConnectionManager` that broadcasts to all connected clients. Each judge's browser is a WebSocket client. Key message types: `SCORE_UPDATE`, `STATUS_UPDATE`, `REORDER`, `REASSIGN_BRACKET`, `SIGNAL`. When one judge updates a score, all other connected judges see it immediately.

### Database
- **PostgreSQL** in production (configured via `.env`)
- Falls back to **SQLite** automatically when no PostgreSQL URL is provided
- Tables auto-created at startup via `init_db()` in `main.py`
- **Alembic** manages versioned migrations (`migrations/versions/`)
- Schema uses German field names: `Gewicht` (weight), `Geschlecht` (gender/sex), etc.

### Tournament Bracket Types
Three tournament formats are implemented in `backend/bracket_manager.py` and `backend/bracket_data.py`:
1. **Single Elimination** — binary tree, winner advances
2. **Double Elimination** — Winner Bracket (WB) + Loser Bracket (LB). Losers from WB enter LB; LB has alternating injection/reduction rounds
3. **Round-Robin Pools** — standings-based with pool index grouping

Bracket progression is "healed" on startup via `heal_bracket_progressions()` to recover from crashes or external edits.

### Frontend (Vanilla JS SPA)
`frontend/app.js` (~1300 lines) manages all client state, UI rendering, and WebSocket communication. No JS framework. Key areas:
- **Table/Mat selection** on login (no authentication)
- **Fight list view** with drag-and-drop reordering
- **Bracket visualization** with SVG-like DOM rendering
- **Scoring modal** with countdown timer and point buttons

### XLSX Import/Export
- `backend/xlsx_handler.py`: Parses participant lists from `.xlsx` files, auto-detecting German and English column headers. Groups participants by gender, age class, weight class.
- `backend/excel_generator.py`: Generates bracket template `.xlsx` files (8/16/32-person) from `excel_templates/`.

## Key Files

| File | Role |
|------|------|
| `main.py` | FastAPI app, WebSocket manager, all API routes, startup logic |
| `frontend/app.js` | Entire frontend logic (state, rendering, WS client) |
| `backend/bracket_manager.py` | Fight advancement logic for all bracket types |
| `backend/database.py` | SQLAlchemy models (`Fight`, `Participant`, `Bracket`, `Group`) |
| `backend/xlsx_handler.py` | XLSX import with multilingual column detection |
| `migrations/versions/` | Alembic schema migrations |

## Code Style

- **Linter**: Ruff (config in `pyproject.toml`), line length 100, Python 3.13+
- **Naming**: lowerCamelCase allowed alongside snake_case (custom Ruff rule)
- **License headers**: All files begin with SPDX headers (GPL-3.0-or-later, TOP Team)
- **Language**: UI and comments are primarily German; docstrings may be English

## Environment Configuration

Copy `.env` and adjust:
```
DB_HOST=<postgres_host>
DB_PORT=5432
DB_NAME=mydatabase
DB_USER=myuser
DB_PASSWORD=mypassword
DATABASE_URL=postgresql://myuser:mypassword@<host>:5432/mydatabase
```

On Windows with WSL2, the PostgreSQL host is typically the WSL2 network IP (e.g., `172.17.x.x`). For Docker, `host.docker.internal` is used.

## Active Branch Context

- **`judge-interface`** (current): Active development branch for the judge-facing UI
- **`main`**: Primary stable branch
- **`bracketGenerator`**: Bracket generation features
- Fight status values: `"upcoming"`, `"finished"`, `"bye"`, `"completed"`
- Fight phase values: `"wb"` (winner bracket), `"lb"` (loser bracket)
