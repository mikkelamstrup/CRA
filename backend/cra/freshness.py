"""A past READY observation cannot authorize new sessions indefinitely."""

from datetime import UTC, datetime, timedelta
from typing import Any

MAX_AGE_SECONDS = 20


def fresh(row: dict[str, Any]) -> bool:
    observed = row.get("last_observed_at")
    return isinstance(observed, datetime) and (
        datetime.now(UTC) - timedelta(seconds=MAX_AGE_SECONDS)
        <= observed
        <= datetime.now(UTC) + timedelta(seconds=1)
    )
