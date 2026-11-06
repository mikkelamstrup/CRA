"""Real PostgreSQL authorization/concurrency; node responses explicitly synthetic."""

import hashlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import UUID, uuid4

import test_identity
import test_labs
import test_terminal
from test_identity import logged_in
from test_reconcile import now, synthetic_snapshot

from cra.auth import Identity
from cra.contracts import Event, LifecyclePayload
from cra.db import connect
from cra.jobs import claim, publish
from cra.reconcile import apply_inventory
from cra.teaching import event
from cra.worker import process, reconcile_failed_jobs

identities = test_identity.identities
inventory = test_labs.inventory
ready_lab = test_terminal.ready_lab
BASE = "/api/v1/command-center"


def action(client, headers, lab, kind="reset", generation=1, key=None):
    return client.post(
        BASE + "/labs/" + lab + "/" + kind,
        headers={**headers, "Idempotency-Key": key or str(uuid4())},
        json={"generation": generation},
    )


def test_overview_scope_current_evidence_stale_capacity_and_no_shell_escalation(
    ready_lab, identities
):
    student, sh, lab, _ = ready_lab
    teacher, th = logged_in("teacher")
    other, oh = logged_in("outsider")
    for suffix in ["/overview", "/events"]:
        assert student.get(BASE + suffix).status_code == 403
    assert action(student, sh, lab).status_code == 403
    assert action(other, oh, lab).status_code == 404
    assert other.get(BASE + "/overview").json()["labs"] == []
    assert teacher.post("/api/v1/labs/" + lab + "/terminal-grants", headers=th).status_code == 404
    assert teacher.post("/api/v1/labs/" + lab + "/desktop-grants", headers=th).status_code == 404
    student.post(
        "/api/v1/teaching/help",
        headers=sh,
        json={"request_key": str(uuid4()), "message": "Synthetic private help"},
    )
    snapshot = synthetic_snapshot(lab)
    apply_inventory(snapshot, now())
    data = teacher.get(BASE + "/overview").json()
    assert data["labs"][0]["owner_id"] == str(identities[2])
    assert data["labs"][0]["completed_objectives"] == [] and not data["labs"][0]["stale"]
    assert data["help_count"] == 1 and "Synthetic private help" not in str(data)
    assert data["capacity"]["available_memory_mib"] == 24000 and not data["capacity"]["stale"]
    assert not any(
        key in str(data) for key in ["private_key", "objective_verifiers", "password_hash"]
    )
    with connect() as db:
        db.execute("UPDATE labs SET last_observed_at=now()-interval '30 seconds'")
        db.execute("UPDATE node_status SET received_at=now()-interval '30 seconds'")
    data = teacher.get(BASE + "/overview").json()
    assert data["labs"][0]["stale"] and data["capacity"]["stale"]
    with connect() as db:
        db.execute("UPDATE memberships SET role='student' WHERE user_id=%s", (identities[3],))
    assert teacher.get(BASE + "/overview").status_code == 403
    assert action(teacher, th, lab).status_code == 403


def test_teacher_generation_race_replay_worker_owner_and_exhaustion(
    ready_lab, identities, monkeypatch
):
    _, _, lab, _ = ready_lab
    teacher, th = logged_in("teacher")
    second_id = uuid4()
    with connect() as db:
        db.execute("UPDATE jobs SET status='SUCCEEDED'")
        db.execute(
            "INSERT INTO users(id,username,password_hash) SELECT %s,'secondteacher',password_hash FROM users WHERE id=%s",
            (second_id, identities[3]),
        )
        db.execute("INSERT INTO memberships VALUES(%s,%s,'teacher')", (second_id, identities[0]))
    second, hh = logged_in("secondteacher")
    key = str(uuid4())
    key2 = str(uuid4())
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(action, c, h, lab, key=k)
            for c, h, k in [(teacher, th, key), (second, hh, key2)]
        ]
        replies = [f.result() for f in futures]
    assert sorted(r.status_code for r in replies) == [202, 409]
    index = next(i for i, r in enumerate(replies) if r.status_code == 202)
    client, headers, request_key, actor = [
        (teacher, th, key, identities[3]),
        (second, hh, key2, second_id),
    ][index]
    assert action(client, headers, lab, key=request_key).json() == replies[index].json()
    assert action(client, headers, lab, generation=2, key=request_key).status_code == 409
    job = claim("teacher-worker-crash")
    assert job["actor_id"] == actor and job["resource_owner_id"] == identities[2]
    with connect() as db:
        db.execute(
            "UPDATE jobs SET lease_until=now()-interval '1 second' WHERE id=%s", (job["id"],)
        )
    replacement = claim("teacher-worker-replacement")
    assert replacement["id"] == job["id"] and replacement["lease_token"] != job["lease_token"]
    commands = []

    def node(path, command):
        commands.append(command)
        assert command.user_id == identities[2] and command.generation == 2
        return {
            "id": lab,
            "organization_id": str(identities[0]),
            "user_id": str(identities[2]),
            "generation": 2,
            "state": "READY",
            "capture_ready": True,
            "target_ip": "192.168.82.10",
            "slot": 0,
            "operator_user_ids": [],
            "objective_verifiers": {
                "find-service": hashlib.sha256(b"synthetic-center-flag").hexdigest()
            },
        }

    monkeypatch.setattr("cra.worker.request", node)
    process(replacement)
    assert len(commands) == 1
    with connect() as db:
        assert (
            db.execute("SELECT state FROM labs WHERE id=%s", (lab,)).fetchone()["state"] == "READY"
        )
        record = db.execute(
            "SELECT event FROM outbox WHERE event->'payload'->>'kind'='lab.lifecycle'"
        ).fetchone()["event"]
        assert record["actor_id"] == str(actor) and record["initiator_type"] == "user"
    assert action(client, headers, lab, "stop", 2).status_code == 202
    leased = claim("teacher-exhausted")
    with connect() as db:
        db.execute(
            "UPDATE jobs SET attempts=max_attempts,lease_until=now()-interval '1 second' WHERE id=%s",
            (leased["id"],),
        )
    assert claim("after-crash") is None and reconcile_failed_jobs() == 1
    with connect() as db:
        assert db.execute("SELECT state,error_code FROM labs WHERE id=%s", (lab,)).fetchone() == {
            "state": "ERROR",
            "error_code": "retry_exhausted",
        }


def test_teacher_transition_uses_student_owner_not_teacher(ready_lab, identities, inventory):
    _, _, lab, _ = ready_lab
    teacher, th = logged_in("teacher")
    inventory["free_disk_gib"] = 500
    with connect() as db:
        db.execute("UPDATE labs SET state='STOPPED',desired_state='STOPPED',last_observed_at=now()")
    team = teacher.post(
        "/api/v1/teams",
        headers=th,
        json={
            "request_key": str(uuid4()),
            "name": "Synthetic owner transition",
            "members": [{"user_id": str(identities[2]), "role": "operator"}],
        },
    ).json()
    result = teacher.post(
        "/api/v1/labs",
        headers={**th, "Idempotency-Key": str(uuid4())},
        json={"mission_id": "first-contact", "mission_version": 1, "team_id": team["id"]},
    )
    assert result.status_code == 202
    with connect() as db:
        db.execute(
            "UPDATE labs SET state='READY',last_observed_at=now() WHERE team_id=%s", (team["id"],)
        )
    for kind in ["start", "reset"]:
        result = action(teacher, th, lab, kind)
        assert result.status_code == 409 and result.json()["detail"] == "stop_team_first"
    with connect() as db:
        db.execute(
            "UPDATE labs SET state='STOPPED',desired_state='STOPPED',last_observed_at=now() WHERE team_id=%s",
            (team["id"],),
        )
    assert action(teacher, th, lab, "start").status_code == 202


def test_orphan_after_teacher_reset_preserves_student_resource_owner(
    ready_lab, identities, monkeypatch
):
    _, _, lab, _ = ready_lab
    teacher, th = logged_in("teacher")
    snapshot = synthetic_snapshot(lab)
    assert action(teacher, th, lab).status_code == 202
    with connect() as db:
        db.execute("UPDATE jobs SET status='SUCCEEDED'")
        db.execute("DELETE FROM labs WHERE id=%s", (lab,))
    observed = snapshot.labs[0]
    prefix = "cra-" + UUID(lab).hex[:10] + "-2"
    snapshot.labs[0] = observed.model_copy(
        update={"generation": 2, "operator": prefix + "o", "target": prefix + "t"}
    )
    apply_inventory(snapshot, now())
    with connect() as db:
        db.execute("UPDATE node_orphans SET first_seen_at=now()-interval '31 seconds'")
    apply_inventory(snapshot, now())
    job = claim("teacher-reset-orphan")
    assert job["kind"] == "orphan-destroy" and job["resource_owner_id"] == identities[2]

    def node(path, command):
        assert command.user_id == identities[2] and command.generation == 2
        return {
            "id": lab,
            "organization_id": str(identities[0]),
            "user_id": str(identities[2]),
            "generation": 2,
            "state": "DESTROYED",
            "slot": None,
        }

    monkeypatch.setattr("cra.orphans.request", node)
    process(job)
    with connect() as db:
        assert (
            db.execute("SELECT status FROM node_orphans WHERE lab_id=%s", (lab,)).fetchone()[
                "status"
            ]
            == "RESOLVED"
        )


def test_event_projection_commit_cursors_duplicates_and_private_payload(identities):
    teacher, _ = logged_in("teacher")
    outsider, _ = logged_in("outsider")
    user = Identity(identities[3], identities[0], "teacher", "teacher", "synthetic-session")
    entered = threading.Event()
    release = threading.Event()
    second_entered = threading.Event()

    def delayed():
        with connect() as db:
            event(
                db,
                user,
                None,
                "team.finding.submitted",
                {"private": "SYNTHETIC_PRIVATE_NOT_FOR_EVENTS"},
                submitted=True,
            )
            entered.set()
            assert release.wait(5)

    def next_event():
        second_entered.set()
        with connect() as db:
            event(
                db,
                user,
                None,
                "help.requested",
                {"private": "SYNTHETIC_PRIVATE_NOT_FOR_EVENTS"},
                submitted=True,
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(delayed)
        assert entered.wait(3)
        second = pool.submit(next_event)
        assert second_entered.wait(3)
        try:
            time.sleep(0.1)
            assert not second.done()
            empty = teacher.get(BASE + "/events").json()
            assert empty["events"] == [] and empty["teaching_cursor"] == 0
        finally:
            release.set()
        first.result()
        second.result()
    page = teacher.get(BASE + "/events").json()
    assert len(page["events"]) == 2 and page["teaching_cursor"] > 0
    assert "SYNTHETIC_PRIVATE_NOT_FOR_EVENTS" not in str(page)
    assert outsider.get(BASE + "/events").json()["events"] == []
    assert (
        teacher.get(BASE + "/events?teaching_after=" + str(page["teaching_cursor"])).json()[
            "events"
        ]
        == []
    )
    item = Event(
        schema_version=1,
        event_id=uuid4(),
        organization_id=identities[0],
        lab_id=uuid4(),
        generation=1,
        actor_id=identities[3],
        occurred_at=datetime.now(UTC),
        source="control",
        evidence_level="system",
        payload=LifecyclePayload(kind="lab.lifecycle", state="READY"),
    )
    with connect() as db:
        publish(db, item)
        # Additive optional fields also preserve idempotency for older event records.
        db.execute(
            "UPDATE outbox SET event=event-'initiator_type' WHERE event_id=%s", (item.event_id,)
        )
        publish(db, item)
    result = teacher.get(BASE + "/events?teaching_after=" + str(page["teaching_cursor"])).json()
    assert len(result["events"]) == 1 and result["events"][0]["event_id"] == str(item.event_id)
    assert result["events"][0]["initiator_type"] == "system"
    with connect() as db:
        for _ in range(105):
            event(db, user, None, "session.changed", {})
    first = teacher.get(BASE + "/events").json()
    assert (
        first["has_more"] and len([e for e in first["events"] if e["stream"] == "teaching"]) == 100
    )
    second = teacher.get(
        BASE
        + f"/events?domain_after={first['domain_cursor']}&teaching_after={first['teaching_cursor']}"
    ).json()
    assert len(second["events"]) == 7 and not second["has_more"]
    tail = teacher.get(BASE + "/events?tail=true").json()
    assert not tail["has_more"] and tail["teaching_cursor"] == second["teaching_cursor"]
