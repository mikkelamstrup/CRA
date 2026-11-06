"""Real PostgreSQL: individual release, private aliases, races and preserved work."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import test_identity
import test_labs
import test_terminal
from test_identity import logged_in
from test_teaching import create_session, patch

from cra.db import connect
from cra.mission_access import release_source

identities = test_identity.identities
inventory = test_labs.inventory
ready_lab = test_terminal.ready_lab
BASE = "/api/v1/command-center/students"


def add_student(org, original):
    uid = uuid4()
    with connect() as db:
        db.execute(
            "INSERT INTO users(id,username,password_hash) SELECT %s,'secondstudent',password_hash FROM users WHERE id=%s",
            (uid, original),
        )
        db.execute(
            "INSERT INTO memberships(user_id,organization_id,role) VALUES(%s,%s,'student')",
            (uid, org),
        )
    return uid


def grant(client, headers, uid, mission="first-contact", version=1, **extra):
    return client.post(
        f"{BASE}/{uid}/releases",
        headers=headers,
        json={"mission_id": mission, "mission_version": version, **extra},
    )


def name(client, headers, uid, value, revision=0):
    return client.put(
        f"{BASE}/{uid}/name", headers=headers, json={"teacher_alias": value, "revision": revision}
    )


def create(client, headers, mission="first-contact"):
    return client.post(
        "/api/v1/labs",
        headers={**headers, "Idempotency-Key": str(uuid4())},
        json={"mission_id": mission, "mission_version": 1},
    )


def test_release_is_individual_versioned_authorized_and_idempotent(identities, inventory):
    org, other_org, uid, teacher_id, outsider_id = identities
    second_id = add_student(org, uid)
    teacher, th = logged_in("teacher")
    student, sh = logged_in()
    second, hh = logged_in("secondstudent")
    outsider, oh = logged_in("outsider")
    create_session(teacher, th)
    assert student.get(BASE).status_code == 403
    assert grant(student, sh, uid).status_code == 403
    assert grant(outsider, oh, uid).status_code == 404
    assert grant(teacher, th, outsider_id).status_code == 404
    assert grant(teacher, th, teacher_id).status_code == 404
    assert grant(teacher, th, uid, mission="missing").status_code == 404
    assert grant(teacher, th, uid, version=2).status_code == 404
    assert grant(teacher, th, uid, user_id=str(second_id)).status_code == 422
    assert grant(teacher, th, uid, organization_id=str(other_org)).status_code == 422
    before = student.get("/api/v1/assignments").json()
    assert [a["mission_id"] for a in before[:3]] == ["first-files", "first-contact", "web-trail"]
    assert before[0]["available"] and not before[1]["available"]
    assert create(student, sh).status_code == 403
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(lambda _: grant(teacher, th, uid), range(2)))
    assert all(r.status_code == 200 for r in replies)
    after = student.get("/api/v1/assignments").json()
    assert after[1]["available"] and after[1]["release_source"] == "individual"
    assert not second.get("/api/v1/assignments").json()[1]["available"]
    assert create(second, hh).status_code == 403
    assert create(student, sh).status_code == 202
    with connect() as db:
        assert db.execute("SELECT count(*) AS n FROM mission_releases").fetchone()["n"] == 1
        assert (
            db.execute(
                "SELECT count(*) AS n FROM teaching_events WHERE kind='student.mission.released'"
            ).fetchone()["n"]
            == 1
        )
        assert release_source(db, org, uid, "student", "first-contact", 2) == "locked"
        # Existing class-day default remains effective independently of grants.
        db.execute("UPDATE teaching_sessions SET day=2")
        assert release_source(db, org, second_id, "student", "first-contact", 1) == "class"


def test_alias_private_to_teachers_login_stable_conflicts_and_races(ready_lab, identities):
    student, sh, _lab, _ = ready_lab
    teacher, th = logged_in("teacher")
    outsider, oh = logged_in("outsider")
    uid = identities[2]
    assert name(student, sh, uid, "Synthetic A").status_code == 403
    assert name(outsider, oh, uid, "Synthetic A").status_code == 404
    assert name(teacher, th, uid, "x" * 81).status_code == 422
    assert name(teacher, th, uid, "Invalid\nName").status_code == 422
    assert name(teacher, th, uid, "Synthetic A", revision=5).status_code == 409
    r = name(teacher, th, uid, "  Synthetic A  ")
    assert r.status_code == 200 and r.json()["display_name"] == "Synthetic A"
    assert name(teacher, th, uid, "Synthetic A").json() == r.json()
    assert name(teacher, th, uid, "Synthetic B").status_code == 409
    center = teacher.get("/api/v1/command-center/overview").json()
    assert center["labs"][0]["owner_display_name"] == "Synthetic A"
    assert center["labs"][0]["owner_name"] == "student"
    for path in [
        "/api/v1/auth/me",
        "/api/v1/assignments",
        "/api/v1/labs",
        "/api/v1/teaching/help",
        "/api/v1/course/days",
        "/api/v1/teams",
    ]:
        assert "Synthetic A" not in student.get(path).text
    assert "Synthetic A" not in teacher.get("/api/v1/command-center/events").text
    again, _ = logged_in()
    assert again.get("/api/v1/auth/me").json()["username"] == "student"
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(
            pool.map(
                lambda value: name(teacher, th, uid, value, revision=1),
                ["Synthetic B", "Synthetic C"],
            )
        )
    assert sorted(r.status_code for r in replies) == [200, 409]
    winner = next(r.json() for r in replies if r.status_code == 200)
    cleared = name(teacher, th, uid, "", revision=winner["alias_revision"]).json()
    assert cleared["display_name"] == "student" and cleared["teacher_alias"] == ""
    with connect() as db:
        rows = db.execute(
            "SELECT payload FROM teaching_events WHERE kind='student.alias.changed'"
        ).fetchall()
        assert len(rows) == 3
        assert all(set(row["payload"]) == {"student_id"} for row in rows)
        db.execute("UPDATE memberships SET role='student' WHERE user_id=%s", (identities[3],))
    assert teacher.get(BASE).status_code == 403


def test_grant_does_not_replace_existing_lab_or_bypass_terminal_pause(ready_lab, identities):
    student, sh, lab, _ = ready_lab
    teacher, th = logged_in("teacher")
    _, response = create_session(teacher, th)
    session = response.json()
    session = teacher.put(
        "/api/v1/teaching/sessions/" + session["id"],
        headers=th,
        json=patch(session, terminal_enabled=False),
    ).json()
    assert not session["terminal_enabled"]
    assert grant(teacher, th, identities[2]).status_code == 200
    assert create(student, sh).status_code == 409
    with connect() as db:
        row = db.execute("SELECT state,generation FROM labs WHERE id=%s", (lab,)).fetchone()
        assert row["state"] == "READY" and row["generation"] == 1
    assert student.post("/api/v1/labs/" + lab + "/terminal-grants", headers=sh).status_code == 403
    teacher.put(
        "/api/v1/teaching/sessions/" + session["id"],
        headers=th,
        json=patch(session, terminal_enabled=True),
    )
    assert student.post("/api/v1/labs/" + lab + "/terminal-grants", headers=sh).status_code == 201


def test_inactive_students_hidden_and_rename_release_rejected(identities):
    teacher, th = logged_in("teacher")
    uid = identities[2]
    with connect() as db:
        db.execute("UPDATE users SET active=false WHERE id=%s", (uid,))
    assert teacher.get(BASE).json()["students"] == []
    assert name(teacher, th, uid, "Synthetic").status_code == 404
    assert grant(teacher, th, uid).status_code == 404


def test_completed_task_and_notebook_survive_explicit_lab_removal(
    identities, inventory, monkeypatch
):
    from cra.jobs import claim
    from cra.worker import process

    student, sh = logged_in()
    teacher, th = logged_in("teacher")
    create_session(teacher, th)
    created = create(student, sh, "first-files").json()
    lab = created["lab_id"]
    with connect() as db:
        db.execute("UPDATE jobs SET status='SUCCEEDED' WHERE id=%s", (created["operation_id"],))
        db.execute("UPDATE labs SET state='READY',last_observed_at=now() WHERE id=%s", (lab,))
        db.execute(
            "INSERT INTO score_ledger(id,organization_id,user_id,mission_id,mission_version,objective_id,lab_id,generation,points) VALUES (%s,%s,%s,'first-files',1,'find-evidence',%s,1,100)",
            (uuid4(), identities[0], identities[2], lab),
        )
    saved = student.put(
        "/api/v1/course/days/1/notebook",
        headers=sh,
        json={
            "revision": 0,
            "body": "Synthetic source explanation",
            "reflection": "Method preserved",
            "theory_read": True,
        },
    )
    assert saved.status_code == 200
    grant(teacher, th, identities[2])
    assert student.get("/api/v1/assignments").json()[0]["completed"]
    result = student.post(
        "/api/v1/labs/" + lab + "/destroy", headers={**sh, "Idempotency-Key": str(uuid4())}
    )
    assert result.status_code == 202

    def node(path, command):
        return {
            "id": lab,
            "organization_id": str(identities[0]),
            "user_id": str(identities[2]),
            "generation": 1,
            "state": "DESTROYED",
            "slot": None,
            "target_ip": None,
            "operator_user_ids": [],
        }

    monkeypatch.setattr("cra.worker.request", node)
    process(claim("synthetic-transition"))
    assert student.get("/api/v1/labs/" + lab).json()["state"] == "DESTROYED"
    assert (
        student.get("/api/v1/course/days/1/notebook").json()["body"]
        == "Synthetic source explanation"
    )
    first = student.get("/api/v1/assignments").json()[0]
    assert first["completed"] and first["earned_points"] == 100
    assert create(student, sh).status_code == 202


def test_teacher_alias_never_enters_projector_feed(ready_lab, identities):
    from test_projector import launch, project_feed, screen

    _, _, lab, _ = ready_lab
    teacher, headers = logged_in("teacher")
    assert name(teacher, headers, identities[2], "Synthetic private label").status_code == 200
    _, _, value = launch(lab)
    projector = screen(value)
    response = project_feed(projector)
    assert response.status_code == 200
    assert "Synthetic private label" not in response.text
    assert "student" not in response.text
    assert "Hold 01" in response.text
