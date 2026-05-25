# SPDX-FileCopyrightText: 2026 TOP Team Combat Control
# SPDX-License-Identifier: GPL-3.0-or-later
"""End-to-end tournament simulation against a live JudgeFrontend backend.

Voraussetzung:
    Backend läuft auf http://localhost:5001
    DB ist mit mindestens einem Bracket + Fights gefüllt (siehe
    edv/seed_test_tournament.py für einen Mini-Stand).

Liest alle nicht-abgeschlossenen Fights aus /api/matches, sortiert sie nach
(bracketId, round, posInRound) und spielt sie über den WebSocket-Endpoint
/ws durch (SCORE_UPDATE + STATUS_UPDATE finished). p1 gewinnt jeden Kampf
mit 10:0.

Vorher: hartkodierte matchId 1..15, die nie mit echten fight.id übereinstimmten.
"""
from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

import requests
import websockets


BACKEND_HTTP = "http://localhost:5001"
BACKEND_WS = "ws://localhost:5001/ws"


def fetch_matches() -> list[dict[str, Any]]:
    r = requests.get(f"{BACKEND_HTTP}/api/matches", timeout=5)
    r.raise_for_status()
    payload = r.json()
    return payload.get("matches", [])


def select_simulatable(matches: list[dict]) -> list[dict]:
    """Pickbare Fights: status pending, beide Teilnehmer gesetzt, nicht bye."""
    out = []
    for m in matches:
        if m.get("status") in ("finished", "bye"):
            continue
        if not m.get("p1") or not m.get("p2"):
            continue
        if not m["p1"].get("participantId") or not m["p2"].get("participantId"):
            continue
        out.append(m)

    def sort_key(m):
        return (m.get("bracketId") or 0, m.get("round") or 0, m.get("posInRound") or 0)

    out.sort(key=sort_key)
    return out


async def simulate(matches: list[dict]) -> None:
    async with websockets.connect(BACKEND_WS) as ws:
        print(f"Connected. Simulating {len(matches)} fight(s).")
        for m in matches:
            mid = m["matchId"]
            label = m.get("categoryLabel") or m.get("category") or "?"
            r = m.get("round")
            pir = m.get("posInRound")
            print(f"  fight #{mid:>4}  bracket={m.get('bracketId')}  r{r}/p{pir}  {label}")
            await ws.send(json.dumps({
                "type": "SCORE_UPDATE",
                "matchId": mid,
                "playerNum": 1,
                "scoreType": "points",
                "value": 10,
            }))
            await asyncio.sleep(0.05)
            await ws.send(json.dumps({
                "type": "STATUS_UPDATE",
                "matchId": mid,
                "status": "finished",
            }))
            await asyncio.sleep(0.05)
        # Drain broadcasts so the server doesn't see us as flaky.
        await asyncio.sleep(0.5)
    print("Done.")


def main() -> int:
    # Mehrere Pässe: nach Round-0-Sieg propagiert das Backend in Round 1,
    # die im naechsten Pass spielbar wird. Schleife bis nichts mehr offen ist
    # oder MAX_PASSES erreicht (Safety-Netz).
    MAX_PASSES = 5
    passes_done = 0
    total_simulated = 0

    while passes_done < MAX_PASSES:
        try:
            matches = fetch_matches()
        except requests.RequestException as e:
            print(f"Backend nicht erreichbar ({e}). Erst uvicorn auf :5001 starten.",
                  file=sys.stderr)
            return 1

        pickable = select_simulatable(matches)
        if not pickable:
            if passes_done == 0:
                print(f"Keine simulierbaren Fights gefunden (insgesamt {len(matches)} im Backend). "
                      f"Beide Teilnehmer gesetzt und status=pending erwartet.")
                return 1
            print(f"Alle spielbaren Fights durch ({total_simulated} insgesamt, {passes_done} Pass(es)).")
            return 0

        passes_done += 1
        print(f"--- Pass {passes_done} ---")
        asyncio.run(simulate(pickable))
        total_simulated += len(pickable)

    print(f"MAX_PASSES={MAX_PASSES} erreicht, breche ab (insgesamt {total_simulated} Fights).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
