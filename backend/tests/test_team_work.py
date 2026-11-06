"""Actual PostgreSQL collaboration/transition tests; node inventory is a policy fixture."""

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import test_identity
import test_labs
import test_teams
from test_identity import logged_in
from test_teams import lab, team

from cra.db import connect

identities = test_identity.identities
team_users = test_teams.team_users
inventory = test_labs.inventory


def test_shared_notebook_conflicts_scope_and_evidence(team_users):
    teacher, th = logged_in("teacher")
    student, sh = logged_in()
    second, seh = logged_in("second")
    outsider, oh = logged_in("outsider")
    tid = team(teacher, th, [team_users[1], team_users[2]]).json()["id"]
    url = f"/api/v1/teams/{tid}/notebook"
    assert student.get(url).json()["revision"] == 0
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(
            pool.map(
                lambda x: x[0].put(url, headers=x[1], json={"body": x[2], "revision": 0}),
                [(student, sh, "alpha"), (second, seh, "beta")],
            )
        )
    assert sorted(r.status_code for r in replies) == [200, 409]
    saved = student.get(url).json()
    winner = (student, sh) if saved["body"] == "alpha" else (second, seh)
    assert (
        winner[0].put(url, headers=winner[1], json={"body": saved["body"], "revision": 0}).json()
        == saved
    )
    assert saved["evidence_level"] == "submitted" and saved["updated_by"]
    assert (
        outsider.get(url).status_code
        == outsider.put(url, headers=oh, json={"body": "forbidden", "revision": 1}).status_code
        == 404
    )
    other_id = team(teacher, th, [team_users[3]]).json()["id"]
    assert student.get(f"/api/v1/teams/{other_id}/notebook").status_code == 404
    assert (
        student.put(
            url, headers=sh, json={"body": "x", "revision": 1, "evidence_level": "verified"}
        ).status_code
        == 422
    )
    with connect() as db:
        rows = db.execute(
            "SELECT payload FROM teaching_events WHERE kind='team.note.saved'"
        ).fetchall()
        assert rows and all("body" not in r["payload"] for r in rows)


def test_findings_idempotency_limits_pagination_and_literal_content(team_users):
    teacher, th = logged_in("teacher")
    student, sh = logged_in()
    foreign, _ = logged_in("outsider")
    tid = team(teacher, th, [team_users[1]]).json()["id"]
    url = f"/api/v1/teams/{tid}/findings"
    body = {
        "request_key": str(uuid4()),
        "body": "<img src=x onerror=alert(1)>",
        "source": "Min egen terminal",
    }
    first = student.post(url, headers=sh, json=body)
    assert (
        first.status_code == 201
        and first.json()["body"] == body["body"]
        and first.json()["evidence_level"] == "submitted"
    )
    assert student.post(url, headers=sh, json=body).json() == first.json()
    assert student.post(url, headers=sh, json={**body, "body": "changed"}).status_code == 409
    assert (
        student.post(url, headers=sh, json={**body, "evidence_level": "verified"}).status_code
        == 422
    )
    for _ in range(19):
        assert (
            student.post(url, headers=sh, json={**body, "request_key": str(uuid4())}).status_code
            == 201
        )
    assert (
        student.post(url, headers=sh, json={**body, "request_key": str(uuid4())}).status_code == 429
    )
    assert foreign.get(url).status_code == 404
    # Explicit old-history rows exercise the cursor independently of the write rate limit.
    with connect() as db:
        for _ in range(35):
            db.execute(
                "INSERT INTO team_findings(id,team_id,actor_id,request_key,body,source,created_at) VALUES (%s,%s,%s,%s,'old','fixture',now()-interval '2 minutes')",
                (uuid4(), tid, team_users[1], uuid4()),
            )
    page = student.get(url).json()
    assert len(page["items"]) == 50 and page["next_cursor"]
    last = student.get(url + "?before=" + page["next_cursor"]).json()
    assert len(last["items"]) == 5 and last["next_cursor"] is None
    assert len({x["id"] for x in page["items"] + last["items"]}) == 55
    assert student.get(url + "?before=" + str(uuid4())).status_code == 404


def test_roles_preserve_identity_and_creation_retry(team_users, inventory):
    teacher, th = logged_in("teacher")
    student, sh = logged_in()
    key = str(uuid4())
    members = [team_users[1], team_users[2]]
    created = team(teacher, th, members, key).json()
    tid = created["id"]
    result = lab(teacher, th, tid)
    assert result.status_code == 202
    body = {
        "revision": created["revision"],
        "members": [
            {"user_id": str(members[0]), "role": "reporter"},
            {"user_id": str(members[1]), "role": "coordinator"},
        ],
    }
    url = f"/api/v1/teams/{tid}/roles"
    assert student.put(url, headers=sh, json=body).status_code == 403
    changed = teacher.put(url, headers=th, json=body)
    assert changed.status_code == 200
    assert teacher.put(url, headers=th, json=body).json() == changed.json()
    assert team(teacher, th, members, key).status_code == 201
    assert (
        teacher.put(
            url,
            headers=th,
            json={**body, "members": [{"user_id": str(members[0]), "role": "operator"}]},
        ).status_code
        == 409
    )
    with connect() as db:
        assert [
            r["user_id"]
            for r in db.execute(
                "SELECT user_id FROM lab_participants WHERE lab_id=%s ORDER BY ordinal",
                (result.json()["lab_id"],),
            )
        ] == members


def stopped(lab_id):
    with connect() as db:
        db.execute(
            "UPDATE labs SET state='STOPPED',desired_state='STOPPED',last_observed_at=now() WHERE id=%s",
            (lab_id,),
        )
        db.execute(
            "UPDATE jobs SET status='SUCCEEDED' WHERE id=(SELECT current_job_id FROM labs WHERE id=%s)",
            (lab_id,),
        )


def test_transition_preserves_reservations_and_six_slots_with_two_active(team_users, inventory):
    teacher, th = logged_in("teacher")
    student, sh = logged_in()
    second, seh = logged_in("second")
    inventory["free_disk_gib"] = 500
    solos = []
    for client, headers in [(student, sh), (second, seh)]:
        response = test_labs.create(client, headers)
        assert response.status_code == 202
        solos.append(response.json()["lab_id"])
    tid = team(teacher, th, [team_users[1], team_users[2]]).json()["id"]
    assert lab(teacher, th, tid).json()["detail"] == "stop_solo_first"
    for identity in solos:
        stopped(identity)
    hold = lab(teacher, th, tid)
    assert hold.status_code == 202
    with connect() as db:
        assert (
            db.execute("SELECT slot FROM labs WHERE id=%s", (hold.json()["lab_id"],)).fetchone()[
                "slot"
            ]
            == 2
        )
    assert (
        student.post(
            f"/api/v1/labs/{solos[0]}/start", headers={**sh, "Idempotency-Key": str(uuid4())}
        ).json()["detail"]
        == "stop_team_first"
    )
    stopped(hold.json()["lab_id"])
    # Three unrelated start intents race under the same admission lock; only two fit.
    third = test_labs.create(teacher, th)
    assert third.status_code == 202
    stopped(third.json()["lab_id"])
    intents = [
        (student, sh, solos[0]),
        (second, seh, solos[1]),
        (teacher, th, third.json()["lab_id"]),
    ]
    with ThreadPoolExecutor(max_workers=3) as pool:
        replies = list(
            pool.map(
                lambda x: x[0].post(
                    "/api/v1/labs/" + x[2] + "/start",
                    headers={**x[1], "Idempotency-Key": str(uuid4())},
                ),
                intents,
            )
        )
    assert sorted(r.status_code for r in replies) == [202, 202, 409]
    with connect() as db:
        assert (
            db.execute("SELECT count(*) AS n FROM labs WHERE state<>'DESTROYED'").fetchone()["n"]
            == 4
        )


def test_recovery_start_credits_verified_running_own_memory(team_users, inventory):
    from test_reconcile import synthetic_snapshot

    student, sh = logged_in()
    created = test_labs.create(student, sh).json()
    lab_id = created["lab_id"]
    inventory.update(synthetic_snapshot(lab_id).model_dump(mode="json"))
    inventory["labs"][0]["running_memory_mib"] = 1024
    inventory["labs"][0]["target_running"] = False
    inventory["available_memory_mib"] = 4864
    with connect() as db:
        db.execute(
            "UPDATE labs SET state='ERROR',error_code='runtime_drift' WHERE id=%s", (lab_id,)
        )
    inventory["labs"][0]["ownership_valid"] = False
    url = "/api/v1/labs/" + lab_id + "/start"
    assert student.post(url, headers={**sh, "Idempotency-Key": str(uuid4())}).status_code == 409
    inventory["labs"][0]["ownership_valid"] = True
    assert student.post(url, headers={**sh, "Idempotency-Key": str(uuid4())}).status_code == 202


def test_eighteen_stopped_reservations_keep_unique_slots_and_reject_nineteenth(team_users, inventory):
    teacher, headers = logged_in("teacher")
    inventory["free_disk_gib"] = 500
    for slot in range(18):
        tid = team(teacher, headers, [team_users[3]]).json()["id"]
        response = lab(teacher, headers, tid)
        assert response.status_code == 202
        identity = response.json()["lab_id"]
        with connect() as db:
            assert (
                db.execute("SELECT slot FROM labs WHERE id=%s", (identity,)).fetchone()["slot"]
                == slot
            )
        stopped(identity)
    tid = team(teacher, headers, [team_users[3]]).json()["id"]
    response = lab(teacher, headers, tid)
    assert response.status_code == 409 and response.json()["detail"] == "capacity_unavailable"
    with connect() as db:
        assert (
            db.execute("SELECT count(*) AS n FROM labs WHERE state='STOPPED'").fetchone()["n"] == 18
        )
