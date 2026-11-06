"""Actual PG competition policy/clock/ledger tests; lab readiness is an explicit fixture."""

import hashlib
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest
import test_identity
import test_labs
import test_teams
from test_identity import logged_in
from test_teams import lab, team

from cra.db import connect

identities = test_identity.identities
inventory = test_labs.inventory
team_users = test_teams.team_users
FLAG = "CRA{synthetic-competition-only}"


def setup(team_users):
    teacher, th = logged_in("teacher")
    student, sh = logged_in()
    tid = team(teacher, th, [team_users[1], team_users[2]]).json()["id"]
    second = team(teacher, th, [team_users[3]]).json()["id"]
    body = {
        "request_key": str(uuid4()),
        "title": "Synthetic championship",
        "duration_seconds": 120,
        "team_ids": [tid, second],
    }
    response = teacher.post("/api/v1/competitions", headers=th, json=body)
    assert response.status_code == 201, response.text
    c = response.json()
    c = act(teacher, th, c, "start").json()
    lab_id = lab(teacher, th, tid).json()["lab_id"]
    with connect() as db:
        db.execute(
            "UPDATE labs SET state='READY',last_observed_at=clock_timestamp() WHERE id=%s",
            (lab_id,),
        )
        db.execute(
            "INSERT INTO lab_flags VALUES (%s,1,'find-service',%s)",
            (lab_id, hashlib.sha256(FLAG.encode()).hexdigest()),
        )
    return teacher, th, student, sh, c, lab_id, body


def act(client, headers, c, action):
    return client.post(
        "/api/v1/competitions/" + c["id"] + "/actions",
        headers=headers,
        json={"revision": c["revision"], "action": action},
    )


def submit(client, headers, c, lab_id, flag=FLAG, generation=1, key=None):
    return client.post(
        "/api/v1/competitions/" + c["id"] + "/attempts",
        headers=headers,
        json={
            "request_key": key or str(uuid4()),
            "lab_id": lab_id,
            "generation": generation,
            "flag": flag,
        },
    )


def get(client, c):
    return next(x for x in client.get("/api/v1/competitions").json() if x["id"] == c["id"])


def test_scope_concurrency_rebuild_immutable_and_practice_separation(team_users, inventory):
    t, th, s, sh, c, l, body = setup(team_users)
    foreign, fh = logged_in("outsider")
    second, seh = logged_in("second")
    assert foreign.get("/api/v1/competitions").json() == []
    assert act(foreign, fh, c, "pause").status_code == 404
    assert submit(foreign, fh, c, l).status_code == 404
    assert act(s, sh, c, "pause").status_code == 403
    assert s.post("/api/v1/competitions", headers=sh, json=body).status_code == 403
    assert not submit(s, sh, c, l, flag="wrong").json()["accepted"]
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(
            pool.map(
                lambda pair: submit(*pair, c, l), [(s, sh), (second, seh), (s, sh), (second, seh)]
            )
        )
    assert all(r.status_code == 200 and r.json()["accepted"] for r in responses)
    assert sum(r.json()["points_awarded"] for r in responses) == 150
    key = str(uuid4())
    one = submit(s, sh, c, l, key=key)
    assert one.json() == submit(s, sh, c, l, key=key).json()
    assert submit(s, sh, c, l, flag="changed", key=key).status_code == 409
    assert submit(s, sh, c, l, generation=2).status_code == 409
    state = get(s, c)
    assert state["standings"][0] == {"rank": 1, "alias": "Hold 01", "points": 150, "solved": 1}
    assert "username" not in str(state) and FLAG not in str(state)
    assert len(state["visible_teams"]) == 1
    assert state["visible_teams"][0]["alias"] == "Hold 01"
    assert state["visible_teams"][0]["completed_challenges"] == ["first-contact"]
    projected = s.get("/api/v1/competitions/" + c["id"] + "/board").json()
    assert set(projected) == {
        "title",
        "status",
        "server_time",
        "remaining_seconds",
        "frozen",
        "standings",
    }
    assert projected["standings"] == state["standings"]
    assert foreign.get("/api/v1/competitions/" + c["id"] + "/board").status_code == 404
    with connect() as db:
        assert db.execute("SELECT count(*) AS n FROM team_score_ledger").fetchone()["n"] == 0
        assert db.execute("SELECT sum(points) AS n FROM competition_scores").fetchone()["n"] == 150
        assert FLAG not in str(db.execute("SELECT * FROM competition_attempts").fetchall())
    for sql in ["UPDATE competition_scores SET points=900", "DELETE FROM competition_scores"]:
        with pytest.raises(psycopg.Error), connect() as db:
            db.execute(sql)
    assert get(s, c)["standings"] == state["standings"]
    # Teacher outside this team's operators cannot submit its flag.
    assert submit(t, th, c, l).status_code in (403, 404)
    with connect() as db:
        db.execute(
            "DELETE FROM team_members WHERE team_id=(SELECT team_id FROM labs WHERE id=%s) AND user_id=%s",
            (l, team_users[1]),
        )
    assert submit(s, sh, c, l).status_code == 404


def test_pause_resume_freeze_reset_and_deadline(team_users, inventory):
    t, th, s, sh, c, l, _ = setup(team_users)
    frozen = act(t, th, c, "freeze").json()
    assert submit(s, sh, c, l).json()["points_awarded"] == 150
    assert get(s, c)["standings"][0]["points"] == 0
    again = act(t, th, frozen, "freeze").json()
    assert again["standings"][0]["points"] == 0
    c = act(t, th, again, "pause").json()
    remaining = c["remaining_seconds"]
    assert submit(s, sh, c, l).status_code == 409
    time.sleep(0.05)
    assert get(s, c)["remaining_seconds"] == remaining
    assert act(t, th, again, "resume").status_code == 409
    c = act(t, th, c, "resume").json()
    c = act(t, th, c, "unfreeze").json()
    assert c["standings"][0]["points"] == 150
    with connect() as db:
        db.execute(
            "UPDATE labs SET generation=2,last_observed_at=clock_timestamp() WHERE id=%s", (l,)
        )
        db.execute(
            "INSERT INTO lab_flags VALUES (%s,2,'find-service',%s)",
            (l, hashlib.sha256(b"synthetic-next").hexdigest()),
        )
    assert submit(s, sh, c, l).status_code == 409
    assert not submit(s, sh, c, l, generation=2).json()["accepted"]
    result = submit(s, sh, c, l, flag="synthetic-next", generation=2).json()
    assert result == {"accepted": True, "already_completed": True, "points_awarded": 0}
    with connect() as db:
        db.execute(
            "UPDATE competitions SET deadline=clock_timestamp()-interval '1 second' WHERE id=%s",
            (c["id"],),
        )
    assert submit(s, sh, c, l, flag="synthetic-next", generation=2).status_code == 409
    assert get(s, c)["status"] == "ended"
    assert act(t, th, c, "pause").status_code == 409


def test_deadline_checked_after_waiting_on_lab_lock(team_users, inventory):
    _t, _th, s, sh, c, l, _ = setup(team_users)
    with connect() as db:
        db.execute(
            "UPDATE competitions SET deadline=clock_timestamp()+interval '0.3 second' WHERE id=%s",
            (c["id"],),
        )
    with ThreadPoolExecutor(max_workers=1) as pool:
        with connect() as db:
            db.execute("SELECT id FROM labs WHERE id=%s FOR UPDATE", (l,))
            future = pool.submit(submit, s, sh, c, l)
            time.sleep(0.6)
        assert future.result().status_code == 409
    with connect() as db:
        assert db.execute("SELECT count(*) AS n FROM competition_scores").fetchone()["n"] == 0


def test_fresh_lab_rate_limit_duplicate_members_and_create_idempotency(team_users, inventory):
    t, th, s, sh, c, l, body = setup(team_users)
    assert t.post("/api/v1/competitions", headers=th, json=body).json()["id"] == c["id"]
    assert (
        t.post("/api/v1/competitions", headers=th, json={**body, "title": "changed"}).status_code
        == 409
    )
    with connect() as db:
        db.execute(
            "UPDATE labs SET created_at=clock_timestamp()-interval '1 day' WHERE id=%s", (l,)
        )
    assert submit(s, sh, c, l).status_code == 409
    with connect() as db:
        db.execute("UPDATE labs SET created_at=clock_timestamp() WHERE id=%s", (l,))
    for _ in range(20):
        assert submit(s, sh, c, l, flag="wrong").status_code == 200
    assert submit(s, sh, c, l).status_code == 429
    c = act(t, th, c, "end").json()
    duplicate = team(t, th, [team_users[1]]).json()["id"]
    invalid = {**body, "request_key": str(uuid4()), "team_ids": [body["team_ids"][0], duplicate]}
    assert t.post("/api/v1/competitions", headers=th, json=invalid).status_code == 409


def test_pause_racing_submission_and_expired_replacement(team_users, inventory):
    t, th, s, sh, c, l, body = setup(team_users)
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(submit, s, sh, c, l)
        paused = act(t, th, c, "pause")
        response = future.result()
    assert paused.status_code == 200
    assert response.status_code in (200, 409)
    with connect() as db:
        scores = db.execute("SELECT count(*) AS n FROM competition_scores").fetchone()["n"]
    assert scores == (1 if response.status_code == 200 else 0)
    c = act(t, th, paused.json(), "resume").json()
    with connect() as db:
        db.execute(
            "UPDATE competitions SET deadline=clock_timestamp()-interval '1 second' WHERE id=%s",
            (c["id"],),
        )
    created = t.post("/api/v1/competitions", headers=th, json={**body, "request_key": str(uuid4())})
    assert created.status_code == 201
    assert get(s, c)["status"] == "ended"
    c = act(t, th, created.json(), "end").json()
    with connect() as db:
        db.execute("UPDATE users SET active=false WHERE id=%s", (team_users[2],))
    denied = t.post("/api/v1/competitions", headers=th, json={**body, "request_key": str(uuid4())})
    assert denied.status_code == 409 and denied.json()["detail"] == "competition_inactive_member"


def test_competition_and_notes_survive_real_dump_restore(team_users, inventory, tmp_path):
    import os
    import subprocess

    from psycopg.conninfo import conninfo_to_dict

    from cra.db import expected_version, migrate

    t, th, s, sh, c, l, _ = setup(team_users)
    assert submit(s, sh, c, l).json()["points_awarded"] == 150
    c = act(t, th, c, "freeze").json()
    tid = c["visible_teams"][0]["team_id"]
    team_path = "/api/v1/teams/" + tid + "/notebook"
    note_path = "/api/v1/course/days/8/notebook"
    assert (
        s.put(
            team_path, headers=sh, json={"revision": 0, "body": "Synthetic shared restore evidence"}
        ).status_code
        == 200
    )
    assert (
        s.put(
            note_path,
            headers=sh,
            json={
                "revision": 0,
                "body": "Synthetic personal restore evidence",
                "reflection": "Evidence is not inference",
                "theory_read": True,
            },
        ).status_code
        == 200
    )
    before = (get(s, c)["standings"], s.get(team_path).json(), s.get(note_path).json())
    dsn = os.environ["CRA_TEST_DATABASE_DSN"]
    assert conninfo_to_dict(dsn)["dbname"] == "cra_test"
    dump = tmp_path / "synthetic-competition.dump"
    try:
        subprocess.run(
            ["pg_dump", "--dbname", dsn, "--format=custom", "--file", str(dump)],
            check=True,
            capture_output=True,
        )
        dump.chmod(0o600)
        with connect() as db:
            db.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
        subprocess.run(
            ["pg_restore", "--dbname", dsn, "--clean", "--if-exists", "--exit-on-error", str(dump)],
            check=True,
            capture_output=True,
        )
        assert migrate() == expected_version()
        assert (get(s, c)["standings"], s.get(team_path).json(), s.get(note_path).json()) == before
        with pytest.raises(psycopg.Error), connect() as db:
            db.execute("DELETE FROM competition_scores")
        result = submit(s, sh, c, l).json()
        assert result["accepted"] and result["points_awarded"] == 0
    finally:
        dump.unlink(missing_ok=True)
