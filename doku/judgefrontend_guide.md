# Judge Interface - Dokumentation (TOP 2026)

Diese Dokumentation bietet einen Überblick über das Turniermanagement-System, den Build-Prozess und die Konfiguration.

## Inhaltsverzeichnis

**Systemübersicht**
- [Technische Architektur](#technische-architektur)
- [Kernfunktionen](#kernfunktionen)

**Linux & CI/CD**
- [GitLab CI Pipeline](#gitlab-ci-pipeline)
- [Deployment mit Docker](#deployment-mit-docker)
- [Interaktives Makefile](#interaktives-makefile)

**Windows (Entwicklung)**
- [Voraussetzungen](#voraussetzungen)
- [Lokale Installation](#lokale-installation)
- [Starten der Anwendung](#starten-der-anwendung)

**Konfiguration & API**
- [Umgebungsvariablen (.env)](#umgebungsvariablen-env)
- [Ippon-Board Integration](#ippon-board-integration)
- [Datenbank-Management](#datenbank-management)

---

## Systemübersicht

### Technische Architektur
- **Backend**: Python 3.13 mit FastAPI für asynchrone WebSockets und REST-Schnittstellen.
- **Frontend**: Vanilla JavaScript (ES6+), HTML5 und CSS3. Kein Framework-Overhead für maximale Performance auf Tablets.
- **Echtzeit**: WebSocket-basierter Event-Bus für sofortige Synchronisation zwischen Kampfrichter, Ippon-Board und Zuschauer-Monitor.

### Kernfunktionen
- **Warteliste**: Automatisches Management von anstehenden Kämpfen (Active vs. Waiting).
- **Auto-Queue**: Nahtloser Übergang zwischen Kämpfen ("Spotify-Style") ohne UI-Flackern.
- **Athletenschutz**: Erzwingung von 10-Minuten-Ruhezeiten mit visuellen Warnungen.

---

## Linux & CI/CD

### GitLab CI Pipeline
Die Datei `.gitlab-ci.yml` automatisiert die Qualitätssicherung bei jedem Push:

**Prozess:**
1. **Linting**: Prüfung der Code-Qualität mit `ruff`.
2. **Build**: Kompilierungstest aller Python-Module zur Syntax-Validierung.
3. **Unit Tests**: Validierung der Turnierbaum-Mathematik und Daten-Handler.

### Deployment mit Docker
Für den produktiven Einsatz wird Docker empfohlen:
```bash
docker compose up -d
```
Das System startet automatisch die API auf Port 5001 und verbindet sich mit der lokal konfigurierten Datenbank.

### Interaktives Makefile
Im Hauptverzeichnis stehen folgende Befehle zur Verfügung:

| Befehl         | Beschreibung                                |
| -------------- | ------------------------------------------- |
| `make up`      | Startet Container im Hintergrund            |
| `make down`    | Stoppt das System                           |
| `make build`   | Baut das Docker-Image neu (Clean Build)     |
| `make logs`    | Zeigt Live-Server-Protokolle                |
| `make migrate` | Führt DB-Migrationen im Container aus       |
| `make dev`     | Startet den Server lokal für Entwicklung    |

---

## Windows (Entwicklung)

### Voraussetzungen

| Software           | Zweck                                         |
| ------------------ | --------------------------------------------- |
| Python 3.10+       | Laufzeitumgebung (Empfohlen: 3.13)            |
| Git                | Abrufen des Quellcodes                        |
| Pip                | Paketverwaltung                               |

### Lokale Installation
1. Repository klonen.
2. Virtuelle Umgebung erstellen und aktivieren:
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\activate
   ```
3. Pakete installieren:
   ```powershell
   pip install -r requirements.txt
   ```

### Starten der Anwendung
```powershell
python -m uvicorn main:app --host 0.0.0.0 --port 5001 --reload
```
Gehen Sie dann im Browser auf `http://localhost:5001`.

---

## Konfiguration & API

### Umgebungsvariablen (.env)
Kopieren Sie `.env.example` nach `.env`, um das Projekt zu konfigurieren:

| Variable           | Standard                          | Beschreibung                             |
| ------------------ | --------------------------------- | ---------------------------------------- |
| `DATABASE_URL`     | `sqlite:///local_tournament.db`   | Datenbankverbindung                      |
| `PORT`             | `5001`                            | Port der Anwendung                       |
| `IPPONBOARD_IP`    | `localhost`                       | IP für Ippon-Board Anbindung             |

### Ippon-Board Integration
Über die Schaltfläche "IPPON BOARD" in der Scoring-Maske werden Kampfdaten an externe Boards übertragen. Dies erfolgt via HTTP POST an die konfigurierte IP/Port Adresse.

### Datenbank-Management
Schema-Änderungen werden mit Alembic verwaltet:
```powershell
# Neue Migration anwenden
alembic upgrade head
```
Die Standard-Datenbank ist eine SQLite-Datei (`local_tournament.db`) im Projektordner.

---
*Stand: März 2026 | TOP Team Combat Control*
