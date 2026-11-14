"""Mission Architect web server: serves the game (web/) and a TiDB-backed JSON API.

Run: python server.py  ->  http://localhost:8600
"""
from __future__ import annotations

import json
import math
import os
import random
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from engine import career_db as CAREER
from engine import catalog as C
from engine import db
from engine import designs as DESIGNS
from engine import leaderboard as LB
from engine import users as USERS
from engine.model import blank_design, default_design, evaluate, normalize
from engine.rooms import FIELD_OWNER, RoomStore
from engine.scoring import score
from engine.simulate import monte_carlo, simulate

WEB = Path(__file__).resolve().parent / "web"
ROOMS = RoomStore()


def clean(o):
    """JSON-safe: inf/nan -> None, tuples -> lists."""
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [clean(v) for v in o]
    return o


def catalog() -> dict:
    return {k: getattr(C, k) for k in ("DESTINATIONS", "SCENARIOS", "ORBITS", "LAUNCH_VEHICLES", "LV_TARGET_LABEL",
                                        "BUSES", "SOLAR_ARRAYS", "RTG", "BATTERIES", "COMMS", "GROUND", "COMPUTERS",
                                        "ADCS", "PROPULSION", "INSTRUMENTS", "DIFFICULTY", "ROLES", "RADIATION_LABEL", "EDL", "PARTNERS", "MAX_PARTNERS", "PAYLOADS", "CREW_RATED", "SUPPLY_KG_PER_CREW_MONTH", "EXISTING", "VEHICLE")} | {
        "FIELD_OWNER": FIELD_OWNER,
        "DEFAULTS": {s: default_design(s) for s in C.SCENARIOS},
        "BLANK": {s: blank_design(s) for s in C.SCENARIOS},
    }


def launch(body: dict, user_id: int | None = None) -> dict:
    design = normalize(body.get("design", {}))
    seed = int(body.get("seed") or random.randint(1, 999_999))
    ev = evaluate(design)
    sim = simulate(design, seed, ev)
    sc = score(sim)
    sim.pop("evaluation", None)
    res = {
        "name": str(body.get("name", "Unnamed mission"))[:40],
        "player": str(body.get("player", ""))[:20],
        "seed": seed,
        "design": design,
        "sim": sim,
        "score": sc,
        "evaluation": ev,
    }
    if sim["launched"] and not design["sandbox"] and body.get("save", True):
        LB.add({
            "name": res["name"],
            "player": res["player"] or "anonymous",
            "scenario": design["scenario"],
            "difficulty": design["difficulty"],
            "score": sc["total"],
            "grade": sc["grade"],
            "seed": seed,
            "design": design,
            "user_id": user_id,
        })
    return res


def room_view(code: str) -> dict:
    r = ROOMS.get(code)
    return {
        "code": code,
        "design": r["design"],
        "version": r["version"],
        "approvals": r["approvals"],
        "players": {p: i["roles"] for p, i in r["players"].items()},
        "chat": r["chat"][-20:],
        "last_run": r["last_run"],
        "evaluation": evaluate(r["design"]),
    }


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(WEB), **kw)

    def log_message(self, fmt, *args):  # quieter console
        if "/api/room/" not in self.path:
            super().log_message(fmt, *args)

    def _json(self, data, status=200):
        raw = json.dumps(clean(data)).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n > 500_000:
            raise ValueError("body too large")
        return json.loads(self.rfile.read(n) or b"{}")

    def _get_auth_user(self) -> dict | None:
        """Extract user session from Authorization header or cookie."""
        auth = self.headers.get("Authorization", "")
        token = None
        if auth.startswith("Bearer "):
            token = auth[7:].strip()
        elif "X-Session-Token" in self.headers:
            token = self.headers.get("X-Session-Token")
        else:
            cookie_header = self.headers.get("Cookie", "")
            for part in cookie_header.split(";"):
                if "=" in part:
                    k, v = part.strip().split("=", 1)
                    if k == "session_token":
                        token = v
                        break
        return USERS.get_session(token) if token else None

    def end_headers(self):
        if not self.path.startswith("/api/"):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def do_GET(self):
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        sess = self._get_auth_user()
        user_id = sess["user_id"] if sess else None

        try:
            if u.path == "/api/health":
                try:
                    res = db.fetch_one("SELECT VERSION() as v, DATABASE() as db")
                    return self._json({"status": "ok", "tidb": res})
                except Exception as ex:
                    return self._json({"status": "error", "error": str(ex)}, 500)

            if u.path == "/api/catalog":
                return self._json(catalog())

            if u.path == "/api/leaderboard":
                return self._json(LB.top(q.get("scenario", "wildfire"), int(q.get("n", 10))))

            if u.path == "/api/auth/me":
                if sess:
                    return self._json({"logged_in": True, "user": sess})
                return self._json({"logged_in": False, "user": None})

            if u.path == "/api/designs":
                player = q.get("player") or (sess["username"] if sess else "anonymous")
                designs = DESIGNS.list_designs(user_id=user_id, player_name=player)
                return self._json(designs)

            if u.path == "/api/career":
                player = q.get("player") or (sess["username"] if sess else "anonymous")
                c_data = CAREER.get_career(user_id=user_id, player_name=player)
                return self._json(c_data or {})

            if u.path.startswith("/api/room/"):
                code = u.path.rsplit("/", 1)[-1].upper()
                if not ROOMS.exists(code):
                    return self._json({"error": "No room with that code."}, 404)
                if int(q.get("since", -1)) == ROOMS.version(code):
                    return self._json({"unchanged": True, "version": ROOMS.version(code)})
                return self._json(room_view(code))

        except Exception as e:  # noqa: BLE001
            return self._json({"error": str(e)}, 400)
        return super().do_GET()

    def do_POST(self):
        u = urlparse(self.path)
        sess = self._get_auth_user()
        user_id = sess["user_id"] if sess else None

        try:
            b = self._body()

            # Auth endpoints
            if u.path == "/api/auth/register":
                res = USERS.register(b.get("username", ""), b.get("password", ""), b.get("display_name", ""))
                return self._json(res, 200 if res.get("ok") else 400)

            if u.path == "/api/auth/login":
                res = USERS.login(b.get("username", ""), b.get("password", ""))
                return self._json(res, 200 if res.get("ok") else 401)

            if u.path == "/api/auth/logout":
                token = b.get("token") or (self.headers.get("Authorization", "")[7:] if self.headers.get("Authorization", "").startswith("Bearer ") else None)
                USERS.logout(token)
                return self._json({"ok": True})

            # Designs endpoints
            if u.path == "/api/designs":
                player = str(b.get("player") or (sess["username"] if sess else "anonymous"))[:50]
                res = DESIGNS.save_design(
                    name=b.get("name", "Untitled"),
                    scenario=b.get("scenario", "wildfire"),
                    design=b.get("design", {}),
                    flight=b.get("flight"),
                    user_id=user_id,
                    player_name=player,
                )
                return self._json(res)

            if u.path == "/api/designs/delete":
                design_id = b.get("id")
                player = sess["username"] if sess else b.get("player")
                ok = DESIGNS.delete_design(design_id, user_id=user_id, player_name=player)
                return self._json({"ok": ok})

            # Career endpoint
            if u.path == "/api/career":
                player = str(b.get("player") or (sess["username"] if sess else "anonymous"))[:50]
                ok = CAREER.save_career(b.get("career", b), user_id=user_id, player_name=player)
                return self._json({"ok": ok})

            # Existing game endpoints
            if u.path == "/api/evaluate":
                return self._json(evaluate(b.get("design", {})))

            if u.path == "/api/risk":
                return self._json(monte_carlo(normalize(b.get("design", {})), 200))

            if u.path == "/api/launch":
                room = b.get("room")
                if room:
                    if not ROOMS.exists(room):
                        return self._json({"error": "No room with that code."}, 404)
                    if not ROOMS.all_approved(room):
                        return self._json({"error": "All four roles must approve first."}, 409)
                    b["design"] = ROOMS.get(room)["design"]
                res = launch(b, user_id=user_id)
                if room:
                    ROOMS.set_last_run(room, clean(res))
                return self._json(res)

            if u.path == "/api/room/create":
                code = ROOMS.create(b.get("scenario", "wildfire"))
                if b.get("design"):
                    ROOMS.replace_design(code, normalize(b["design"]))
                ROOMS.join(code, b.get("player", "anonymous"), [])
                return self._json(room_view(code))

            parts = u.path.strip("/").split("/")  # api/room/CODE/action
            if len(parts) == 4 and parts[1] == "room":
                code, action = parts[2].upper(), parts[3]
                if not ROOMS.exists(code):
                    return self._json({"error": "No room with that code."}, 404)
                p = str(b.get("player", ""))[:20]
                if action == "join":
                    roles = [r for r in b.get("roles", []) if r in C.ROLES]
                    taken = ROOMS.roles_taken(code, exclude=p)
                    if any(r in taken for r in roles):
                        return self._json({"error": "Role already taken."}, 409)
                    ROOMS.join(code, p, roles)
                elif action == "set":
                    if not ROOMS.set_field(code, p, b["field"], b["value"]):
                        return self._json({"error": "That belongs to another role."}, 403)
                elif action == "approve":
                    ROOMS.approve(code, p, b["role"], bool(b.get("value", True)))
                elif action == "say":
                    ROOMS.say(code, p, str(b.get("text", "")))
                return self._json(room_view(code))

        except Exception as e:  # noqa: BLE001
            return self._json({"error": str(e)}, 400)
        self._json({"error": "not found"}, 404)

    def do_DELETE(self):
        u = urlparse(self.path)
        sess = self._get_auth_user()
        user_id = sess["user_id"] if sess else None

        if u.path.startswith("/api/designs/"):
            design_id = u.path.rsplit("/", 1)[-1]
            player = sess["username"] if sess else None
            ok = DESIGNS.delete_design(design_id, user_id=user_id, player_name=player)
            return self._json({"ok": ok})
        self._json({"error": "not found"}, 404)


def main():
    try:
        db.init_db()
        print("Connected to TiDB and verified schema.")
    except Exception as e:
        print(f"Warning: TiDB initialization failed/deferred: {e}")

    port = int(os.environ.get("PORT", 8600))
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"Mission Architect on http://localhost:{port}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
