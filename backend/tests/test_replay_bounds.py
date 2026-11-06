"""Reject invalid replay intervals before opening a database connection."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi import HTTPException

from cra.auth import Identity
from cra.replay import replay


@pytest.mark.parametrize(
    "start,end",
    [
        (None, datetime.min.replace(tzinfo=UTC)),
        (None, datetime(2026, 1, 1, tzinfo=UTC).replace(tzinfo=None)),
        (datetime(2026, 1, 2, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC)),
        (datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 3, tzinfo=UTC)),
    ],
)
def test_invalid_interval_returns_422_without_database(start, end):
    user = Identity(uuid4(), uuid4(), "synthetic", "teacher", "synthetic-session")
    with pytest.raises(HTTPException) as error:
        replay(uuid4(), user, generation=None, from_time=start, to_time=end)
    assert error.value.status_code == 422
    assert error.value.detail == "replay_time_range"
