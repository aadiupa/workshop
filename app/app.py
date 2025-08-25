
from __future__ import annotations
import json, os, time, uuid, threading
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any
from flask import Flask, render_template, request, redirect, url_for, abort

APP = Flask(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
os.makedirs(DATA_DIR, exist_ok=True)

STATE_FILE = os.path.join(DATA_DIR, "state.json")

# ------------------ Models ------------------
@dataclass
class Team:
    id: str
    name: str

@dataclass
class Challenge:
    id: str
    title: str
    description: str
    hint: str
    checks: List[str]

@dataclass
class Config:
    shuffle: bool = True
    hints: bool = True

@dataclass
class State:
    teams: Dict[str, Team] = field(default_factory=dict)
    challenges: Dict[str, Challenge] = field(default_factory=dict)
    solved: Dict[str, List[str]] = field(default_factory=dict)  # team_id -> [challenge_id]
    solves_ts: Dict[str, float] = field(default_factory=dict)   # team_id -> last solve ts
    cfg: Config = field(default_factory=Config)

    def to_json(self) -> dict:
        return {
            "teams": {k: asdict(v) for k, v in self.teams.items()},
            "challenges": {k: asdict(v) for k, v in self.challenges.items()},
            "solved": self.solved,
            "solves_ts": self.solves_ts,
            "cfg": asdict(self.cfg),
        }

    @staticmethod
    def from_json(d: dict) -> "State":
        st = State()
        st.teams = {k: Team(**v) for k, v in d.get("teams", {}).items()}
        st.challenges = {k: Challenge(**v) for k, v in d.get("challenges", {}).items()}
        st.solved = d.get("solved", {})
        st.solves_ts = d.get("solves_ts", {})
        cfgd = d.get("cfg", {})
        st.cfg = Config(**cfgd) if cfgd else Config()
        return st

LOCK = threading.Lock()
STATE: Optional[State] = None

# --------------- Challenge Validators ---------------
def expect_service_selector(payload: dict) -> Optional[str]:
    try:
        if payload.get("kind") != "Service":
            return "We expected kind=Service."
        sel = payload.get("spec", {}).get("selector", {})
        if sel.get("app") == "web":
            return None
        return "Selector must include app=web."
    except Exception as e:
        return f"Bad payload: {e}"

def expect_configmap_key(payload: dict) -> Optional[str]:
    if payload.get("kind") != "ConfigMap":
        return "We expected kind=ConfigMap."
    data = payload.get("data", {})
    if isinstance(data, dict) and data.get("WELCOME") and str(data.get("WELCOME")).strip():
        return None
    return "Provide data.WELCOME with a non-empty value."

def expect_liveness_root(payload: dict) -> Optional[str]:
    if payload.get("kind") != "Deployment":
        return "We expected kind=Deployment."
    try:
        tpl = payload["spec"]["template"]["spec"]
        containers = tpl["containers"]
        found = False
        for c in containers:
            if c.get("name") == "web":
                lp = c.get("livenessProbe", {}).get("httpGet", {}).get("path")
                if lp == "/":
                    found = True
        return None if found else 'Set containers[name=web].livenessProbe.httpGet.path="/"'
    except Exception:
        return 'Set containers[name=web].livenessProbe.httpGet.path="/"'

def expect_memory_limit(payload: dict) -> Optional[str]:
    if payload.get("kind") != "Deployment":
        return "We expected kind=Deployment."
    try:
        containers = payload["spec"]["template"]["spec"]["containers"]
        ok = False
        for c in containers:
            if c.get("name") == "web":
                limits = c.get("resources", {}).get("limits", {})
                mem = limits.get("memory")
                if isinstance(mem, str) and mem.lower().endswith("mi"):
                    try:
                        val = int(mem[:-2])
                        if val >= 128:
                            ok = True
                    except:  # noqa
                        pass
        return None if ok else "Increase resources.limits.memory for container web to >= 128Mi."
    except Exception:
        return "Increase resources.limits.memory for container web to >= 128Mi."

def expect_valid_image(payload: dict) -> Optional[str]:
    if payload.get("kind") != "Deployment":
        return "We expected kind=Deployment."
    try:
        containers = payload["spec"]["template"]["spec"]["containers"]
        ok = False
        for c in containers:
            if c.get("name") == "web":
                image = c.get("image", "")
                if image in ("ealen/echo-server:latest", "ealen/echo-server:0.6.0", "ealen/echo-server:0.7.0"):
                    ok = True
        return None if ok else 'Set container "web" image to a valid echo server tag, e.g., "ealen/echo-server:latest".'
    except Exception:
        return 'Set container "web" image to a valid echo server tag, e.g., "ealen/echo-server:latest".'

def expect_np_ingress(payload: dict) -> Optional[str]:
    if payload.get("kind") != "NetworkPolicy":
        return "We expected kind=NetworkPolicy."
    try:
        spec = payload["spec"]
        pod_sel = spec.get("podSelector", {}).get("matchLabels", {})
        if pod_sel.get("app") != "web":
            return 'podSelector.matchLabels.app must be "web".'
        ingress = spec.get("ingress")
        if not isinstance(ingress, list) or not ingress:
            return "Provide at least one ingress rule."
        ports = ingress[0].get("ports", [])
        ok_port = any((p.get("port") == 80 and p.get("protocol", "TCP") == "TCP") for p in ports if isinstance(ports, list))
        if not ok_port:
            return "Allow TCP port 80 in the ingress rule."
        return None
    except Exception:
        return "Allow TCP port 80 to app=web pods."

VALIDATORS = [
    ("svc_selector", "Service selector mismatch", 
     "The Service isn’t matching any pods. Fix the selector so it targets the app pods.\nWe’re looking for app=web under spec.selector.",
     '{"kind":"Service","spec":{"selector":{"app":"web"}}}',
     ["kind=Service", "spec.selector.app==web"], expect_service_selector),
    ("cm_missing", "Missing ConfigMap key", 
     "The app expects a welcome message at data.WELCOME. Provide a non-empty string.",
     '{"kind":"ConfigMap","data":{"WELCOME":"hello team"}}',
     ["kind=ConfigMap", "data.WELCOME non-empty"], expect_configmap_key),
    ("live_bad", "Bad liveness probe path",
     "Liveness probe keeps failing (404). Point it at the root path.",
     '{"kind":"Deployment","spec":{"template":{"spec":{"containers":[{"name":"web","livenessProbe":{"httpGet":{"path":"/"}}}]}}}}',
     ['kind=Deployment', 'containers[name="web"].livenessProbe.httpGet.path="/"'], expect_liveness_root),
    ("oom_limits", "OOM from tiny memory limit",
     "The app OOMs on requests. Increase the memory limit for container web.",
     '{"kind":"Deployment","spec":{"template":{"spec":{"containers":[{"name":"web","resources":{"limits":{"memory":"128Mi"}}}]}}}}',
     ['kind=Deployment', 'resources.limits.memory for "web" >= 128Mi'], expect_memory_limit),
    ("image_typo", "Image tag typo",
     "Pods are ImagePullBackOff due to a bad tag. Set a valid echo-server tag.",
     '{"kind":"Deployment","spec":{"template":{"spec":{"containers":[{"name":"web","image":"ealen/echo-server:latest"}]}}}}',
     ['kind=Deployment', 'image for "web" set to a valid tag'], expect_valid_image),
    ("np_block", "NetworkPolicy blocks ingress",
     "Nobody can reach the app. Add an ingress rule that allows TCP/80 to app=web pods.",
     '{"kind":"NetworkPolicy","spec":{"podSelector":{"matchLabels":{"app":"web"}},"ingress":[{"ports":[{"port":80,"protocol":"TCP"}]}]}}',
     ['kind=NetworkPolicy', 'podSelector matchLabels app=web', 'ingress allows TCP/80'], expect_np_ingress),
]

# ------------------ Persistence ------------------
def load_state() -> State:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return State.from_json(data)
    # default state
    st = State()
    default_teams = ["Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot"]
    for name in default_teams:
        tid = name.lower()
        st.teams[tid] = Team(id=tid, name=name)
        st.solved[tid] = []
    for cid, title, desc, hint, checks, _ in VALIDATORS:
        st.challenges[cid] = Challenge(id=cid, title=title, description=desc, hint=hint, checks=checks)
    save_state(st)
    return st

def save_state(st: State) -> None:
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st.to_json(), f, indent=2)
    os.replace(tmp, STATE_FILE)

def get_board() -> List[dict]:
    global STATE
    st = STATE
    total = len(st.challenges)
    rows = []
    for tid, team in st.teams.items():
        solved = len(st.solved.get(tid, []))
        rows.append({
            "team": team,
            "solved": solved,
            "points": solved,
            "last_solve": time.strftime("%H:%M:%S", time.localtime(st.solves_ts.get(tid, 0))) if st.solves_ts.get(tid) else None,
        })
    rows.sort(key=lambda r: (-r["points"], st.solves_ts.get(r["team"].id, 0) or 0))
    return rows, total

# ------------------ Routes ------------------
@APP.route("/")
def index():
    with LOCK:
        board, total = get_board()
    return render_template("index.html", board=board, total=total)

@APP.route("/teams")
def teams():
    with LOCK:
        st = STATE
        total = len(st.challenges)
        solved = {tid: len(s) for tid, s in st.solved.items()}
        teams = list(st.teams.values())
    return render_template("teams.html", teams=teams, solved=solved, total=total)

@APP.route("/t/<team_id>")
def team_detail(team_id):
    with LOCK:
        st = STATE
        team = st.teams.get(team_id)
        if not team: abort(404)
        solved = set(st.solved.get(team_id, []))
        chs = list(st.challenges.values())
        if st.cfg.shuffle:
            # deterministic shuffle per team
            chs = sorted(chs, key=lambda c: hash((team_id, c.id)))
    return render_template("team.html", team=team, challenges=chs, solved=solved)

@APP.route("/t/<team_id>/c/<challenge_id>", methods=["GET", "POST"])
def challenge_detail(team_id, challenge_id):
    with LOCK:
        st = STATE
        team = st.teams.get(team_id)
        ch = st.challenges.get(challenge_id)
        if not (team and ch): abort(404)
        solved = challenge_id in st.solved.get(team_id, [])
        show_hint = st.cfg.hints
    error = ok = None
    if request.method == "POST":
        payload_raw = request.form.get("payload","").strip()
        if not payload_raw:
            error = "Please paste a JSON payload."
        else:
            try:
                payload = json.loads(payload_raw)
            except Exception as e:
                error = f"Invalid JSON: {e}"
            else:
                # run validator
                validator = next(v for v in VALIDATORS if v[0]==challenge_id)[5]
                res = validator(payload)
                if res is None:
                    with LOCK:
                        if challenge_id not in STATE.solved[team_id]:
                            STATE.solved[team_id].append(challenge_id)
                            STATE.solves_ts[team_id] = time.time()
                            save_state(STATE)
                    ok = "Nice! Challenge solved."
                else:
                    error = res
    return render_template("challenge.html", team=team, ch=ch, solved=solved, error=error, ok=ok, show_hint=show_hint)

@APP.route("/facilitator", methods=["GET", "POST"])
def facilitator():
    global STATE
    with LOCK:
        if request.method == "POST":
            action = request.form.get("action","save")
            shuffle = bool(request.form.get("shuffle"))
            hints = bool(request.form.get("hints"))
            if action == "save":
                STATE.cfg.shuffle = shuffle
                STATE.cfg.hints = hints
            elif action == "reset_solved":
                for tid in STATE.solved:
                    STATE.solved[tid] = []
                    STATE.solves_ts[tid] = 0
            elif action == "reset_all":
                if os.path.exists(STATE_FILE):
                    os.remove(STATE_FILE)
                STATE = load_state()
            save_state(STATE)
        board, total = get_board()
        cfg = STATE.cfg
    return render_template("facilitator.html", board=board, total=total, cfg=cfg)

def main():
    global STATE
    with LOCK:
        STATE = load_state()
    port = int(os.environ.get("PORT", "5001"))
    APP.run(host="0.0.0.0", port=port, debug=False)

if __name__ == "__main__":
    main()
