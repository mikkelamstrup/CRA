"""Real PostgreSQL policy/concurrency tests; node inventory is an explicit fixture."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
import test_identity
import test_labs
from test_identity import PASSWORD, logged_in
from test_terminal import issue

from cra.auth import PASSWORDS
from cra.db import connect

identities = test_identity.identities
inventory = test_labs.inventory


@pytest.fixture
def team_users(identities, monkeypatch, tmp_path):
    org, _, student, teacher, outsider = identities
    second = uuid4()
    with connect() as db:
        db.execute(
            "INSERT INTO users(id,username,password_hash) VALUES (%s,'second',%s)",
            (second, PASSWORDS.hash(PASSWORD)),
        )
        db.execute("INSERT INTO memberships VALUES (%s,%s,'student')", (second, org))
    key = tmp_path / "synthetic-signing-key"
    key.write_bytes(b"synthetic-not-installed")
    monkeypatch.setenv("CRA_TERMINAL_SIGNING_KEY", str(key))
    return org, student, second, teacher, outsider


def team(teacher, headers, members, key=None):
    return teacher.post(
        "/api/v1/teams",
        headers=headers,
        json={
            "request_key": key or str(uuid4()),
            "name": "Synthetic team",
            "members": [{"user_id": str(u), "role": "operator"} for u in members],
        },
    )


def lab(teacher, headers, team_id, key=None):
    return teacher.post(
        "/api/v1/labs",
        headers={**headers, "Idempotency-Key": key or str(uuid4())},
        json={"mission_id": "first-contact", "mission_version": 1, "team_id": team_id},
    )


def prepare(team_users):
    _, student, second, _, _ = team_users
    teacher, headers = logged_in("teacher")
    created = team(teacher, headers, [student, second])
    assert created.status_code == 201
    result = lab(teacher, headers, created.json()["id"])
    assert result.status_code == 202
    lab_id = result.json()["lab_id"]
    with connect() as db:
        db.execute(
            "UPDATE labs SET state='READY',target_ip='192.168.82.10',last_observed_at=now() WHERE id=%s",
            (lab_id,),
        )
        db.execute(
            "INSERT INTO lab_flags VALUES (%s,1,'find-service',%s)",
            (lab_id, hashlib.sha256(b"synthetic-team-flag").hexdigest()),
        )
    return teacher, headers, created.json()["id"], lab_id


def test_team_roles_scope_and_concurrent_idempotency(team_users, inventory):
    _, student, second, _, outsider = team_users
    teacher, headers = logged_in("teacher")
    pupil, pupil_headers = logged_in()
    assert team(pupil, pupil_headers, [student]).status_code == 403
    assert pupil.get("/api/v1/teams/roster").status_code == 403
    assert team(teacher, headers, [student, outsider]).status_code == 404
    assert team(teacher, headers, [student, student]).status_code == 422
    key = str(uuid4())
    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(pool.map(lambda _: team(teacher, headers, [student, second], key), range(5)))
    assert {r.status_code for r in results} == {201}
    assert len({r.json()["id"] for r in results}) == 1
    assert team(teacher, headers, [student], key).status_code == 409
    identity = results[0].json()["id"]
    assert pupil.get("/api/v1/teams").json()[0]["id"] == identity
    foreign, _ = logged_in("outsider")
    assert foreign.get("/api/v1/teams").json() == []
    assert lab(pupil, pupil_headers, identity).status_code == 403
    key = str(uuid4())
    first = lab(teacher, headers, identity, key)
    assert first.status_code == 202 and lab(teacher, headers, identity, key).json() == first.json()
    assert lab(teacher, headers, identity).status_code == 409
    with connect() as db:
        assert db.execute("SELECT count(*) AS n FROM lab_participants").fetchone()["n"] == 2


def test_team_operator_grants_are_independent_and_revoked_on_membership_loss(team_users, inventory):
    teacher, headers, team_id, lab_id = prepare(team_users)
    pupil, a_headers = logged_in()
    second, b_headers = logged_in("second")
    foreign, f_headers = logged_in("outsider")
    assert (
        teacher.post(f"/api/v1/labs/{lab_id}/terminal-grants", headers=headers).status_code == 403
    )
    assert foreign.get(f"/api/v1/labs/{lab_id}").status_code == 404
    assert (
        foreign.post(f"/api/v1/labs/{lab_id}/terminal-grants", headers=f_headers).status_code == 404
    )
    a = pupil.get(f"/api/v1/labs/{lab_id}").json()
    b = second.get(f"/api/v1/labs/{lab_id}").json()
    assert a["target_ip"] == "192.168.82.10" and b["target_ip"] == "192.168.92.10"
    assert a["can_operate"] and not a["can_manage"]
    assert (
        pupil.post(
            f"/api/v1/labs/{lab_id}/destroy", headers={**a_headers, "Idempotency-Key": str(uuid4())}
        ).status_code
        == 404
    )
    ga = issue(pupil, a_headers, lab_id)
    gb = issue(second, b_headers, lab_id)
    ca = pupil.post("/internal/terminal/consume", headers=a_headers, json=ga)
    cb = second.post("/internal/terminal/consume", headers=b_headers, json=gb)
    assert ca.status_code == cb.status_code == 200
    a_claim = json.loads(ca.json()["payload"])
    b_claim = json.loads(cb.json()["payload"])
    assert a_claim["user_id"] == b_claim["user_id"] == str(team_users[3])
    assert a_claim["operator_user_id"] == str(team_users[1]) and b_claim["operator_user_id"] == str(
        team_users[2]
    )
    assert pupil.post("/internal/terminal/check", headers=a_headers, json=ga).status_code == 200
    assert second.post("/internal/terminal/check", headers=b_headers, json=gb).status_code == 200
    with connect() as db:
        db.execute(
            "DELETE FROM team_members WHERE team_id=%s AND user_id=%s", (team_id, team_users[1])
        )
    assert pupil.post("/internal/terminal/check", headers=a_headers, json=ga).status_code == 403
    assert pupil.get(f"/api/v1/labs/{lab_id}").status_code == 404
    assert second.post("/internal/terminal/check", headers=b_headers, json=gb).status_code == 200


def test_team_score_awarded_once_across_actors_and_reset_separate_from_solo(team_users, inventory):
    teacher, headers, _, lab_id = prepare(team_users)
    clients = [logged_in(), logged_in("second")]

    def submit(pair, flag="synthetic-team-flag", generation=1):
        client, h = pair
        return client.post(
            f"/api/v1/labs/{lab_id}/objectives/find-service/attempts",
            headers={**h, "Idempotency-Key": str(uuid4())},
            json={"generation": generation, "flag": flag},
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, clients))
    assert all(r.status_code == 200 and r.json()["accepted"] for r in results)
    assert sum(r.json()["points_awarded"] for r in results) == 100
    assert all(
        pair[0].get(f"/api/v1/labs/{lab_id}/progress").json()["total_points"] == 100
        for pair in clients
    )
    assert submit((teacher, headers)).status_code == 403
    with connect() as db:
        assert db.execute("SELECT count(*) AS n FROM score_ledger").fetchone()["n"] == 0
        assert db.execute("SELECT count(*) AS n FROM team_score_ledger").fetchone()["n"] == 1
        db.execute("UPDATE labs SET generation=2 WHERE id=%s", (lab_id,))
        db.execute(
            "INSERT INTO lab_flags VALUES (%s,2,'find-service',%s)",
            (lab_id, hashlib.sha256(b"synthetic-new-flag").hexdigest()),
        )
    assert submit(clients[0], generation=2).json()["accepted"] is False
    result = submit(clients[1], flag="synthetic-new-flag", generation=2).json()
    assert result["accepted"] and result["points_awarded"] == 0 and result["total_points"] == 100


@pytest.mark.parametrize("change", ["missing", "extra", "swapped", "stopped", "absent"])
def test_reconciliation_checks_every_assigned_operator(team_users, inventory, change):
    from test_reconcile import now, synthetic_snapshot

    from cra.node_contracts import NodeOperator
    from cra.reconcile import apply_inventory

    teacher, _, _, lab_id = prepare(team_users)
    snapshot = synthetic_snapshot(lab_id)
    observed = snapshot.labs[0]
    prefix = observed.operator[:-1]
    observed.operators = [
        NodeOperator(user_id=user, name=prefix + "op"[index], present=True, running=True)
        for index, user in enumerate(team_users[1:3])
    ]
    if change == "missing":
        observed.operators = []
    elif change == "extra":
        observed.operators.append(
            NodeOperator(user_id=uuid4(), name=prefix + "q", present=True, running=True)
        )
    elif change == "swapped":
        observed.operators[0].user_id, observed.operators[1].user_id = (
            observed.operators[1].user_id,
            observed.operators[0].user_id,
        )
    elif change == "stopped":
        observed.operators[1].running = False
    else:
        observed.operators[1].running = False
        observed.operators[1].present = False
    apply_inventory(snapshot, now())
    result = teacher.get("/api/v1/labs/" + lab_id).json()
    assert result["state"] == "ERROR"
    assert result["error_code"] == (
        {"stopped": "runtime_drift", "absent": "resource_missing"}.get(change, "ownership_mismatch")
    )


def test_demoted_team_owner_cannot_mutate_shared_resources(team_users, inventory):
    teacher, headers, _, lab_id = prepare(team_users)
    assert teacher.get("/api/v1/labs/" + lab_id).json()["can_manage"]
    with connect() as db:
        db.execute(
            "UPDATE memberships SET role='student' WHERE user_id=%s AND organization_id=%s",
            (team_users[3], team_users[0]),
        )
    assert not teacher.get("/api/v1/labs/" + lab_id).json()["can_manage"]
    for action in ["start", "stop", "reset", "destroy"]:
        assert (
            teacher.post(
                f"/api/v1/labs/{lab_id}/{action}",
                headers={**headers, "Idempotency-Key": str(uuid4())},
            ).status_code
            == 403
        )


def test_team_capacity_counts_each_operator_disk_and_memory(team_users, inventory):
    teacher, headers = logged_in("teacher")
    created = team(teacher, headers, team_users[1:4])
    assert created.status_code == 201
    inventory["free_disk_gib"] = 30
    assert lab(teacher, headers, created.json()["id"]).json()["detail"] == "capacity_unavailable"
    inventory["free_disk_gib"] = 100
    inventory["available_memory_mib"] = 7500
    assert lab(teacher, headers, created.json()["id"]).json()["detail"] == "capacity_unavailable"
    with connect() as db:
        assert db.execute("SELECT count(*) AS n FROM labs").fetchone()["n"] == 0


def test_older_active_team_lab_is_not_hidden_by_destroyed_history(team_users, inventory):
    teacher, _, _, lab_id = prepare(team_users)
    with connect() as db:
        for _ in range(21):
            db.execute(
                "INSERT INTO labs(id,organization_id,user_id,mission_id,mission_version,state,desired_state) VALUES (%s,%s,%s,'first-contact',1,'DESTROYED','DESTROYED')",
                (uuid4(), team_users[0], team_users[3]),
            )
    assert teacher.get("/api/v1/labs").json()[0]["id"] == lab_id


def test_new_team_reserves_space_for_existing_thin_disks(team_users, inventory):
    teacher, headers, _, _ = prepare(team_users)
    another = team(teacher, headers, [team_users[3]])
    assert another.status_code == 201
    # Existing 3-VM team reserves36GiB, new 2-VM team24GiB: 50 is insufficient.
    inventory["free_disk_gib"] = 50
    assert lab(teacher, headers, another.json()["id"]).json()["detail"] == "capacity_unavailable"
