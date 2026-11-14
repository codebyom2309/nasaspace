"""Tests for TiDB integration: auth, saved designs, careers, leaderboard, rooms."""
from __future__ import annotations

import json
import pytest

from engine import career_db as CAREER
from engine import db
from engine import designs as DESIGNS
from engine import leaderboard as LB
from engine import users as USERS
from engine.rooms import RoomStore

if not db.is_available():
    pytestmark = pytest.mark.skip(reason="TiDB not available in this environment")


@pytest.fixture(scope="module", autouse=True)
def setup_tidb():
    if db.is_available():
        db.init_db()


def test_auth_workflow():
    test_user = "pilot_test"
    # Clean up prior test runs if any
    db.execute_commit("DELETE FROM users WHERE username = %s", (test_user,))

    # Register
    reg = USERS.register(test_user, "supersecret", "Test Pilot")
    assert reg.get("ok") is True
    token = reg.get("token")
    assert token

    # Duplicate check
    dup = USERS.register(test_user, "anotherpass")
    assert "already taken" in dup.get("error", "").lower()

    # Verify session
    sess = USERS.get_session(token)
    assert sess and sess["username"] == test_user

    # Login
    login_res = USERS.login(test_user, "supersecret")
    assert login_res.get("ok") is True
    assert login_res["user"]["username"] == test_user

    # Invalid login
    bad_login = USERS.login(test_user, "wrongpassword")
    assert "invalid" in bad_login.get("error", "").lower()

    # Cleanup
    db.execute_commit("DELETE FROM users WHERE username = %s", (test_user,))


def test_saved_designs_tidb():
    # Save design
    saved = DESIGNS.save_design(
        name="Apollo Probe",
        scenario="lunar_ice",
        design={"orbit": "frozen", "bus": "medium"},
        flight={"score": 850, "grade": "S"},
        player_name="test_cadet",
    )
    assert saved.get("ok") is True
    design_id = saved["id"]

    # List
    items = DESIGNS.list_designs(player_name="test_cadet")
    assert any(d["id"] == design_id and d["name"] == "Apollo Probe" for d in items)

    # Delete
    deleted = DESIGNS.delete_design(design_id, player_name="test_cadet")
    assert deleted is True

    items_after = DESIGNS.list_designs(player_name="test_cadet")
    assert not any(d["id"] == design_id for d in items_after)


def test_career_persistence_tidb():
    career_state = {
        "name": "Artemis Agency",
        "difficulty": "hard",
        "month": 12,
        "funds": 500,
        "done": {"wildfire": {"score": 900, "grade": "S"}},
    }
    saved = CAREER.save_career(career_state, player_name="test_commander")
    assert saved is True

    loaded = CAREER.get_career(player_name="test_commander")
    assert loaded is not None
    assert loaded["name"] == "Artemis Agency"
    assert loaded["month"] == 12
    assert loaded["done"]["wildfire"]["score"] == 900

    # Clean up
    db.execute_commit("DELETE FROM careers WHERE player_name = %s", ("test_commander",))


def test_leaderboard_tidb_live():
    test_player = "test_lb_astronaut"
    # Insert
    LB.add({
        "player": test_player,
        "name": "Pathfinder",
        "scenario": "mars_rover",
        "difficulty": "normal",
        "score": 890.0,
        "grade": "S",
        "seed": 42,
        "design": {"orbit": "delta"},
    })

    # Query top
    top_rows = LB.top("mars_rover", 10)
    assert any(r["player"] == test_player and r["score"] == 890.0 for r in top_rows)

    # Clean up
    db.execute_commit("DELETE FROM leaderboard WHERE player_name = %s", (test_player,))


def test_rooms_tidb_persistence_across_instances():
    # Instance 1 creates room and edits
    store1 = RoomStore()
    code = store1.create("wildfire")
    store1.join(code, "hannah", ["manager"])
    store1.say(code, "hannah", "Ready to design!")

    # Instance 2 (simulating server restart) reads room from TiDB
    store2 = RoomStore()
    assert store2.exists(code)
    room_data = store2.get(code)
    assert room_data["design"]["scenario"] == "wildfire"
    assert "hannah" in room_data["players"]
    assert any("Ready to design!" in c["text"] for c in room_data["chat"])

    # Clean up
    db.execute_commit("DELETE FROM rooms WHERE code = %s", (code,))

