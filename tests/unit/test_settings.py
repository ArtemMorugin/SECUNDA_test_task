import pytest
from pydantic import ValidationError

from src.config.settings import Settings


@pytest.mark.parametrize(
    "changes",
    [
        {"payment_processing_min_delay": 6, "payment_processing_max_delay": 5},
        {"webhook_max_attempts": 4},
        {"webhook_timeout_seconds": 0},
        {"delivery_lease_seconds": 5},
    ],
)
def test_invalid_settings(changes):
    with pytest.raises(ValidationError):
        Settings(**changes)


def test_generic_debug_environment_is_ignored(monkeypatch):
    monkeypatch.setenv("DEBUG", "release")
    monkeypatch.setenv("APP_DEBUG", "false")
    assert Settings().debug is False


def test_invalid_log_level(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "VERBOSE")
    with pytest.raises(ValidationError):
        Settings()
