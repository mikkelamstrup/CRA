"""PostgreSQL replay authorization and bounded historical projection fixtures."""

from datetime import timedelta
from uuid import UUID, uuid4

import test_identity
import test_labs
import test_terminal
from psycopg.types.json import Jsonb
from test_identity import logged_in
from test_reconcile import now, synthetic_snapshot
from test_traffic import add_window

from cra.contracts import Event, LifecyclePayload, ObjectivePayload
from cra.db import connect
from cra.jobs import publish
from cra.reconcile import apply_inventory
from cra.traffic import prune

identities = test_identity.identities
inventory = test_labs.inventory
ready_lab = test_terminal.ready_lab
BASE = "/api/v1/command-center/replay"


def domain(lab, ids, generation=1, validated=False, instant=None):
    event = Event(
        schema_version=1,
        event_id=uuid4(),
        organization_id=ids[0],
        lab_id=UUID(lab),
        generation=generation,
        actor_id=ids[2],
        occurred_at=instant or now(),
        source="score" if validated else "control",
        evidence_level="validated" if validated else "system",
        payload=ObjectivePayload(
            kind="objective.completed", objective_id="find-service", points_delta=100
        )
        if validated
        else LifecyclePayload(kind="lab.lifecycle", state="READY"),
    )
    with connect() as db:
        publish(db, event)
    return event


def test_scope_role_private_fields_and_recorded_sources(ready_lab, identities):
    student, _, lab, _ = ready_lab
    teacher, _ = logged_in("teacher")
    foreign, _ = logged_in("outsider")
    domain(lab, identities)
    domain(lab, identities, validated=True)
    apply_inventory(add_window(synthetic_snapshot(lab)), now())
    assert student.get(BASE + "/labs").status_code == 403
    assert student.get(BASE + "/labs/" + lab).status_code == 403
    assert foreign.get(BASE + "/labs/" + lab).status_code == 404
    assert foreign.get(BASE + "/labs").json()["labs"] == []
    body = teacher.get(BASE + "/labs/" + lab).json()
    assert body["historical"] and body["generation"] == 1
    assert {e["evidence_level"] for e in body["events"]} == {"system", "observed", "validated"}
    observed = next(e for e in body["events"] if e["source"] == "lab_link")
    assert (
        observed["operator_number"] == 1
        and observed["window"]["flows"][0]["destination_port"] == 8080
    )
    assert not any(
        s in str(body) for s in ("password", "private_key", "actor_id", "CRA{", "user_id")
    )
    with connect() as db:
        db.execute("UPDATE memberships SET role='student' WHERE user_id=%s", (identities[3],))
    assert teacher.get(BASE + "/labs/" + lab).status_code == 403


def test_generations_duplicates_and_destroyed_lab_history(ready_lab, identities):
    _, _, lab, _ = ready_lab
    teacher, _ = logged_in("teacher")
    event = domain(lab, identities, validated=True)
    with connect() as db:
        publish(db, event)
    snap = add_window(synthetic_snapshot(lab))
    apply_inventory(snap, now())
    apply_inventory(snap, now())
    with connect() as db:
        db.execute(
            "UPDATE labs SET generation=2,state='DESTROYED',updated_at=now() WHERE id=%s", (lab,)
        )
    domain(lab, identities, generation=2)
    older = teacher.get(BASE + "/labs/" + lab, params={"generation": 1}).json()
    assert older["generations"] == [2, 1] and all(e["generation"] == 1 for e in older["events"])
    assert sum(e["event_id"] == str(event.event_id) for e in older["events"]) == 1
    assert sum(e["source"] == "lab_link" for e in older["events"]) == 1
    current = teacher.get(BASE + "/labs/" + lab).json()
    assert current["generation"] == 2 and all(e["generation"] == 2 for e in current["events"])
    assert teacher.get(BASE + "/labs/" + lab, params={"generation": 3}).status_code == 404
    assert teacher.get(BASE + "/labs").json()["labs"][0]["state"] == "DESTROYED"


def test_time_ranges_are_explicit_and_bounded(ready_lab, identities):
    _, _, lab, _ = ready_lab
    teacher, _ = logged_in("teacher")
    old = now() - timedelta(days=3)
    event = domain(lab, identities, validated=True, instant=old)
    assert str(event.event_id) not in teacher.get(BASE + "/labs/" + lab).text
    params = {
        "from_time": (old - timedelta(minutes=1)).isoformat(),
        "to_time": (old + timedelta(minutes=1)).isoformat(),
    }
    body = teacher.get(BASE + "/labs/" + lab, params=params).json()
    assert [e["event_id"] for e in body["events"]] == [str(event.event_id)]
    for start, end in [
        ("2026-01-01T00:00:00", "2026-01-01T01:00:00"),
        ("2026-01-01T00:00:00Z", "2026-01-03T00:00:00Z"),
        ("2026-01-02T00:00:00Z", "2026-01-01T00:00:00Z"),
    ]:
        assert (
            teacher.get(
                BASE + "/labs/" + lab, params={"from_time": start, "to_time": end}
            ).status_code
            == 422
        )


def test_limits_and_retention_never_erase_domain_evidence(ready_lab, identities):
    _, _, lab, _ = ready_lab
    teacher, _ = logged_in("teacher")
    snap = add_window(synthetic_snapshot(lab))
    apply_inventory(snap, now())
    with connect() as db:
        flow = db.execute("SELECT * FROM flow_history WHERE lab_id=%s", (lab,)).fetchone()
        for i in range(2, 105):
            payload = {**flow["payload"], "sequence": i, "source_sequence": i * 2}
            db.execute(
                "INSERT INTO flow_history(event_id,schema_version,organization_id,lab_id,generation,operator_user_id,epoch,window_sequence,observed_at,payload) VALUES (%s,1,%s,%s,1,%s,%s,%s,%s,%s)",
                (
                    uuid4(),
                    identities[0],
                    lab,
                    identities[2],
                    flow["epoch"],
                    i,
                    now(),
                    Jsonb(payload),
                ),
            )
    for _ in range(104):
        domain(lab, identities)
    body = teacher.get(BASE + "/labs/" + lab).json()
    assert body["domain_truncated"] and body["flow_truncated"] and len(body["events"]) == 200
    assert [e["occurred_at"] for e in body["events"]] == sorted(
        e["occurred_at"] for e in body["events"]
    )
    with connect() as db:
        db.execute("UPDATE flow_history SET received_at=now()-interval '25 hours'")
        prune(db)
    body = teacher.get(BASE + "/labs/" + lab).json()
    assert len(body["events"]) == 100 and body["flow_retention_hours"] == 24
    assert all(e["source"] == "control" for e in body["events"])


def test_event_receipt_is_measured_after_transaction_start_with_honest_legacy_fallback(
    ready_lab, identities
):
    _, _, lab, _ = ready_lab
    teacher, _ = logged_in("teacher")
    original = domain(lab, identities)
    with connect() as db:
        began = db.execute("SELECT now() AS t").fetchone()["t"]
        db.execute("SELECT pg_sleep(0.05)")
        event = original.model_copy(update={"event_id": uuid4(), "occurred_at": now()})
        publish(db, event)
        row = db.execute(
            "SELECT created_at,recorded_at,event_occurred_at FROM outbox WHERE event_id=%s",
            (event.event_id,),
        ).fetchone()
        assert row["created_at"] == began < event.occurred_at <= row["recorded_at"]
        assert row["event_occurred_at"] == event.occurred_at
    body = teacher.get(BASE + "/labs/" + lab).json()
    item = next(e for e in body["events"] if e["event_id"] == str(event.event_id))
    assert item["receipt_basis"] == "recorded"
    with connect() as db:
        db.execute("UPDATE outbox SET recorded_at=NULL WHERE event_id=%s", (event.event_id,))
    item = next(
        e
        for e in teacher.get(BASE + "/labs/" + lab).json()["events"]
        if e["event_id"] == str(event.event_id)
    )
    assert item["receipt_basis"] == "transaction_start_legacy"
