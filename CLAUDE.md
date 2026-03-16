# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**TOP - Team Combat Control**: A real-time tournament management system for combat sports (Judo/Jiu-Jitsu). Judges at different mat tables use a shared web UI to score fights and track bracket progression. The interface is German-language focused.

## Commands

### Development (without Docker)
```bash
# Activate virtual environment (Git Bash / WSL)
source .venv/Scripts/activate

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

### Manual API Triggers
```bash
# Pre-create all LB fight shells for all double-elimination brackets
curl -X POST http://localhost:5001/api/generate-lb

# Manually trigger bracket progression healing
curl -X POST http://localhost:5001/api/heal
```

### CI/CD (GitLab)
The pipeline runs three stages: `lint` (Ruff), `build` (syntax check), `test` (import smoke tests + integration).

## Architecture

### Single-Process, Single-Port Design
FastAPI at port **5001** serves both the REST/WebSocket API and the frontend static files. There is no separate frontend dev server — `frontend/` is mounted as static files at `/`. Access the app at `http://localhost:5001`.

**Critical**: `app.mount("/", StaticFiles(...))` must be the very last line in `main.py`. Any API routes defined after it will be shadowed by the static file handler.

### Real-Time Coordination via WebSocket
`main.py` contains a `ConnectionManager` that broadcasts to all connected clients. Each judge's browser is a WebSocket client.

**Client → Server message types:**
| Type | Description |
|------|-------------|
| `SCORE_UPDATE` | Set score for player 1 or 2 of a fight |
| `STATUS_UPDATE` | Mark a fight as finished/upcoming |
| `REORDER` | Drag-and-drop reordering (updates `fight_number`) |
| `REASSIGN_BRACKET` | Move a bracket to a different mat table |
| `SIGNAL` | Timer signal (start/stop/reset) — relayed to all clients |
| `IPPON_START` | Push fighter info to the external Ippon scoring board |

**Server → Client message types:**
| Type | Description |
|------|-------------|
| `SCORE_SYNC` | Updated match dict for one fight (after score/status change) |
| `REFRESH_LIST` | Tell all clients to re-fetch `/api/matches` |
| `POOL_STANDINGS` | Recalculated standings for a round-robin pool bracket |
| `IPPON_UPDATE` | Live score update relayed from the Ippon board |
| `SIGNAL` | Timer signal relayed from originating client to all others |

### Database
- **PostgreSQL** in production (configured via `.env`)
- Falls back to **SQLite** automatically when no PostgreSQL URL is provided
- Tables auto-created at startup via `init_db()` in `main.py`
- **Alembic** manages versioned migrations (`migrations/versions/`)
- Schema uses German field names: `Gewicht` (weight), `Geschlecht` (gender/sex), etc.

### Tournament Bracket Types
Three tournament formats are implemented, but the `bracket_type` field in the DB has multiple legacy/variant string values — always use `in_(...)` queries when filtering:

| bracket_type values | Meaning |
|---------------------|---------|
| `"ko"`, `"double"`, `"DOUBLE_ELIMINATION"` | Double elimination (Winner Bracket + Loser Bracket) |
| `"POOL"` | Pure round-robin pool (no KO phase) |
| Brackets with pool-phase fights + `bracket_type="double"` | **Double-pool→KO**: two pools, then single-elimination semifinals/final |

The double-pool→KO format is **excluded** from LB generation — it only uses single-elimination KO after pools complete. Detection: check if any `FightModel` with `bracket_phase == "pool"` exists for that bracket.

**Bracket progression details:**
1. **Single Elimination** — binary tree; `next_pos = pos // 2`, slot = p1 if even, p2 if odd
2. **Double Elimination** — Winner Bracket (WB) + Loser Bracket (LB):
   - WB round 0 losers pair with each other in LB round 0: `lb_pos = pos // 2`, slot p1/p2 by parity
   - WB round r≥1 losers inject into LB round `2r-1` at `lb_pos = pos`, always as **p2**
   - LB rounds alternate: **even DB round = reduction** (winner stays in same lane), **odd DB round = injection** (two winners collapse, `next_pos = pos // 2`)
3. **Round-Robin Pools** — all participants fight each other; standings by wins DESC, then Ubw (own score − conceded) DESC
4. **Double-Pool → KO** — two pools (index 0 and 1); on completion: SF1 = A1 vs B2, SF2 = B1 vs A2, Final = SF1 winner vs SF2 winner

Bracket progression is "healed" on startup via `heal_bracket_progressions()` to recover from crashes or external edits. This function is idempotent and loops until stable (handles cascading byes).

### Critical Data Model Gotcha: Participant ID Indirection

`FightModel.participant1_id` and `FightModel.participant2_id` are **GroupParticipant IDs** (`group_participants.id`), NOT `Participant` IDs (`participants.id`).

To resolve a name, you must go through the junction table:
```
fight.participant1_id → GroupParticipantModel.id → GroupParticipantModel.participant_id → ParticipantModel
```

The batch version in `GET /api/matches` does this efficiently with dict lookups. The per-fight version in `get_match_dict()` queries each table individually. Both return the same JSON structure.

### Fight Status Values
- DB stores `"completed"` for finished fights
- API outputs `"finished"` (the `get_match_dict` / `GET /api/matches` normalizes `"completed"` → `"finished"`)
- Frontend uses: `"upcoming"`, `"finished"`, `"bye"` — never `"completed"`

### Table ID Resolution Priority
`table_id` for a fight is resolved in this order:
1. `fight.table_id` (fight-level override)
2. `bracket.mat_id` (bracket-level assignment)
3. `"0"` (fallback — fight is hidden from all table filters)

### Frontend (Vanilla JS SPA)
`frontend/app.js` (~1444 lines) is organized as flat module objects (no framework):

| Object | Role |
|--------|------|
| `Config` | API base URL, WebSocket URL, dev mode detection |
| `State` | All client-side state (activeMatches, scoring state, timers, pool standings) |
| `Network` | `fetch()` and WebSocket management, message handling |
| `UI` | All DOM rendering (fight list, bracket visualization, modals, admin dashboard) |
| `Scoring` | Countdown timer, score buttons, fight start/end flow |
| `DragDrop` | Drag-and-drop reordering of the fight list |

Key UI features:
- **Table/Mat selection** on login (no authentication — just picks a table number or "admin")
- **Admin mode** (`tableNum === 'admin'`): shows a cross-mat dashboard with all brackets grouped by mat column (cols 1–4 + "none")
- **Fight list view** with drag-and-drop reordering; finished/bye fights are hidden
- **Bracket visualization** with DOM-rendered bracket tree; toggle WB/LB with phase buttons
- **Scoring modal** with countdown timer (4:00 default), point buttons, and Ippon board integration
- **Pool standings table** rendered in bracket view for round-robin brackets
- **Victory popup** shown on fight finish

### Ippon Board Integration

The Ippon board is a separate C++/Qt application (`ipponboard_competition-control`) running on a colleague's Linux VM. Its `ApiServer` (port **8080**) is a raw `QTcpServer` that speaks a minimal HTTP subset — not a full HTTP framework.

#### Known network addresses (current setup)
| Machine | IP |
|---------|----|
| This server (judge frontend, Windows laptop) | `172.17.192.190` |
| Colleague's VM (Ippon board) | `172.17.192.168` |

Both are on the same `172.17.192.x` WiFi subnet. The laptop has no ethernet port — WiFi only.

#### Environment variables
| Var | Value | Description |
|-----|-------|-------------|
| `IPPON_HOST` | `172.17.192.168` | IP of the colleague's VM running the Ippon board app |
| `IPPON_PORT` | `8080` | Port the board listens on |
| `OUR_HOST` | `172.17.192.190` | LAN IP of this server as seen by the board — used in the callback URL |

#### Network troubleshooting commands
If the IP changes (e.g. after a reboot), use these to rediscover it:

**On colleague's Linux VM** — find their IP and confirm the board is running:
```bash
ip addr | grep inet          # shows all IPs — look for 172.17.x.x on enp0s3
netstat -tlnp | grep LISTEN  # confirm Ipponboard is on port 8080 (shows as "Ipponboard")
```

**On this Windows machine** — find our own IP:
```
ipconfig                     # look for IPv4 Address under WiFi adapter
```

**Test connectivity from this machine:**
```bash
curl -X POST http://localhost:5001/api/ippon-test
# expects: {"status": 201, "body": "Both fighters created", ...}
```

**Test that colleague can reach this server** (run from colleague's VM):
```bash
curl http://172.17.192.190:5001/api/matches
```

#### Windows Firewall
The board POSTs callbacks to `OUR_HOST:5001`. Windows blocks inbound TCP by default — ping not working from colleague's VM is normal (ICMP is blocked separately from TCP). Add a firewall rule once:
```powershell
# Run in PowerShell as Administrator
netsh advfirewall firewall add rule name="TOP Judge Port 5001" dir=in action=allow protocol=TCP localport=5001
```

#### Outbound: sending fighters to the board
`_send_fighters_to_board(fight_id, session)` in `main.py` is called when a judge sends an `IPPON_START` WebSocket message. It uses `urllib.request` (stdlib, no extra dependency) to POST to `http://<IPPON_HOST>:<IPPON_PORT>/fighters`.

**Request payload (`POST /fighters`):**
```json
{
  "fighter1": { "firstname": "Julia", "lastname": "Schmitt", "gender": "M", "agegroup": "U18", "weightclass": "60kg" },
  "fighter2": { "firstname": "Anna",  "lastname": "Schmidt", "gender": "M", "agegroup": "U18", "weightclass": "60kg" },
  "callback": "http://172.17.192.190:5001/api/ippon-score"
}
```
- `gender` + `agegroup` are concatenated by the board into a category string e.g. `"MU18"`
- The board stores the `callback` URL and uses it for all subsequent score pushes
- On success (HTTP 201) `_current_ippon_fight_id` is set in memory to track which fight is active on the board
- `State.ipponBoardMatchId` is set on the frontend to track which fight is waiting for a result

#### Inbound: score callbacks from the board
`FightDataDispatcher` (C++ `IView` observer) fires `UpdateView()` on every score change. `ApiServer::BroadcastData()` then calls `SendWebhook()`, which POSTs the current fight state to the stored callback URL.

**Callback payload (`POST /api/ippon-score`)** — sent on every score change:
```json
{
  "fighter1": { "name": "J.  Schmitt", "ippon": 0, "wazaari": 1, "yuko": 0, "shido": 0 },
  "fighter2": { "name": "A.  Schmidt", "ippon": 0, "wazaari": 0, "yuko": 0, "shido": 2 },
  "time": "3:12",
  "isGoldenScore": false,
  "winner": "none | fighter1 | fighter2"
}
```
- `name` is abbreviated by the board: `"J.  Schmitt"` (first initial + double space + last name)
- `time` is remaining time (countdown) — converted to elapsed duration: `duration = 240 - remaining_seconds`
- `winner` is `"none"` until an ippon or hansoku-make is scored

`api_ippon_score` in `main.py`:
1. Always broadcasts `IPPON_UPDATE` to all WebSocket clients
2. Only acts on `"winner": "fighter1"` or `"fighter2"` — ignored if `_current_ippon_fight_id` is `None` (board button was never pressed)
3. On winner: sets winner score to 10, stores elapsed duration, marks fight `"finished"`, sets `winner_id`, runs bracket advancement, broadcasts `SCORE_SYNC` + `REFRESH_LIST`, clears `_current_ippon_fight_id`

Frontend (`IPPON_UPDATE` handler in `app.js`):
- Only fires if `State.ipponBoardMatchId` is set (i.e. IPPON BOARD button was pressed for this fight)
- Restores `State.currentScoringMatch` even if the scoring modal was closed
- Calls `winByDecision(1 or 2)` → shows victory popup with the real fighter name
- Clears `State.ipponBoardMatchId` after winner is handled

#### Test endpoint
```bash
curl -X POST http://localhost:5001/api/ippon-test
```
Sends hardcoded `Test Eins` / `Test Zwei` fighters to verify network connectivity before using a real fight. Does not set `_current_ippon_fight_id`.

#### C++ board internals (read-only reference)
| Class | Role |
|-------|------|
| `ApiServer` | `QTcpServer` on port 8080; parses raw HTTP, routes `POST /fighters`, manages TCP client list, sends webhooks via `QNetworkAccessManager` |
| `ApiEndpoints` | Handles `POST /fighters`; parses JSON, creates `Fighter` objects, passes them to `FighterManager`, stores callback URL in `ApiServer::m_callbackUrl` |
| `FightDataDispatcher` | `IView` observer; called on every score change; builds score JSON and emits `dataUpdated` signal → triggers `ApiServer::BroadcastData` |
| `Fighter` | Data model: `first_name`, `last_name`, `weight`, `category` (gender+agegroup concatenated) |

### XLSX Import/Export
- `backend/xlsx_handler.py`: Parses participant lists from `.xlsx` files, auto-detecting German and English column headers. Groups participants by gender, age class, weight class.
- `backend/excel_generator.py`: Generates bracket template `.xlsx` files (8/16/32-person) from `excel_templates/`.

## Key Files

| File | Role |
|------|------|
| `main.py` | FastAPI app (~1015 lines), WebSocket manager, all API routes, bracket progression logic, startup/heal |
| `frontend/app.js` | Entire frontend logic (~1444 lines): state, rendering, WS client |
| `backend/bracket_manager.py` | `BracketManager` class: winner/loser coordinate calculation, pool standings |
| `backend/database.py` | SQLAlchemy models (`Fight`, `Participant`, `Bracket`, `Group`, `GroupParticipant`) |
| `backend/xlsx_handler.py` | XLSX import with multilingual column detection |
| `backend/bracket_data.py` | Static bracket shape data |
| `migrations/versions/` | Alembic schema migrations |

## API Routes

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/matches` | All fights as match dicts (batch-optimized) |
| `POST` | `/api/heal` | Manually trigger bracket progression healing |
| `POST` | `/api/generate-lb` | Pre-create all LB fight shells for double-elim brackets |
| `POST` | `/api/ippon-score` | Receive live score callback from Ippon board; auto-finishes fight on winner |
| `POST` | `/api/ippon-test` | Send hardcoded test fighters to board to verify connectivity |
| `WS` | `/ws` | WebSocket endpoint for all real-time coordination |

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
IPPON_HOST=<ippon_board_ip>
IPPON_PORT=8080
OUR_HOST=<this_server_ip_as_seen_by_ippon>
```

On Windows with WSL2, the PostgreSQL host is typically the WSL2 network IP (e.g., `172.17.x.x`). For Docker, `host.docker.internal` is used. `OUR_HOST` must be this server's LAN IP (not localhost) so the Ippon board can reach the callback endpoint.

## Active Branch Context

- **`judge-interface`** (current): Active development branch for the judge-facing UI
- **`main`**: Primary stable branch
- **`bracketGenerator`**: Bracket generation features
- Fight status values: `"upcoming"`, `"finished"`, `"bye"`, `"completed"` (last one is DB-internal only)
- Fight phase values: `"wb"` (winner bracket), `"lb"` (loser bracket), `"pool"` (round-robin pool)
