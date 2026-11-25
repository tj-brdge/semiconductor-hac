"""'New in the last three months' needs the theme to have been found three months
ago. Against a month in which it was absent, every company used to count as new."""
import datetime as dt
import json

from themegraph.monitor import refresh as M
from themegraph.monitor.profiles import load_profile


def test_no_comparison_when_the_theme_was_absent_three_months_before(tmp_path):
    st = M.refresh(tmp_path / "state", tmp_path / "site", fixtures=True,
                   as_of=dt.date(2021, 12, 31), log=lambda *a: None,
                   profile=load_profile("semis"))
    assert st["ok"], st["errors"]
    reg = json.loads((tmp_path / "state" / "registry.json").read_text())
    snaps = [str(s) for s in reg["snapshots"]]
    checked = 0
    for th in reg["themes"].values():
        rows = {r["t"]: r for r in th["measures"]}
        for t, r in rows.items():
            if not r.get("present") or t not in snaps or snaps.index(t) < 3:
                continue
            base = rows.get(snaps[snaps.index(t) - 3])
            if base is not None and not base.get("present"):
                checked += 1
                assert r["new_issuers_3m"] is None, (th["id"], t)
            elif base is not None:
                assert r["new_issuers_3m"] is not None
    assert checked, "the fixture should contain a theme absent at a base month"
