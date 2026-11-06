import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_dsn: str
    static_dir: Path
    profile: str = "local-single-user"


def settings() -> Settings:
    return Settings(
        database_dsn=os.environ.get(
            "CRA_DATABASE_DSN", "dbname=cra user=craapp host=/var/run/postgresql"
        ),
        static_dir=Path(os.environ.get("CRA_STATIC_DIR", "../frontend/dist")),
    )
