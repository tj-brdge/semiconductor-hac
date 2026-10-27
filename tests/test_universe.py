import datetime as dt

from themegraph.config import load_universe
from themegraph.universe.membership import symbols_as_of


def test_membership_is_point_in_time():
    u = load_universe()
    early = symbols_as_of(u, dt.date(2016, 6, 30))
    late = symbols_as_of(u, dt.date(2025, 6, 30))
    assert "LLTC" in early and "LLTC" not in late
    assert "ARM" not in early and "ARM" in late
    assert len(u.issuers) == 76


def test_pilot_universe_is_described_and_versioned():
    u = load_universe()  # load_universe also validates sectors and the change log
    assert u.name and u.version == u.changes[-1].version
    assert {i.segment for i in u.issuers} <= set(u.sectors)
