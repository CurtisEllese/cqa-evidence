"""Use a realistic loopback ASGI transport; no production test-host exception."""
import pytest
from starlette.testclient import TestClient


@pytest.fixture(autouse=True)
def loopback_test_transport(monkeypatch):
    original = TestClient.__init__
    def initialize(self, *args, **kwargs):
        if len(args) < 2:
            kwargs.setdefault("base_url", "http://127.0.0.1")
        kwargs.setdefault("client", ("127.0.0.1", 50000))
        original(self, *args, **kwargs)
    monkeypatch.setattr(TestClient, "__init__", initialize)
