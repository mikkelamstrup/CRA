import os

import pytest
from psycopg.conninfo import conninfo_to_dict

from cra.db import connect, migrate


@pytest.fixture
def database(monkeypatch: pytest.MonkeyPatch):
    dsn = os.environ.get("CRA_TEST_DATABASE_DSN")
    if not dsn:
        pytest.skip("Real control-VM PostgreSQL required")
    if conninfo_to_dict(dsn).get("dbname") != "cra_test":
        raise RuntimeError("Refuse a non-test database")
    monkeypatch.setenv("CRA_DATABASE_DSN", dsn)
    with connect() as db:
        db.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public")
    migrate()
    yield dsn
