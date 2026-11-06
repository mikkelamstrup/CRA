"""Actual PostgreSQL fences/authorization; packet metadata is explicitly synthetic."""

import time
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
import test_identity
import test_labs
import test_terminal
from test_identity import logged_in
from test_reconcile import now, synthetic_snapshot

from cra.db import connect
from cra.flow_contracts import FlowWindow, HeaderFlow
from cra.reconcile import apply_inventory
from cra.traffic import prune

identities = test_identity.identities
inventory = test_labs.inventory
ready_lab = test_terminal.ready_lab


def add_window(snapshot, sequence=1, port=8080):
    capture = snapshot.labs[0].captures[0]
    flow = HeaderFlow(
        direction="operator_to_target",
        src="192.168.82.20",
        dst="192.168.82.10",
        protocol=6,
        source_port=1234,
        destination_port=port,
        packets=2,
        bytes=160,
    )
    capture.windows = [
        FlowWindow(
            sequence=sequence,
            source_sequence=sequence * 2,
            observed_at=time.time(),
            flows=[flow],
            partial=False,
            omitted_packets=0,
        )
    ]
    capture.last_window = sequence
    capture.packets = sequence * 2
    capture.bytes = sequence * 160
    capture.observed_at = time.time()
    return snapshot


def url(lab):
    return f"/api/v1/command-center/labs/{lab}/traffic"


def test_teacher_scope_numeric_observations_never_award_points(ready_lab, identities):
    student, _, lab, _ = ready_lab
    teacher, _ = logged_in("teacher")
    other, _ = logged_in("outsider")
    snap = add_window(synthetic_snapshot(lab))
    apply_inventory(snap, now())
    assert student.get(url(lab)).status_code == 403
    assert other.get(url(lab)).status_code == 404
    value = teacher.get(url(lab)).json()
    assert not value["stale"] and value["captures"][0]["connected"]
    event = value["events"][0]
    assert event["source"] == "lab_link" and event["evidence_level"] == "observed"
    assert (
        event["operator_user_id"] == str(identities[2])
        and event["window"]["flows"][0]["destination_port"] == 8080
    )
    assert not any(
        key in str(value) for key in ("private_key", "objective_verifiers", "CRA{", "help_requests")
    )
    with connect() as db:
        assert db.execute("SELECT count(*) AS n FROM lab_completions").fetchone()["n"] == 0
        db.execute("UPDATE memberships SET role='student' WHERE user_id=%s", (identities[3],))
    assert teacher.get(url(lab)).status_code == 403


def test_replay_dedup_rejects_changed_window_and_old_epoch_snapshot(ready_lab):
    _, _, lab, _ = ready_lab
    teacher, _ = logged_in("teacher")
    original = add_window(synthetic_snapshot(lab))
    apply_inventory(original, now())
    apply_inventory(original, now())
    assert len(teacher.get(url(lab)).json()["events"]) == 1
    changed = original.model_copy(deep=True)
    changed.labs[0].captures[0].windows[0].flows[0].destination_port = 22
    with pytest.raises(ValueError, match="Changed capture window"):
        apply_inventory(changed, now())
    delayed_before = now()
    replacement = original.model_copy(deep=True)
    replacement.labs[0].captures[0].epoch = uuid4()
    add_window(replacement, port=22)
    apply_inventory(replacement, now())
    assert apply_inventory(original, delayed_before) == 0
    value = teacher.get(url(lab)).json()
    assert value["captures"][0]["epoch"] == str(replacement.labs[0].captures[0].epoch)
    assert (
        len(value["events"]) == 1
        and value["events"][0]["window"]["flows"][0]["destination_port"] == 22
    )
    with connect() as db:
        assert db.execute("SELECT count(*) AS n FROM flow_history").fetchone()["n"] == 2


def test_old_generation_wrong_assignment_and_regressive_counters(ready_lab):
    _, _, lab, _ = ready_lab
    snap = add_window(synthetic_snapshot(lab))
    apply_inventory(snap, now())
    wrong = snap.model_copy(deep=True)
    wrong.labs[0].captures[0].operator_user_id = uuid4()
    with pytest.raises(ValueError, match="Capture assignment"):
        apply_inventory(wrong, now())
    wrong = snap.model_copy(deep=True)
    wrong.labs[0].captures[0].packets = 0
    with pytest.raises(ValueError, match="Regressive capture"):
        apply_inventory(wrong, now())
    with connect() as db:
        db.execute("UPDATE labs SET generation=2,updated_at=now() WHERE id=%s", (lab,))
    apply_inventory(snap, now())
    teacher, _ = logged_in("teacher")
    value = teacher.get(url(lab)).json()
    assert value["generation"] == 2 and value["captures"] == [] and value["events"] == []
    with connect() as db:
        assert db.execute("SELECT count(*) AS n FROM flow_history").fetchone()["n"] == 1


def test_lost_link_changes_actual_lab_readiness_but_ring_loss_does_not(ready_lab):
    student, headers, lab, _ = ready_lab
    teacher, _ = logged_in("teacher")
    snap = add_window(synthetic_snapshot(lab), sequence=9)
    apply_inventory(snap, now())
    assert teacher.get(url(lab)).json()["captures"][0]["poll_missed_windows"] == 8
    assert student.get("/api/v1/labs/" + lab).json()["state"] == "READY"
    add_window(snap, sequence=12)
    apply_inventory(snap, now())
    assert teacher.get(url(lab)).json()["captures"][0]["poll_missed_windows"] == 10
    snap.labs[0].captures[0].connected = False
    snap.labs[0].capture_ready = False
    apply_inventory(snap, now())
    assert student.get("/api/v1/labs/" + lab).json()["error_code"] == "runtime_drift"
    assert (
        student.post("/api/v1/labs/" + lab + "/terminal-grants", headers=headers).status_code == 409
    )
    assert teacher.get(url(lab)).json()["stale"]
    snap.labs[0].captures[0].connected = True
    snap.labs[0].capture_ready = True
    apply_inventory(snap, now())
    assert student.get("/api/v1/labs/" + lab).json()["state"] == "READY"
    with connect() as db:
        db.execute("UPDATE lab_capture_snapshots SET received_at=now()-interval '21 seconds'")
    assert teacher.get(url(lab)).json()["stale"]


def test_history_retention_is_bounded_across_epochs_and_age(ready_lab, monkeypatch):
    _, _, lab, _ = ready_lab
    monkeypatch.setattr("cra.traffic.PER_LAB_LIMIT", 4)
    monkeypatch.setattr("cra.traffic.GLOBAL_LIMIT", 3)
    snap = synthetic_snapshot(lab)
    for i in range(7):
        snap.labs[0].captures[0].epoch = uuid4()
        add_window(snap, port=8000 + i)
        apply_inventory(snap, now())
    with connect() as db:
        rows = db.execute("SELECT * FROM flow_history ORDER BY sequence").fetchall()
        assert len(rows) == 3
        assert [r["payload"]["flows"][0]["destination_port"] for r in rows] == [8004, 8005, 8006]
        db.execute(
            "UPDATE flow_history SET received_at=%s WHERE event_id=%s",
            (now() - timedelta(hours=25), rows[0]["event_id"]),
        )
        prune(db)
        assert db.execute("SELECT count(*) AS n FROM flow_history").fetchone()["n"] == 2
        db.execute("DELETE FROM labs WHERE id=%s", (UUID(lab),))
        assert db.execute("SELECT count(*) AS n FROM lab_capture_snapshots").fetchone()["n"] == 0
        assert db.execute("SELECT count(*) AS n FROM flow_history").fetchone()["n"] == 0
