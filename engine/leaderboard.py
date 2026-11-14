"""TiDB-backed leaderboard with file fallback for offline/isolated tests."""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path

from . import db

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "leaderboard.json"
_file_lock = threading.Lock()
logger = logging.getLogger(__name__)

# Exposed so tests can override PATH if they wish
PATH = DEFAULT_PATH


def _is_custom_path(path: Path | None) -> bool:
    target = path or PATH
    return target != DEFAULT_PATH


def _load_file(path: Path) -> list[dict]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _add_file(entry: dict, path: Path) -> None:
    with _file_lock:
        rows = _load_file(path)
        rows.append({**entry, "time": time.time()})
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rows[-2000:]), encoding="utf-8")


def _top_file(scenario: str, n: int, path: Path) -> list[dict]:
    rows = [r for r in _load_file(path) if r.get("scenario") == scenario]
    return sorted(rows, key=lambda r: -r.get("score", 0))[:n]


def load(path: Path | None = None) -> list[dict]:
    target = path or PATH
    if _is_custom_path(target):
        return _load_file(target)

    try:
        rows = db.fetch_all(
            "SELECT player_name AS player, mission_name AS name, scenario, difficulty, score, grade, seed, design_json AS design, UNIX_TIMESTAMP(created_at) AS time FROM leaderboard ORDER BY created_at DESC LIMIT 2000"
        )
        for r in rows:
            if isinstance(r.get("design"), str):
                try:
                    r["design"] = json.loads(r["design"])
                except Exception:
                    pass
        return rows
    except Exception as e:
        logger.warning("TiDB load failed, falling back to file: %s", e)
        return _load_file(target)


def add(entry: dict, path: Path | None = None) -> None:
    target = path or PATH
    if _is_custom_path(target):
        _add_file(entry, target)
        return

    # Mirror to local file for offline recovery
    try:
        _add_file(entry, target)
    except Exception:
        pass

    try:
        design = entry.get("design", {})
        design_str = json.dumps(design) if not isinstance(design, str) else design
        user_id = entry.get("user_id")
        db.execute_commit(
            """INSERT INTO leaderboard
               (user_id, player_name, mission_name, scenario, difficulty, score, grade, seed, design_json)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                user_id,
                str(entry.get("player") or "anonymous")[:50],
                str(entry.get("name") or "Unnamed mission")[:100],
                str(entry.get("scenario", ""))[:50],
                str(entry.get("difficulty", "normal"))[:20],
                float(entry.get("score", 0.0)),
                str(entry.get("grade", "F"))[:5],
                int(entry.get("seed", 0)),
                design_str,
            ),
        )
    except Exception as e:
        logger.error("TiDB leaderboard insert error: %s", e)


def top(scenario: str, n: int = 10, path: Path | None = None) -> list[dict]:
    target = path or PATH
    if _is_custom_path(target):
        return _top_file(scenario, n, target)

    try:
        rows = db.fetch_all(
            """SELECT player_name AS player, mission_name AS name, scenario, difficulty, score, grade, seed,
                      design_json AS design, UNIX_TIMESTAMP(created_at) AS time
               FROM leaderboard
               WHERE scenario = %s
               ORDER BY score DESC
               LIMIT %s""",
            (scenario, int(n)),
        )
        for r in rows:
            if isinstance(r.get("design"), str):
                try:
                    r["design"] = json.loads(r["design"])
                except Exception:
                    pass
        if rows:
            return rows
        # If DB is empty, check file
        return _top_file(scenario, n, target)
    except Exception as e:
        logger.warning("TiDB leaderboard top query failed: %s", e)
        return _top_file(scenario, n, target)
