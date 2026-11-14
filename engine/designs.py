"""Saved designs management backed by TiDB."""
from __future__ import annotations

import json
import logging
from typing import Any

from . import db

logger = logging.getLogger(__name__)


def list_designs(user_id: int | None = None, player_name: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    """List saved designs for a user or player."""
    try:
        if user_id:
            rows = db.fetch_all(
                """SELECT id, user_id, player_name, name, scenario, design_json, flight_json,
                          UNIX_TIMESTAMP(created_at) as created_at, UNIX_TIMESTAMP(updated_at) as updated_at
                   FROM saved_designs
                   WHERE user_id = %s
                   ORDER BY updated_at DESC
                   LIMIT %s""",
                (user_id, limit),
            )
        elif player_name and player_name != "anonymous":
            rows = db.fetch_all(
                """SELECT id, user_id, player_name, name, scenario, design_json, flight_json,
                          UNIX_TIMESTAMP(created_at) as created_at, UNIX_TIMESTAMP(updated_at) as updated_at
                   FROM saved_designs
                   WHERE player_name = %s
                   ORDER BY updated_at DESC
                   LIMIT %s""",
                (player_name, limit),
            )
        else:
            return []

        out = []
        for r in rows:
            design = json.loads(r["design_json"]) if isinstance(r["design_json"], str) else r["design_json"]
            flight = json.loads(r["flight_json"]) if r.get("flight_json") and isinstance(r["flight_json"], str) else r.get("flight_json")
            out.append({
                "id": str(r["id"]),
                "name": r["name"],
                "scenario": r["scenario"],
                "design": design,
                "flight": flight,
                "at": int((r.get("updated_at") or r.get("created_at") or 0) * 1000),
            })
        return out
    except Exception as e:
        logger.error("Failed to list saved designs: %s", e)
        return []


def save_design(
    name: str,
    scenario: str,
    design: dict[str, Any],
    flight: dict[str, Any] | None = None,
    user_id: int | None = None,
    player_name: str = "anonymous",
) -> dict[str, Any]:
    """Save or create a new design in the drawer."""
    try:
        design_str = json.dumps(design)
        flight_str = json.dumps(flight) if flight else None
        clean_name = (name or "Untitled").strip()[:100]

        row_id = db.execute_commit(
            """INSERT INTO saved_designs (user_id, player_name, name, scenario, design_json, flight_json)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (user_id, player_name[:50], clean_name, scenario[:50], design_str, flight_str),
        )
        return {"ok": True, "id": str(row_id), "name": clean_name}
    except Exception as e:
        logger.error("Failed to save design: %s", e)
        return {"error": str(e)}


def delete_design(design_id: int | str, user_id: int | None = None, player_name: str | None = None) -> bool:
    """Delete a saved design with ownership check."""
    try:
        if user_id:
            count = db.execute_commit("DELETE FROM saved_designs WHERE id = %s AND user_id = %s", (design_id, user_id))
            return count > 0
        if player_name:
            count = db.execute_commit("DELETE FROM saved_designs WHERE id = %s AND player_name = %s", (design_id, player_name))
            return count > 0
        count = db.execute_commit("DELETE FROM saved_designs WHERE id = %s", (design_id,))
        return count > 0
    except Exception as e:
        logger.error("Failed to delete design %s: %s", design_id, e)
        return False

