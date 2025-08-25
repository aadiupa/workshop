# Tech Quiz Arena (facilitator pack)

A fast, no-infra team quiz for ~30 tech-savvy colleagues. Web-based, runs locally.
You control the round (next/reveal), teams answer on their devices, and a live scoreboard updates.

## Quick start
1) Ensure Python 3.10+ is installed.
2) Create a venv and install dependencies:
   ```bash
   python -m venv .venv
   source .venv/bin/activate    # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```
3) Run the app (port 5001 by default):
   ```bash
   python -m app
   # or
   PORT=5001 python -m app
   ```
4) Open http://localhost:5001. Share this with participants (or expose via `cloudflared tunnel --url http://localhost:5001`).

## Admin token (to protect resets)
- Optional but recommended in remote sessions.
- Set an env var before starting:
  ```bash
  export ADMIN_TOKEN="supersecret"
  python -m app
  ```
- The Facilitator page will show a small token input. Enter it once; it will be submitted with reset actions.

**Reset protection rules:**
- If `ADMIN_TOKEN` is set: reset actions require the correct token.
- If not set: reset actions are allowed **only from localhost** (127.0.0.1 or ::1).
- Blocked attempts get a playful 403 message.

## Flow (45–60 min)
- 0–5 min: Intro + split into 6 teams (Alpha, Bravo, Charlie, Delta, Echo, Foxtrot).
- 5–10 min: Each team opens their team link from the Teams page.
- 10–45 min: Facilitator controls next/reveal; teams answer each question.
- 45–60 min: Leaderboard + debrief (explanations shown on reveal).

## Features
- Single-answer, multi-select, and short text answers (regex-matched).
- Manual control by facilitator: Next / Reveal & Score / Previous.
- Optional negative marking toggle (-0.5 for wrong non-empty submissions).
- Shuffle questions, reset round, and live scoreboard.
- Optional timer display (client-side countdown).
