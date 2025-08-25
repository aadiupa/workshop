
from __future__ import annotations
import os, json, time, threading, re
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Any
from flask import Flask, render_template, request, redirect, url_for, abort, make_response

APP = Flask(__name__)

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
os.makedirs(DATA_DIR, exist_ok=True)
STATE_FILE = os.path.join(DATA_DIR, "state.json")
QUESTIONS_FILE = os.path.join(os.path.dirname(__file__), "questions.json")

ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN")  # optional

# ---------------- Models ----------------
@dataclass
class Team:
    id: str
    name: str

@dataclass
class Question:
    id: str
    kind: str  # "single" | "multi" | "short"
    text: str
    topic: str
    difficulty: str
    choices: List[str] = field(default_factory=list)
    answer: Any = None  # single: int; multi: List[int]; short: List[str] (regex)
    explanation: str = ""

@dataclass
class Answer:
    choice: Optional[int] = None
    choices: Optional[List[int]] = None
    text: Optional[str] = None
    correct: Optional[bool] = None

@dataclass
class RoundState:
    qidx: int = 0
    revealed: bool = False
    neg_mark: bool = False
    timer_secs: int = 60
    deadline: Optional[float] = None  # epoch seconds
    submissions: Dict[str, Dict[int, Answer]] = field(default_factory=dict)
    scores: Dict[str, float] = field(default_factory=dict)

@dataclass
class RootState:
    teams: Dict[str, Team] = field(default_factory=dict)
    questions: List[Question] = field(default_factory=list)
    rnd: RoundState = field(default_factory=RoundState)

    def to_json(self) -> dict:
        return {
            "teams": {k: asdict(v) for k,v in self.teams.items()},
            "questions": [asdict(q) for q in self.questions],
            "rnd": {
                "qidx": self.rnd.qidx,
                "revealed": self.rnd.revealed,
                "neg_mark": self.rnd.neg_mark,
                "timer_secs": self.rnd.timer_secs,
                "deadline": self.rnd.deadline,
                "submissions": {tid: {str(i): asdict(ans) for i, ans in qs.items()} for tid, qs in self.rnd.submissions.items()},
                "scores": self.rnd.scores,
            }
        }

    @staticmethod
    def from_json(d: dict) -> "RootState":
        st = RootState()
        st.teams = {k: Team(**v) for k, v in d.get("teams", {}).items()}
        st.questions = [Question(**q) for q in d.get("questions", [])]
        rnd = d.get("rnd", {})
        rs = RoundState(
            qidx = rnd.get("qidx", 0),
            revealed = rnd.get("revealed", False),
            neg_mark = rnd.get("neg_mark", False),
            timer_secs = rnd.get("timer_secs", 60),
            deadline = rnd.get("deadline"),
            submissions = {tid: {int(i): Answer(**ans) for i, ans in qs.items()} for tid, qs in rnd.get("submissions", {}).items()},
            scores = rnd.get("scores", {}),
        )
        st.rnd = rs
        return st

LOCK = threading.Lock()
STATE: Optional[RootState] = None

# ---------------- Helpers ----------------
def load_questions() -> List[Question]:
    with open(QUESTIONS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return [Question(**q) for q in data]

def load_state() -> RootState:
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return RootState.from_json(json.load(f))
    st = RootState()
    for name in ["Alpha","Bravo","Charlie","Delta","Echo","Foxtrot"]:
        tid = name.lower()
        st.teams[tid] = Team(id=tid, name=name)
        st.rnd.submissions[tid] = {}
        st.rnd.scores[tid] = 0.0
    st.questions = load_questions()
    save_state(st)
    return st

def save_state(st: RootState) -> None:
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st.to_json(), f, indent=2)
    os.replace(tmp, STATE_FILE)

def current_question(st: RootState) -> Optional[Question]:
    if not st.questions:
        return None
    if st.rnd.qidx < 0 or st.rnd.qidx >= len(st.questions):
        return None
    return st.questions[st.rnd.qidx]

def eval_answer(q: Question, ans: Answer) -> bool:
    if q.kind == "single":
        return ans.choice is not None and int(ans.choice) == int(q.answer)
    if q.kind == "multi":
        correct = set(q.answer or [])
        got = set(ans.choices or [])
        return got == correct
    if q.kind == "short":
        text = (ans.text or "").strip()
        for rx in (q.answer or []):
            if re.fullmatch(rx, text, flags=re.IGNORECASE):
                return True
        return False
    return False

def human_answer(q: Question) -> str:
    if q.kind in ("single","multi"):
        if not q.choices:
            return ""
        if q.kind == "single":
            try:
                idx = int(q.answer)
                return f"{idx}: {q.choices[idx]}"
            except Exception:
                return str(q.answer)
        else:
            try:
                idxs = [int(i) for i in (q.answer or [])]
                return ", ".join([f"{i}: {q.choices[i]}" for i in idxs])
            except Exception:
                return str(q.answer)
    else:
        return " / ".join(q.answer or [])

def board(st: RootState):
    rows = []
    for tid, team in st.teams.items():
        subs = st.rnd.submissions.get(tid, {})
        answered = len([i for i in subs if isinstance(subs[i], Answer)])
        correct = len([i for i in subs if subs[i].correct is True])
        rows.append({
            "team": team,
            "points": round(st.rnd.scores.get(tid, 0.0), 2),
            "answered": answered,
            "correct": correct,
        })
    rows.sort(key=lambda r: (-r["points"], r["team"].name))
    return rows

def is_local_request() -> bool:
    ra = request.remote_addr or ""
    return ra.startswith("127.") or ra == "::1" or ra == "localhost"

def block_reset_response():
    text = ("<h2>Uh huh! Someone's been caught trying to reset the game 🤭</h2>"
            "<p>You have been reported to the P&C Team. "
            "You're being paid for 365 days and still doing this? Bold strategy, Cotton!</p>"
            "<p><em>Resets are restricted. Ask the facilitator if you truly need one.</em></p>")
    resp = make_response(text, 403)
    return resp

def allowed_reset() -> bool:
    # If admin token is configured, require it
    if ADMIN_TOKEN:
        token = request.form.get("admin_token") or request.headers.get("X-QUIZ-ADMIN","")
        return token and token == ADMIN_TOKEN
    # Otherwise, only allow from localhost
    return is_local_request()

# ---------------- Routes ----------------
@APP.route("/")
def index():
    with LOCK:
        st = STATE
        b = board(st)
    return render_template("index.html", board=b)

@APP.route("/teams")
def teams():
    with LOCK:
        st = STATE
        teams = list(st.teams.values())
        scores = st.rnd.scores
    return render_template("teams.html", teams=teams, scores=scores)

@APP.route("/t/<team_id>", methods=["GET"])
def team_page(team_id):
    with LOCK:
        st = STATE
        team = st.teams.get(team_id)
        if not team: abort(404)
        q = current_question(st)
        qidx = st.rnd.qidx
        total = len(st.questions)
        subs = st.rnd.submissions.get(team_id, {})
        current = subs.get(qidx)
        revealed = st.rnd.revealed
        answer_h = human_answer(q) if (q and revealed) else None
        submitted = len([1 for t, byq in st.rnd.submissions.items() if byq.get(qidx)])
        deadline = st.rnd.deadline
    return render_template("team.html", team=team, q=q, qidx=qidx, total=total, current=current, revealed=revealed, answer_human=answer_h, submitted=submitted, deadline=deadline, msg=None)

@APP.route("/t/<team_id>/submit", methods=["POST"])
def submit_answer(team_id):
    with LOCK:
        st = STATE
        team = st.teams.get(team_id)
        if not team: abort(404)
        q = current_question(st)
        if not q: abort(400)
        if st.rnd.revealed:
            return redirect(url_for('team_page', team_id=team_id))
        ans = Answer()
        if q.kind == "single":
            choice = request.form.get("choice")
            ans.choice = int(choice) if choice is not None else None
        elif q.kind == "multi":
            choices = request.form.getlist("choices")
            ans.choices = [int(c) for c in choices]
        elif q.kind == "short":
            ans.text = request.form.get("text","").strip()
        st.rnd.submissions.setdefault(team_id, {})[st.rnd.qidx] = ans
        save_state(st)
    return redirect(url_for('team_page', team_id=team_id))

@APP.route("/facilitator", methods=["GET","POST"])
def facilitator():
    with LOCK:
        st = STATE
        blocked = False
        if request.method == "POST":
            action = request.form.get("action")
            st.rnd.neg_mark = bool(request.form.get("neg")) or st.rnd.neg_mark
            if action == "start_timer":
                try:
                    st.rnd.timer_secs = int(request.form.get("timer", st.rnd.timer_secs))
                except: pass
                st.rnd.deadline = time.time() + st.rnd.timer_secs
            elif action == "shuffle":
                import random
                random.shuffle(st.questions)
                st.rnd.qidx = 0
                st.rnd.revealed = False
                st.rnd.deadline = None
                for tid in st.teams:
                    st.rnd.submissions[tid] = {}
            elif action == "prev":
                st.rnd.qidx = max(0, st.rnd.qidx - 1)
                st.rnd.revealed = False
                st.rnd.deadline = None
            elif action == "next":
                st.rnd.qidx = min(len(st.questions)-1, st.rnd.qidx + 1)
                st.rnd.revealed = False
                st.rnd.deadline = None
            elif action in ("reset_round","reset_all","reveal"):
                # reveal is allowed; resets are protected
                if action in ("reset_round","reset_all") and not allowed_reset():
                    blocked = True
                else:
                    if action == "reveal":
                        q = current_question(st)
                        if q:
                            for tid in st.teams:
                                ans = st.rnd.submissions.get(tid, {}).get(st.rnd.qidx)
                                if not ans:
                                    continue
                                correct = eval_answer(q, ans)
                                ans.correct = bool(correct)
                                if correct:
                                    delta = 1.0 if q.kind == "single" else 2.0
                                    st.rnd.scores[tid] = st.rnd.scores.get(tid, 0.0) + delta
                                else:
                                    if st.rnd.neg_mark and ((q.kind=="single" and ans.choice is not None) or (q.kind=="multi" and (ans.choices or [])) or (q.kind=="short" and (ans.text or ""))):
                                        st.rnd.scores[tid] = st.rnd.scores.get(tid, 0.0) - 0.5
                            st.rnd.revealed = True
                            st.rnd.deadline = None
                    elif action == "reset_round":
                        st.rnd.revealed = False
                        st.rnd.deadline = None
                        for tid in st.teams:
                            st.rnd.submissions[tid] = {}
                    elif action == "reset_all":
                        for tid in st.teams:
                            st.rnd.submissions[tid] = {}
                            st.rnd.scores[tid] = 0.0
                        st.rnd.qidx = 0
                        st.rnd.revealed = False
                        st.rnd.deadline = None
            save_state(st)
        q = current_question(st)
        qidx = st.rnd.qidx
        total = len(st.questions)
        subs = []
        for tid, team in st.teams.items():
            ans = st.rnd.submissions.get(tid, {}).get(qidx)
            if q and ans:
                if q.kind=="single":
                    atext = q.choices[ans.choice] if ans.choice is not None and 0 <= ans.choice < len(q.choices) else "(no answer)"
                elif q.kind=="multi":
                    atext = ", ".join([q.choices[i] for i in (ans.choices or []) if 0 <= i < len(q.choices)]) if (ans.choices) else "(no answer)"
                else:
                    atext = ans.text or "(no answer)"
                correct = ans.correct if st.rnd.revealed else None
            else:
                atext = "—"
                correct = None
            subs.append({"team": team, "answer": atext, "correct": correct})
        answer_human = human_answer(q) if q else None
        state = st.rnd
        b = board(st)
        admin_required = True if ADMIN_TOKEN else False
    if request.method == "POST" and 'blocked' in locals() and blocked:
        return block_reset_response()
    return render_template("facilitator.html", q=q, qidx=qidx, total=total, subs=subs, answer_human=answer_human, state=state, board=b, admin_required=admin_required)

@APP.route("/play")
def play_all():
    with LOCK:
        st = STATE
        q = current_question(st)
        qidx = st.rnd.qidx
        revealed = st.rnd.revealed
        answer_human = human_answer(q) if (q and revealed) else None
    return render_template("play_all.html", q=q, qidx=qidx, revealed=revealed, answer_human=answer_human)

def main():
    global STATE
    with LOCK:
        STATE = load_state()
    port = int(os.environ.get("PORT", "5001"))
    APP.run(host="0.0.0.0", port=port, debug=False)

if __name__ == "__main__":
    main()
