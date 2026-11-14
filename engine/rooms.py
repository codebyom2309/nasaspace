"""Shared multiplayer state: a room holds one design that 1-4 players edit together.

Backed by TiDB for persistence across restarts, with an in-memory cache for fast polling.
"""
from __future__ import annotations

import json
import logging
import random
import string
import threading
import time
from typing import Any

from . import catalog as C
from . import db
from .model import blank_design

logger = logging.getLogger(__name__)

FIELD_OWNER = {f: r for r, info in C.ROLES.items() for f in info["owns"]}
FIELD_OWNER.update({"scenario": "manager", "sandbox": "manager"})


class RoomStore:
    def __init__(self):
        self._rooms: dict[str, dict] = {}
        self._lock = threading.Lock()

    def _load_from_db(self, code: str) -> dict | None:
        try:
            r_row = db.fetch_one("SELECT * FROM rooms WHERE code = %s", (code,))
            if not r_row:
                return None
            players: dict[str, Any] = {}
            for p in db.fetch_all("SELECT * FROM room_players WHERE room_code = %s", (code,)):
                roles = json.loads(p["roles_json"]) if isinstance(p["roles_json"], str) else p["roles_json"]
                seen = p["last_seen"].timestamp() if hasattr(p["last_seen"], "timestamp") else time.time()
                players[p["player_name"]] = {"roles": roles, "seen": seen}

            chat: list[dict] = []
            for c in db.fetch_all("SELECT * FROM room_chat WHERE room_code = %s ORDER BY id ASC LIMIT 50", (code,)):
                t = c["created_at"].timestamp() if hasattr(c["created_at"], "timestamp") else time.time()
                chat.append({
                    "t": t,
                    "who": c["player_name"],
                    "text": c["message"],
                    "msg": bool(c["is_user_msg"]),
                })

            design = json.loads(r_row["design_json"]) if isinstance(r_row["design_json"], str) else r_row["design_json"]
            approvals = json.loads(r_row["approvals"]) if isinstance(r_row["approvals"], str) else r_row["approvals"]
            last_run = None
            if r_row.get("last_run_json"):
                last_run = json.loads(r_row["last_run_json"]) if isinstance(r_row["last_run_json"], str) else r_row["last_run_json"]
            created = r_row["created_at"].timestamp() if hasattr(r_row["created_at"], "timestamp") else time.time()

            return {
                "design": design,
                "version": int(r_row["version"]),
                "players": players,
                "approvals": approvals,
                "chat": chat,
                "last_run": last_run,
                "created": created,
                "scenario": r_row["scenario"],
            }
        except Exception as e:
            logger.debug("TiDB room load skipped/failed for %s: %s", code, e)
            return None

    def _save_room_meta(self, code: str):
        r = self._rooms.get(code)
        if not r:
            return
        try:
            design_str = json.dumps(r["design"])
            approvals_str = json.dumps(r["approvals"])
            last_run_str = json.dumps(r["last_run"]) if r.get("last_run") else None
            scenario = r["design"].get("scenario", "wildfire")
            db.execute_commit(
                """INSERT INTO rooms (code, scenario, design_json, approvals, version, last_run_json)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON DUPLICATE KEY UPDATE
                     scenario = VALUES(scenario),
                     design_json = VALUES(design_json),
                     approvals = VALUES(approvals),
                     version = VALUES(version),
                     last_run_json = VALUES(last_run_json)""",
                (code, scenario, design_str, approvals_str, r["version"], last_run_str),
            )
        except Exception as e:
            logger.debug("TiDB room meta save skipped/failed for %s: %s", code, e)

    def _save_player(self, code: str, player: str, roles: list[str]):
        try:
            db.execute_commit(
                """INSERT INTO room_players (room_code, player_name, roles_json)
                   VALUES (%s, %s, %s)
                   ON DUPLICATE KEY UPDATE roles_json = VALUES(roles_json)""",
                (code, player, json.dumps(roles)),
            )
        except Exception as e:
            logger.debug("TiDB player save skipped/failed for %s: %s", code, e)

    def _save_chat(self, code: str, who: str, text: str, is_user_msg: bool = False):
        try:
            db.execute_commit(
                """INSERT INTO room_chat (room_code, player_name, message, is_user_msg)
                   VALUES (%s, %s, %s, %s)""",
                (code, who, text[:500], is_user_msg),
            )
        except Exception as e:
            logger.debug("TiDB chat save skipped/failed for %s: %s", code, e)

    def _ensure_room(self, code: str) -> bool:
        if code in self._rooms:
            return True
        loaded = self._load_from_db(code)
        if loaded:
            self._rooms[code] = loaded
            return True
        return False

    def create(self, scenario: str = "wildfire") -> str:
        with self._lock:
            while True:
                code = "".join(random.choices(string.ascii_uppercase.replace("O", "").replace("I", ""), k=4))
                if not self._ensure_room(code):
                    break
            self._rooms[code] = {
                "design": blank_design(scenario),
                "version": 0,
                "players": {},
                "approvals": {r: False for r in C.ROLES},
                "chat": [],
                "last_run": None,
                "created": time.time(),
            }
            self._save_room_meta(code)
            return code

    def exists(self, code: str) -> bool:
        with self._lock:
            return self._ensure_room(code)

    def get(self, code: str) -> dict:
        with self._lock:
            self._ensure_room(code)
            r = self._rooms[code]
            return {
                **r,
                "design": dict(r["design"]),
                "players": dict(r["players"]),
                "approvals": dict(r["approvals"]),
                "chat": list(r["chat"]),
            }

    def version(self, code: str) -> int:
        with self._lock:
            if not self._ensure_room(code):
                return -1
            return self._rooms[code]["version"]

    def join(self, code: str, player: str, roles: list[str]):
        with self._lock:
            if not self._ensure_room(code):
                return
            r = self._rooms[code]
            r["players"][player] = {"roles": list(roles), "seen": time.time()}
            r["version"] += 1
            self._save_room_meta(code)
            self._save_player(code, player, roles)

    def roles_taken(self, code: str, exclude: str | None = None) -> dict[str, str]:
        with self._lock:
            self._ensure_room(code)
            r = self._rooms[code]
            return {role: p for p, info in r["players"].items() if p != exclude for role in info["roles"]}

    def set_field(self, code: str, player: str, field: str, value) -> bool:
        with self._lock:
            if not self._ensure_room(code):
                return False
            r = self._rooms[code]
            owner = FIELD_OWNER.get(field)
            if owner not in r["players"].get(player, {}).get("roles", []):
                return False
            if r["design"].get(field) == value:
                return True
            if field == "scenario":
                r["design"] = blank_design(value) | {"difficulty": r["design"]["difficulty"]}
            else:
                r["design"][field] = value
            r["approvals"] = {k: False for k in r["approvals"]}
            r["version"] += 1
            chat_item = {"t": time.time(), "who": player, "text": f"changed {field}"}
            r["chat"].append(chat_item)
            r["chat"] = r["chat"][-50:]
            self._save_room_meta(code)
            self._save_chat(code, player, f"changed {field}", is_user_msg=False)
            return True

    def replace_design(self, code: str, design: dict):
        with self._lock:
            if not self._ensure_room(code):
                return
            r = self._rooms[code]
            r["design"] = dict(design)
            r["approvals"] = {k: False for k in r["approvals"]}
            r["version"] += 1
            self._save_room_meta(code)

    def approve(self, code: str, player: str, role: str, value: bool = True) -> bool:
        with self._lock:
            if not self._ensure_room(code):
                return False
            r = self._rooms[code]
            if role not in r["players"].get(player, {}).get("roles", []):
                return False
            r["approvals"][role] = value
            r["version"] += 1
            self._save_room_meta(code)
            return True

    def all_approved(self, code: str) -> bool:
        with self._lock:
            if not self._ensure_room(code):
                return False
            return all(self._rooms[code]["approvals"].values())

    def say(self, code: str, player: str, text: str):
        with self._lock:
            if not self._ensure_room(code):
                return
            r = self._rooms[code]
            msg_text = text[:300]
            r["chat"].append({"t": time.time(), "who": player, "text": msg_text, "msg": True})
            r["chat"] = r["chat"][-50:]
            r["version"] += 1
            self._save_room_meta(code)
            self._save_chat(code, player, msg_text, is_user_msg=True)

    def set_last_run(self, code: str, run: dict):
        with self._lock:
            if not self._ensure_room(code):
                return
            self._rooms[code]["last_run"] = run
            self._rooms[code]["version"] += 1
            self._save_room_meta(code)
