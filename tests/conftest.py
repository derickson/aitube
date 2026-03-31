"""Integration test fixtures. Requires the backend running on localhost:3103."""

import httpx
import pytest


BASE_URL = "http://localhost:3103"


@pytest.fixture(scope="session")
def api():
    """Synchronous httpx client pointed at the running backend."""
    with httpx.Client(base_url=BASE_URL, timeout=120) as client:
        yield client
