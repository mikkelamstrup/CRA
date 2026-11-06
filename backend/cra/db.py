import hashlib
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from .config import settings

MIGRATIONS = Path(__file__).parent / "migrations"


@contextmanager
def connect(dsn: str | None = None) -> Iterator[psycopg.Connection[dict[str, Any]]]:
    with psycopg.connect(
        dsn or settings().database_dsn, row_factory=dict_row, connect_timeout=3
    ) as connection:
        yield connection


def expected_version() -> int:
    return max(int(path.name.split("_", 1)[0]) for path in MIGRATIONS.glob("*.sql"))


def migrate(dsn: str | None = None) -> int:
    with connect(dsn) as db:
        db.execute("SELECT pg_advisory_xact_lock(1415230001)")
        db.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(version integer PRIMARY KEY, digest text NOT NULL, "
            "applied_at timestamptz NOT NULL DEFAULT now())"
        )
        for path in sorted(MIGRATIONS.glob("*.sql")):
            version = int(path.name.split("_", 1)[0])
            sql = path.read_text()
            digest = hashlib.sha256(sql.encode()).hexdigest()
            old = db.execute(
                "SELECT digest FROM schema_migrations WHERE version=%s", (version,)
            ).fetchone()
            if old:
                if old["digest"] != digest:
                    raise RuntimeError("An applied migration has changed")
                continue
            db.execute(sql)
            db.execute(
                "INSERT INTO schema_migrations(version,digest) VALUES (%s,%s)", (version, digest)
            )
    return expected_version()


if __name__ == "__main__":
    print(f"Schema ready at version {migrate()}")
