# Fix‑It Chaos Lab (facilitator pack)

A fast, no‑infrastructure way to run a chaos‑style game for ~30 people. 
Attendees get a link, pick a team, and fix broken “resources” by submitting JSON patches.
You get a live scoreboard.

## What this is (and isn’t)
- ✅ A simulation: realistic *enough* Kubernetes/DevOps break‑fix puzzles without needing a real cluster.
- ✅ Works on your laptop. Optional: expose it publicly with Cloudflare Tunnel in 1 command.
- ❌ Not a real shared K8s cluster (use this when you want a low‑prep, highly interactive session).

## Quick start (facilitator)
1) Make sure you have Python 3.10+ installed.
2) In a terminal here, create a venv and install deps:
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```
3) Run the app (defaults to port 5001; set `PORT` to change):
   ```bash
   python -m app                # or: PORT=8000 python -m app
   ```
4) Open http://localhost:5001  (replace 5001 if you set a different `PORT`)

### Optional: share a public link
If you want remote folks to join, expose it with Cloudflare Tunnel:
```bash
# 1) Install cloudflared (one-time) from https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/install-and-setup/installation/
cloudflared --version

# 2) Run a quick ad-hoc tunnel
cloudflared tunnel --url http://localhost:5001   # adjust if you changed `PORT`
```
It prints a public URL (e.g., https://something.trycloudflare.com). Share that URL with attendees.

## Run-of-show (45–60 min)
- 0–5 min: Intro + split into 6 teams (Alpha, Bravo, Charlie, Delta, Echo, Foxtrot).
- 5–10 min: Everyone opens the link → clicks their team.
- 10–50 min: Teams work through 6 challenges. Each solved challenge updates the scoreboard live.
- 50–60 min: Debrief: “Which bug bit you IRL?” and share mitigation checklists.

## Challenges (simulated but realistic)
Each challenge is a small “infra state” that’s broken. The UI gives a short brief and a *hint* of the correct patch. 
Teams submit a JSON payload that *represents* the fix you’d make (e.g., a Service selector or a liveness probe change). 
The server validates the intent — not exact formatting — and marks it solved.

- 01 Service selector mismatch → fix the selector (`spec.selector.app=web`)
- 02 Missing ConfigMap key used by the app → add `data.WELCOME` with a non-empty value
- 03 Bad liveness probe path → set `containers[?name=web].livenessProbe.httpGet.path="/"`
- 04 OOM from tiny memory limit → increase `resources.limits.memory` to at least `128Mi`
- 05 Image tag typo → set `image` to a valid echo server tag (e.g., `"ealen/echo-server:latest"`)
- 06 NetworkPolicy blocks ingress → allow traffic to `app=web` on TCP 80

> Note: This lab validates logical intent. It doesn’t apply the change to a real cluster.

## Scoring
- 1 point per solved challenge. Scoreboard sorts by points, then by earliest completion time.
- Facilitator can reset a team or the whole game from the “Facilitator” panel.

## Files
- `app/__main__.py` (entry) and `app/app.py` (Flask server + validators)
- `app/templates/*.html` (UI)
- `app/static/style.css` (minimal styling)
- `requirements.txt` (Flask only)

## Tips
- Encourage teams to discuss *why* their fix is correct before submitting.
- If you want more chaos, toggle the “shuffle challenge order” checkbox on the facilitator panel.
- Want to make it harder? Turn off hints on the facilitator panel.

Have fun!
