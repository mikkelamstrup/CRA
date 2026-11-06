"""Real PostgreSQL fencing/reservation tests. Inventory is an explicit fixture."""

import hashlib
import hmac
import json
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
import test_identity
import test_labs
from psycopg.errors import CheckViolation, UniqueViolation
from test_identity import logged_in
from test_reconcile import now, synthetic_snapshot
from test_terminal import issue

from cra.compute_nodes import node, nodes
from cra.db import connect
from cra.jobs import claim
from cra.reconcile import apply_inventory, once
from cra.worker import process

identities = test_identity.identities


@pytest.fixture
def two_nodes(tmp_path, monkeypatch):
    config = tmp_path / "nodes.json"
    config.write_text(
        json.dumps(
            [
                {"id": 1, "host": "10.0.2.2", "accept_new": True},
                {"id": 2, "host": "100.64.0.2", "accept_new": True},
            ]
        )
    )
    monkeypatch.setenv("CRA_NODES_FILE", str(config))
    # Each node can accept one solo with 4 GiB reserve; queued jobs are not in inventory.
    inventories = {
        identity: {
            "node_id": identity,
            "schema_version": 1,
            "connected": True,
            "max_labs": 15,
            "observed_at": time.time(),
            "architecture": "x86_64",
            "available_memory_mib": 6500,
            "physical_memory_mib": 6500,
            "running_declared_memory_mib": 0,
            "free_disk_gib": 500,
            "labs": [],
        }
        for identity in (1, 2)
    }
    monkeypatch.setattr("cra.labs.node_request", lambda path, *, node_id=1: inventories[node_id])
    return config, inventories


def test_registry_default_unknown_and_malformed_fail_closed(tmp_path, monkeypatch):
    config = tmp_path / "nodes.json"
    monkeypatch.setenv("CRA_NODES_FILE", str(config))
    assert list(nodes()) == [1]
    with pytest.raises(ValueError):
        node(2)
    for value in [
        [],
        {},
        [{"id": True, "host": "10.0.2.2", "accept_new": True}],
        [{"id": 2, "host": "127.0.0.1", "accept_new": True}],
        [{"id": 2, "host": "100.64.0.2", "accept_new": True, "certs": "/tmp/x"}],
    ]:
        config.write_text(json.dumps(value))
        with pytest.raises(ValueError):
            nodes()


def test_parallel_placement_reserves_queued_ram_and_reuses_slot_only_across_nodes(
    identities, two_nodes
):
    clients = [logged_in(name) for name in ("student", "teacher", "outsider")]
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda pair: test_labs.create(*pair), clients))
    assert sorted(r.status_code for r in results) == [202, 202, 409]
    with connect() as db:
        rows = db.execute("SELECT node_id,slot,state FROM labs ORDER BY node_id").fetchall()
        assert rows == [
            {"node_id": 1, "slot": 0, "state": "QUEUED"},
            {"node_id": 2, "slot": 0, "state": "QUEUED"},
        ]
        jobs = db.execute(
            "SELECT payload->>'node_id' AS node_id FROM jobs ORDER BY node_id"
        ).fetchall()
        assert [r["node_id"] for r in jobs] == ["1", "2"]
    with pytest.raises(CheckViolation), connect() as db:
        db.execute("UPDATE labs SET node_id=3-node_id")
    with pytest.raises(UniqueViolation), connect() as db:
        original = db.execute("SELECT * FROM labs WHERE node_id=1").fetchone()
        db.execute(
            "INSERT INTO labs(id,organization_id,user_id,mission_id,mission_version,state,slot,node_id) VALUES(%s,%s,%s,'first-files',1,'STOPPED',0,1)",
            (uuid4(), original["organization_id"], original["user_id"]),
        )


def test_drain_wrong_identity_unknown_capacity_and_stale_observation(identities, two_nodes):
    config, inventories = two_nodes
    config.write_text(json.dumps([{"id": 2, "host": "100.64.0.2", "accept_new": True}]))
    client, headers = logged_in()
    for field, value in [
        ("node_id", 1),
        ("physical_memory_mib", None),
        ("running_declared_memory_mib", None),
        ("observed_at", time.time() - 60),
    ]:
        original = inventories[2][field]
        inventories[2][field] = value
        assert test_labs.create(client, headers).status_code == 503
        inventories[2][field] = original
    result = test_labs.create(client, headers)
    assert result.status_code == 202
    with connect() as db:
        row = db.execute(
            "SELECT node_id FROM labs WHERE id=%s", (result.json()["lab_id"],)
        ).fetchone()
        assert row["node_id"] == 2


def test_wrong_job_node_never_calls_node_and_legacy_job_routes_to_one(
    identities, two_nodes, monkeypatch
):
    client, headers = logged_in()
    assert test_labs.create(client, headers).status_code == 202
    with connect() as db:
        db.execute("UPDATE jobs SET payload=jsonb_set(payload,'{node_id}','2')")
    called = []
    monkeypatch.setattr("cra.worker.request", lambda *a, **kw: called.append(a))
    process(claim("wrong-node"))
    assert not called
    with connect() as db:
        assert db.execute("SELECT status FROM jobs").fetchone()["status"] == "FAILED"
        db.execute("UPDATE jobs SET status='QUEUED',payload=payload-'node_id',available_at=now()")
    from cra.node_client import NodeUnavailable

    def unavailable(path, command):
        called.append(command.node_id)
        raise NodeUnavailable()

    monkeypatch.setattr("cra.worker.request", unavailable)
    process(claim("legacy-node"))
    assert called == [1]


def test_reconcile_node_outage_and_foreign_lab_quarantine_are_scoped(
    identities, two_nodes, monkeypatch
):
    first, fh = logged_in()
    second, sh = logged_in("teacher")
    a = test_labs.create(first, fh).json()["lab_id"]
    b = test_labs.create(second, sh).json()["lab_id"]
    with connect() as db:
        db.execute("UPDATE labs SET state='READY',last_observed_at=now()")
    ia = synthetic_snapshot(a)
    ib = synthetic_snapshot(b).model_copy(update={"node_id": 2})
    assert apply_inventory(ia, now(), 1) == 0
    assert apply_inventory(None, now(), 2) == 1
    with connect() as db:
        assert db.execute("SELECT state FROM labs WHERE id=%s", (a,)).fetchone()["state"] == "READY"
    assert apply_inventory(ib, now(), 2) == 1
    with pytest.raises(ValueError):
        apply_inventory(ia, now(), 2)
    # Same UUID observed on another host can only quarantine, never authorize destruction.
    foreign = ia.model_copy(update={"node_id": 2})
    apply_inventory(foreign, now(), 2)
    with connect() as db:
        row = db.execute(
            "SELECT * FROM node_orphans WHERE node_id=2 AND lab_id=%s", (a,)
        ).fetchone()
        assert (
            row["status"] == "QUARANTINED" and not row["cleanup_reserved"] and row["job_id"] is None
        )
        assert db.execute("SELECT state FROM labs WHERE id=%s", (a,)).fetchone()["state"] == "READY"
    # Separate outage scenario starts with a fresh synthetic READY row.
    with connect() as db:
        db.execute(
            "UPDATE labs SET state='READY',error_code=NULL,last_observed_at=now() WHERE id=%s", (b,)
        )
    # Removing configuration still marks its persisted labs unavailable; no fallback to node 1.
    two_nodes[0].write_text(json.dumps([{"id": 1, "host": "10.0.2.2", "accept_new": True}]))
    monkeypatch.setattr("cra.reconcile.request", lambda path: ia.model_dump(mode="json"))
    once()
    with connect() as db:
        assert (
            db.execute("SELECT error_code FROM labs WHERE id=%s", (b,)).fetchone()["error_code"]
            == "node_unavailable"
        )


def test_node_two_claim_uses_own_key_and_complete_binding(
    identities, two_nodes, tmp_path, monkeypatch
):
    two_nodes[0].write_text(json.dumps([{"id": 2, "host": "100.64.0.2", "accept_new": True}]))
    root = tmp_path / "terminal-signing.key"
    root.write_bytes(b"synthetic-node-one")
    (tmp_path / "node-signing").mkdir()
    (tmp_path / "node-signing/2.key").write_bytes(b"synthetic-node-two")
    monkeypatch.setenv("CRA_TERMINAL_SIGNING_KEY", str(root))
    client, headers = logged_in()
    lab = test_labs.create(client, headers).json()["lab_id"]
    with connect() as db:
        db.execute("UPDATE labs SET state='READY',last_observed_at=now()")
    signed = client.post(
        "/internal/terminal/consume", headers=headers, json=issue(client, headers, lab)
    ).json()
    claims = json.loads(signed["payload"])
    assert claims["node_id"] == 2 and claims["lab_id"] == lab
    assert (
        signed["signature"]
        == hmac.new(b"synthetic-node-two", signed["payload"].encode(), hashlib.sha256).hexdigest()
    )
    assert "private_key" not in signed


def test_node_two_orphan_cannot_use_node_one_history_after_lab_loss(identities, two_nodes):
    client, headers = logged_in()
    lab = test_labs.create(client, headers).json()["lab_id"]
    observed = synthetic_snapshot(lab).model_copy(update={"node_id": 2})
    with connect() as db:
        db.execute("DELETE FROM labs WHERE id=%s", (lab,))
    apply_inventory(observed, now(), 2)
    with connect() as db:
        db.execute("UPDATE node_orphans SET first_seen_at=now()-interval '1 minute'")
    apply_inventory(observed, now(), 2)
    with connect() as db:
        row = db.execute("SELECT * FROM node_orphans").fetchone()
        assert (
            row["node_id"] == 2 and row["status"] == "QUARANTINED" and not row["cleanup_reserved"]
        )
        assert (
            db.execute("SELECT count(*) AS n FROM jobs WHERE kind='orphan-destroy'").fetchone()["n"]
            == 0
        )


def test_upgrade_from_schema23_preserves_rows_jobs_slots_and_cleanup_fences(
    database, tmp_path, monkeypatch
):
    import shutil

    from cra import db as database_module

    legacy = tmp_path / "migrations"
    legacy.mkdir()
    for path in database_module.MIGRATIONS.glob("*.sql"):
        if int(path.name.split("_")[0]) <= 23:
            shutil.copyfile(path, legacy / path.name)
    with connect() as db:
        db.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    with monkeypatch.context() as patch:
        patch.setattr(database_module, "MIGRATIONS", legacy)
        assert database_module.migrate() == 23
    org, user, lab, orphan, job = [uuid4() for _ in range(5)]
    with connect() as db:
        db.execute("INSERT INTO organizations VALUES(%s,'synthetic')", (org,))
        db.execute(
            "INSERT INTO users(id,username,password_hash) VALUES(%s,'upgrade','disabled')", (user,)
        )
        db.execute("INSERT INTO memberships VALUES(%s,%s,'student')", (user, org))
        db.execute(
            "INSERT INTO labs(id,organization_id,user_id,mission_id,mission_version,state,slot,generation) VALUES(%s,%s,%s,'first-files',1,'STOPPED',17,8)",
            (lab, org, user),
        )
        db.execute(
            "INSERT INTO jobs(id,organization_id,actor_id,resource_owner_id,request_key,kind,payload) VALUES(%s,%s,%s,%s,'legacy','start',%s)",
            (job, org, user, user, json.dumps({"lab_id": str(lab), "generation": 8, "slot": 17})),
        )
        db.execute(
            "INSERT INTO node_orphans(lab_id,organization_id,user_id,generation,slot,status,cleanup_reserved) VALUES(%s,%s,%s,4,0,'RESOLVED',true)",
            (orphan, org, user),
        )
        before = {
            table: db.execute("SELECT * FROM " + table).fetchall()
            for table in ("labs", "jobs", "node_orphans")
        }
    assert database_module.migrate() == 24
    with connect() as db:
        for table, rows in before.items():
            after = db.execute("SELECT * FROM " + table).fetchall()
            assert [{k: v for k, v in row.items() if k != "node_id"} for row in after] == rows
            if table != "jobs":
                assert all(row["node_id"] == 1 for row in after)
    with pytest.raises(CheckViolation), connect() as db:
        db.execute("UPDATE node_orphans SET cleanup_reserved=false")
    with pytest.raises(CheckViolation), connect() as db:
        db.execute(
            "INSERT INTO labs(id,organization_id,user_id,mission_id,mission_version,state,slot,node_id) VALUES(%s,%s,%s,'first-files',1,'STOPPED',0,2)",
            (orphan, org, user),
        )


def test_worker_affinity_claims_only_own_node_with_legacy_default(identities, two_nodes):
    for name in ("student", "teacher"):
        client, headers = logged_in(name)
        assert test_labs.create(client, headers).status_code == 202
    with connect() as db:
        db.execute("UPDATE jobs SET payload=payload-'node_id' WHERE payload->>'node_id'='1'")
    second = claim("node-two", node_id=2)
    first = claim("node-one", node_id=1)
    assert second["payload"]["node_id"] == 2 and first["payload"].get("node_id", 1) == 1
    assert claim("empty-two", node_id=2) is None
