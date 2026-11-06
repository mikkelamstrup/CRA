import os
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from psycopg.conninfo import conninfo_to_dict

from cra.db import connect, expected_version, migrate
from cra.main import app


def test_health_and_database_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(app, base_url="http://127.0.0.1:28000")
    assert client.get("/api/v1/health").json()["profile"] == "local-single-user"
    monkeypatch.setenv("CRA_DATABASE_DSN", "host=127.0.0.1 port=1 dbname=absent connect_timeout=1")
    response = client.get("/api/v1/ready")
    assert response.status_code == 503
    assert response.json() == {"ready": False, "reason": "database_unavailable"}


@pytest.mark.skipif(
    not os.environ.get("CRA_TEST_DATABASE_DSN"), reason="Real control-VM PostgreSQL required"
)
def test_real_database_migration_and_dump_restore(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dsn = os.environ["CRA_TEST_DATABASE_DSN"]
    if conninfo_to_dict(dsn).get("dbname") != "cra_test":
        raise RuntimeError("Refuse a non-test database")
    monkeypatch.setenv("CRA_DATABASE_DSN", dsn)
    with connect() as db:
        db.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    assert migrate() == expected_version()
    assert migrate() == expected_version()
    assert (
        TestClient(app, base_url="http://127.0.0.1:28000").get("/api/v1/ready").json()["ready"]
        is True
    )
    with connect() as db:
        db.execute("INSERT INTO platform_metadata VALUES ('restore-proof','synthetic-value')")
    backup = tmp_path / "foundation.dump"
    subprocess.run(
        ["pg_dump", "--dbname", dsn, "--format=custom", "--file", str(backup)], check=True
    )
    with connect() as db:
        db.execute("DELETE FROM platform_metadata WHERE name='restore-proof'")
    subprocess.run(
        ["pg_restore", "--dbname", dsn, "--clean", "--if-exists", "--exit-on-error", str(backup)],
        check=True,
    )
    with connect() as db:
        assert db.execute(
            "SELECT value FROM platform_metadata WHERE name='restore-proof'"
        ).fetchone() == {"value": "synthetic-value"}
