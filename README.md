<!-- SPDX-FileCopyrightText: 2026 TOP Team Combat Control
# SPDX-License-Identifier: CC0-1.0-->

# judgeFrontend

This documentation serves as a guide for the technical presentation of the tournament management software.

---

## 🟢 Presentation Quick Start

Follow these steps for a perfect live demonstration:

1.  **Back-end Service**:
    ```powershell
    # Standard mode (SQLite/PostgreSQL)
    python -m uvicorn main:app --port 5001 --reload
    ```
2.  **Front-end Client**:
    ```powershell
    python -m http.server 8080
    ```
3.  **Access**: Open `http://localhost:8080` in Chrome/Edge.

---

## 🏆 Key Features (Oral Presentation Points)

### 1. Unified Branding Integration
The UI color palette (Shadow Grey & Fresh Sky Blue) is synchronized across all tournament apps (Weigh-in & Judge) for a professional look.

### 2. Mat-Side Efficiency (No Auth)
Staff can quickly select their assigned table from a simple dropdown, avoiding authentication overhead during fast-paced tournament rounds.

### 3. Real-Time Drag & Drop Reordering
Change the fight queue on the fly. Dragging matches automatically updates the order across all connected referee tablets via WebSockets.

### 4. Safety & Health: Automatic Rest Timers
The system enforces a **10-minute rest period** between matches for individual fighters. Referees receive a clear warning if they attempt to start a match prematurely.

### 5. Instant Bracket Context
 referees are never more than one click away from the bracket context. Clicking "Live-Turnierbaum" on any match card reveals the category progress.

---

## Technical Stack
- **API/WS**: FastAPI (Python 3.13)
- **Database**: PostgreSQL (IP: 172.17.192.28) / SQLite Fallback
- **Frontend**: Modern JS (ES6+), Vanilla CSS, WebSocket API
- **UX**: Progressive Web App (PWA) ready

---

Maintained by the Tournament Development Team.
License: GPL-3.0-or-later
Copyright: 2026 TOP Team Combat Control
