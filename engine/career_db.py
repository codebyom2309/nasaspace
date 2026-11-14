"""Career and agency state persistence backed by TiDB."""
from __future__ import annotations

import json
import logging
from typing import Any

from . import db

logger = logging.getLogger(__name__)


def get_career(user_id: int | None = None, player_name: str | None = None) -> dict[str, Any] | None:
    """Retrieve career state for a user or player."""
    try:
        row = None
        if user_id:
            row = db.fetch_one(
                "SELECT agency_name, difficulty, current_month, career_json FROM careers WHERE user_id = %s",
                (user_id,),
            )
        elif player_name and player_name != "anonymous":
            row = db.fetch_one(
                "SELECT agency_name, difficulty, current_month, career_json FROM careers WHERE player_name = %s ORDER BY updated_at DESC LIMIT 1",
                (player_name,),
            )
        if not row:
            return None

        career_data = json.loads(row["career_json"]) if isinstance(row["career_json"], str) else row["career_json"]
        career_data["name"] = row["agency_name"]
        career_data["difficulty"] = row["difficulty"]
        career_data["month"] = row["current_month"]
        return career_data
    except Exception as e:
        logger.error("Failed to load career: %s", e)
        return None


def save_career(
    career_data: dict[str, Any],
    user_id: int | None = None,
    player_name: str = "anonymous",
) -> bool:
    """Save or update career state in TiDB."""
    try:
        agency_name = str(career_data.get("name", "My Agency"))[:100]
        difficulty = str(career_data.get("difficulty", "normal"))[:20]
        current_month = int(career_data.get("month", 0))
        career_str = json.dumps(career_data)

        if user_id:
            db.execute_commit(
                """INSERT INTO careers (user_id, player_name, agency_name, difficulty, current_month, career_json)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON DUPLICATE KEY UPDATE
                     agency_name = VALUES(agency_name),
                     difficulty = VALUES(difficulty),
                     current_month = VALUES(current_month),
                     career_json = VALUES(career_json)""",
                (user_id, player_name[:50], agency_name, difficulty, current_month, career_str),
            )
            return True
        elif player_name and player_name != "anonymous":
            existing = db.fetch_one("SELECT id FROM careers WHERE player_name = %s LIMIT 1", (player_name,))
            if existing:
                db.execute_commit(
                    """UPDATE careers
                       SET agency_name = %s, difficulty = %s, current_month = %s, career_json = %s
                       WHERE id = %s""",
                    (agency_name, difficulty, current_month, career_str, existing["id"]),
                )
            else:
                db.execute_commit(
                    """INSERT INTO careers (user_id, player_name, agency_name, difficulty, current_month, career_json)
                       VALUES (NULL, %s, %s, %s, %s, %s)""",
                    (player_name[:50], agency_name, difficulty, current_month, career_str),
                )
            return True
        return False
    except Exception as e:
        logger.error("Failed to save career: %s", e)
        return False

