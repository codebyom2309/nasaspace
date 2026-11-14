# NASA Space Apps 2026 — Space Mission Design Game: Progress

Project: Mission Architect
Developed by: **Team Turbobits**
Stack: Python Engine + Node.js / Web Client + TiDB Cloud

## Architecture & Style
- Python engine (pure, thoroughly tested) + standalone web app in a *Papers, Please* desk style: pixel art, documents, rubber stamps.
- Icons and UI assets are custom pixel sprites rendered in code via `web/sprites.js`.
- Dual runtime: runs natively with `python server.py` and via Node.js / Google AI Studio container runner (`server.js`).
- Database: TiDB Cloud distributed MySQL compatibility for persistent multiplayer rooms, authenticated accounts, and multi-scenario leaderboards.

## Core Modules Completed
- `engine/catalog.py` — Complete content catalog: missions (Wildfire Watch, Lunar Ice, Mars Climate, Europa Ocean, etc.), orbits per destination, launch vehicles with multi-destination capacity curves, buses, solar arrays, RTGs, batteries, comms, ground stations, flight computers, ADCS, propulsion systems, instruments, and 4 collaborative multiplayer roles.
- `engine/model.py` — Static spacecraft evaluation: rocket equation mass breakdown, power budget (generation, loads, eclipse battery needs), data rate downlinks, radiation survivability, and detailed plain-language issues.
- `engine/simulate.py` — Deterministic monthly seeded simulation (launch failures, radiation hits, solar storms, safe modes, reaction wheel anomalies, micrometeoroids, budget adjustments, discoveries) + Monte Carlo risk assessment.
- `engine/scoring.py` — 950-point comprehensive scoring matrix with grades S/A/B/C/F and post-mission analysis.
- `engine/rooms.py` — Multiplayer room store with role ownership, live field synchronization, and TiDB cloud persistence.
- `engine/db.py` — Thread-safe connection pool for TiDB Cloud with SSL and automatic failover.
- `web/` — Retro pixel-art UI: interactive desk, parts binder, real-time blueprint canvas, review meters, mission launch animation, and *The Orbital Times* newspaper verdict.
