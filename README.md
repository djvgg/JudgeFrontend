# Judo Judge Interface - Real-Time Scoring

Diese Dokumentation dient als technischer Leitfaden für das Turniermanagement-System, optimiert für schnelles Kampfrichter-Management und Echtzeit-Synchronisation.

---

## Schnellstart

1.  Server starten:
    ```powershell
    # Startet API und Frontend auf Port 5001
    python -m uvicorn main:app --host 0.0.0.0 --port 5001 --reload
    ```
2.  Zugriff: Öffnen Sie http://localhost:5001 in Chrome oder Edge.

---

## Kernfunktionen des Systems

### 1. Echtzeit-Synchronisation (WebSockets)
Das Herzstück des Systems ist ein WebSocket-gesteuerter Event-Bus. Jede Punkteaktualisierung, jeder Timer-Start/Stopp und jeder Fortschritt in der Liste wird sofort an alle verbundenen Clients (Tablets, Ippon-Boards und öffentliche Anzeigen) übertragen.

### 2. Ippon-Board Integration
Direkte Unterstützung für die Ippon Board API. Über eine dedizierte Schaltfläche können Kampfrichter Kampfdaten direkt an externe Anzeige-Boards senden, um eine konsistente Datenanzeige für Zuschauer und Kampfgericht zu gewährleisten.

### 3. Sicherheit & Athletenschutz
- Automatische Ruhezeiten: Das System erzwingt eine 10-minütige Pause für Kämpfer zwischen zwei Kämpfen.
- Visuelle Warnungen: Die UI verhindert den Start eines Kampfes, wenn die Erholungsphase noch läuft, und zeigt einen Countdown direkt auf der Kampfkarte an.

### 4. Intelligente Fehlerdiagnose
Anstatt generischer Fehlermeldungen bietet die UI detaillierte Lösungen auf Deutsch:
- Netzwerk: "Bitte WLAN prüfen" bei Verbindungsverlust.
- Server: "Technik-Team kontaktieren" bei Dienstausfall.
- Datenbank: "Datenbank nicht gefunden" bei fehlenden Turnierdaten.
- 100% Offline-Fähigkeit: Automatischer Fallback auf lokale Ressourcen und SQLite-Datenbank.


---

## Technische Architektur

### Frontend (Vanilla Stack)
- HTML5/CSS3: Custom Design-System basierend auf professionellen System-Fonts, optimiert für hohen Kontrast (Dark Mode).
- Native JS (ES6+): Modulare Architektur (State, Network, UI, Scoring) ohne Overhead durch Frameworks für maximale Geschwindigkeit.
- Smarte System-Symbole: Gezielter Einsatz von leichtgewichtigen Symbolen für intuitive Navigation ohne externe Abhängigkeiten.

### Backend (Python & FastAPI)
- FastAPI: Effiziente Verwaltung von asynchronen WebSockets und REST-Endpunkten.
- SQLAlchemy: Robuste ORM-Verwaltung für Turnierdaten (PostgreSQL/SQLite).
- Self-Healing Algorithm: Automatische Korrektur der Turnierbaum-Logik beim Startup.

---

## Projektstruktur
- frontend/index.html: Hauptoberfläche & Scoring Modals.
- frontend/app.js: Kernlogik, WebSocket-Handler und Visualisierung.
- frontend/style.css: Visuelles Design & Layout.
- main.py: API-Server & Echtzeit-Engine.
- backend/: Geschäftslogik für Turnierbäume (KO, Pool, Doppel-KO).

---

Maintained by TOP Team Combat Control 2026
Lizenz: GPL-3.0-or-later | © 2026 Tournament Development Team
