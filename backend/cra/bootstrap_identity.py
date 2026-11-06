"""Provision a synthetic solo account from a private JSON file; never print secrets."""

import json
import sys
from pathlib import Path
from uuid import UUID

from .auth import PASSWORDS, Login
from .db import connect

SOLO_ORG = UUID("10000000-0000-4000-8000-000000000001")
SOLO_USER = UUID("20000000-0000-4000-8000-000000000001")


def bootstrap(credentials: Login) -> None:
    with connect() as db:
        db.execute(
            "INSERT INTO organizations VALUES (%s,'Lokal akademi') ON CONFLICT DO NOTHING",
            (SOLO_ORG,),
        )
        existing = db.execute(
            "SELECT id FROM users WHERE username=%s", (credentials.username,)
        ).fetchone()
        if existing:
            if existing["id"] != SOLO_USER:
                raise RuntimeError("Username belongs to another identity")
            return
        db.execute(
            "INSERT INTO users(id,username,password_hash) VALUES (%s,%s,%s)",
            (SOLO_USER, credentials.username, PASSWORDS.hash(credentials.password)),
        )
        db.execute("INSERT INTO memberships VALUES (%s,%s,'teacher')", (SOLO_USER, SOLO_ORG))
    print("Synthetic solo identity provisioned; credentials remain in private file")


if __name__ == "__main__":
    if sys.argv[1] == "--stdin":
        bootstrap(Login.model_validate_json(sys.stdin.read(4096)))
    else:
        path = Path(sys.argv[1])
        if path.stat().st_mode & 0o077:
            raise RuntimeError("Identity file must be private")
        bootstrap(Login.model_validate(json.loads(path.read_text())))
