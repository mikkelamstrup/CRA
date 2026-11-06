import hashlib
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import test_identity
import test_labs
import test_terminal
from test_identity import logged_in

from cra.db import connect

identities = test_identity.identities
inventory = test_labs.inventory
ready_lab = test_terminal.ready_lab
# Explicit synthetic fixture values, never flags from installed labs.
FLAG = "CRA{synthetic-test-only-generation-one}"
NEXT = "CRA{synthetic-test-only-generation-two}"


def verifier(lab_id, flag=FLAG, generation=1):
    with connect() as db:
        db.execute(
            "INSERT INTO lab_flags VALUES (%s,%s,%s,%s)",
            (lab_id, generation, "find-service", hashlib.sha256(flag.encode()).hexdigest()),
        )


def submit(client, headers, lab_id, flag=FLAG, generation=1, key=None):
    return client.post(
        "/api/v1/labs/" + lab_id + "/objectives/find-service/attempts",
        headers={**headers, "Idempotency-Key": key or str(uuid4())},
        json={"flag": flag, "generation": generation},
    )


def test_concurrent_correct_flags_award_once_with_atomic_event_and_idempotency(ready_lab):
    client, headers, lab_id, _ = ready_lab
    verifier(lab_id)
    wrong = submit(client, headers, lab_id, "synthetic-wrong").json()
    assert (
        not wrong["accepted"] and wrong["points_awarded"] == 0 and wrong["evidence_level"] == "none"
    )
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: submit(client, headers, lab_id).json(), range(8)))
    assert all(row["accepted"] for row in results)
    assert sum(row["points_awarded"] for row in results) == 100
    key = str(uuid4())
    response = submit(client, headers, lab_id, key=key)
    assert submit(client, headers, lab_id, key=key).json() == response.json()
    assert submit(client, headers, lab_id, flag="changed", key=key).status_code == 409
    with connect() as db:
        assert db.execute("SELECT count(*) AS n FROM score_ledger").fetchone()["n"] == 1
        events = db.execute(
            "SELECT event FROM outbox WHERE event->'payload'->>'kind'='objective.completed'"
        ).fetchall()
        assert len(events) == 1 and events[0]["event"]["payload"]["points_delta"] == 100
        assert events[0]["event"]["evidence_level"] == "validated"
        assert FLAG not in str(db.execute("SELECT * FROM objective_attempts").fetchall())
    progress = client.get("/api/v1/labs/" + lab_id + "/progress").json()
    assert progress == {
        "total_points": 100,
        "completed_objectives": ["find-service"],
        "earned_objectives": ["find-service"],
    }
    assert FLAG not in client.get("/api/v1/missions").text
    assert "verifier" not in client.get("/api/v1/labs/" + lab_id).text


def test_scope_validator_unavailable_rate_limit_and_generation(ready_lab):
    client, headers, lab_id, _ = ready_lab
    assert submit(client, headers, lab_id).status_code == 503
    other, other_headers = logged_in("outsider")
    assert submit(other, other_headers, lab_id).status_code == 404
    assert other.get("/api/v1/labs/" + lab_id + "/progress").status_code == 404
    verifier(lab_id)
    assert submit(client, headers, lab_id, generation=2).status_code == 409
    for _ in range(20):
        assert submit(client, headers, lab_id, flag="wrong").status_code == 200
    assert submit(client, headers, lab_id).status_code == 429
    with connect() as db:
        assert db.execute("SELECT count(*) AS n FROM score_ledger").fetchone()["n"] == 0


def test_reset_fences_grants_and_score_survives_without_repeat_points(ready_lab):
    client, headers, lab_id, _ = ready_lab
    verifier(lab_id)
    earned_key = str(uuid4())
    assert submit(client, headers, lab_id, key=earned_key).json()["points_awarded"] == 100
    grant = test_terminal.issue(client, headers, lab_id)
    assert client.post("/internal/terminal/consume", headers=headers, json=grant).status_code == 200
    key = str(uuid4())
    url = "/api/v1/labs/" + lab_id + "/reset"
    first = client.post(url, headers={**headers, "Idempotency-Key": key})
    assert first.status_code == 202
    assert client.post(url, headers={**headers, "Idempotency-Key": key}).json() == first.json()
    lab = client.get("/api/v1/labs/" + lab_id).json()
    assert lab["generation"] == 2 and lab["state"] == "RESETTING"
    assert submit(client, headers, lab_id, key=earned_key).status_code == 409
    assert client.post("/internal/terminal/check", headers=headers, json=grant).status_code == 403
    # API policy fixture only. Real reset proof is the browser/KVM acceptance.
    with connect() as db:
        db.execute("UPDATE labs SET state='READY'")
    verifier(lab_id, NEXT, 2)
    assert submit(client, headers, lab_id, generation=1).status_code == 409
    assert not submit(client, headers, lab_id, generation=2).json()["accepted"]
    answer = submit(client, headers, lab_id, flag=NEXT, generation=2).json()
    assert answer["accepted"] and answer["points_awarded"] == 0 and answer["total_points"] == 100
    with connect() as db:
        assert db.execute("SELECT count(*) AS n FROM score_ledger").fetchone()["n"] == 1
        assert db.execute("SELECT count(*) AS n FROM lab_completions").fetchone()["n"] == 2
        assert (
            db.execute(
                "SELECT sum((event->'payload'->>'points_delta')::integer) AS n FROM outbox WHERE event->'payload'->>'kind'='objective.completed'"
            ).fetchone()["n"]
            == 100
        )
