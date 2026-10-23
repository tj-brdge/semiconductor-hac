import datetime as dt
from types import SimpleNamespace

import pytest

from themegraph.config import load_pipeline


@pytest.fixture
def cfg():
    return load_pipeline()


class FakeFetcher:
    """Serves canned bodies by URL; records requests."""

    def __init__(self, routes: dict[str, bytes]):
        self.routes = routes
        self.calls = []

    def fetch(self, url, params=None, endpoint=None, retries=3):
        self.calls.append((url, params))
        for key, body in self.routes.items():
            if url.endswith(key):
                return SimpleNamespace(body=body, fetched_at=dt.datetime.now(dt.UTC))
        raise KeyError(url)


@pytest.fixture
def fake_fetcher():
    return FakeFetcher
