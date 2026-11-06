"""Upgrade an existing version-21 outbox without inventing receipt timestamps."""

from datetime import UTC, datetime
from uuid import uuid4

from psycopg.types.json import Jsonb

from cra import db as database_module
from cra.contracts import Event, LifecyclePayload
from cra.db import connect, migrate
from cra.jobs import publish


def test_upgrade_preserves_old_events_and_accepts_new_writers(database, monkeypatch, tmp_path):
    migrations = database_module.MIGRATIONS
    for path in migrations.glob("*.sql"):
        if int(path.name.split("_", 1)[0]) <= 21:
            (tmp_path / path.name).write_bytes(path.read_bytes())
    with connect() as db:
        db.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    monkeypatch.setattr(database_module, "MIGRATIONS", tmp_path)
    assert migrate() == 21
    org = uuid4()
    event = Event(
        schema_version=1,
        event_id=uuid4(),
        organization_id=org,
        lab_id=uuid4(),
        generation=1,
        actor_id=uuid4(),
        occurred_at=datetime(2026, 1, 1, tzinfo=UTC),
        source="control",
        evidence_level="system",
        payload=LifecyclePayload(kind="lab.lifecycle", state="READY"),
    )
    encoded = event.model_dump(mode="json")
    with connect() as db:
        db.execute("INSERT INTO organizations VALUES (%s,'synthetic upgrade')", (org,))
        db.execute(
            "INSERT INTO outbox(event_id,organization_id,event) VALUES (%s,%s,%s)",
            (event.event_id, org, Jsonb(encoded)),
        )
    monkeypatch.setattr(database_module, "MIGRATIONS", migrations)
    assert migrate() == database_module.expected_version()
    assert migrate() == database_module.expected_version()
    with connect() as db:
        old = db.execute("SELECT * FROM outbox WHERE event_id=%s", (event.event_id,)).fetchone()
        assert old["event"] == encoded
        assert old["event_occurred_at"] == event.occurred_at
        assert old["recorded_at"] is None
        new = event.model_copy(update={"event_id": uuid4()})
        publish(db, new)
        assert (
            db.execute(
                "SELECT recorded_at FROM outbox WHERE event_id=%s", (new.event_id,)
            ).fetchone()["recorded_at"]
            is not None
        )
