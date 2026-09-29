from datetime import UTC, time

import pytest

from tools.todo.core import config


@pytest.fixture(autouse=True)
def utc_midnight_reset(monkeypatch):
    """Most tests are written against a midnight-UTC reset; KST tests override this."""
    monkeypatch.setattr(config, "RESET_TZ", UTC)
    monkeypatch.setattr(config, "DAILY_RESET", time(0, 0))
