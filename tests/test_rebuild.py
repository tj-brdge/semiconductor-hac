"""Rebuilding history with companies added later (docs/MONITOR.md, "Rebuilding
history"): the registry's basis is fixed when it is built, and a rebuild never
destroys the monitor it replaces."""

import datetime as dt
import json
from types import SimpleNamespace as NS

from themegraph import cli
from themegraph.config import load_universe, with_full_history
from themegraph.monitor import refresh as M
from themegraph.monitor.profiles import load_profile

TECH = load_profile("tech-v2")
NEW_SEGMENTS = ("app_software", "security", "data_cloud")
DAY = dt.date(2021, 8, 1)


def _added_on(day, marker=None):
    """The synthetic software companies join coverage on `day`; the coverage list
    optionally carries the rebuild marker."""
    def hook(ctx, as_of):
        u = ctx["universe"]
        iss = [i.model_copy(update={"covered_from": day}) if i.segment in NEW_SEGMENTS
               else i for i in u.issuers]
        return {"universe": u.model_copy(update={"issuers": iss,
                                                 "full_history_through": marker})}
    return hook


def test_the_marker_clears_only_dates_up_to_it():
    u = load_universe(TECH.universe)
    assert u.full_history_through == dt.date(2026, 11, 20)
    assert sum(i.covered_from is not None for i in u.issuers) == 27
    built = with_full_history(u, u.full_history_through)
    assert not any(i.covered_from for i in built.issuers) and len(built.issuers) == 154
    earlier = with_full_history(u, dt.date(2026, 11, 19))
    assert sum(i.covered_from is not None for i in earlier.issuers) == 27
    assert with_full_history(u, None) is u
    assert any(i.covered_from for i in u.issuers)        # the file's record is untouched


def _reg(root):
    return json.loads((root / "state" / "registry.json").read_text())


def test_a_new_registry_built_with_the_marker_has_the_companies_from_the_start(tmp_path):
    st = M.refresh(tmp_path / "state", tmp_path / "site", fixtures=True,
                   as_of=dt.date(2021, 11, 1), log=lambda *a: None, profile=TECH,
                   inputs_hook=_added_on(DAY, marker=DAY))
    assert st["ok"], st["errors"]
    reg = _reg(tmp_path)
    assert reg["full_history_through"] == str(DAY) and "history" not in st["warnings"]
    themes = reg["themes"].values()
    assert not any(t["route"] == "newly_covered" for t in themes)   # no live expansion
    software = {s for t in themes if t["published"] for v in t["versions"]
                for c in v["constituents"] for s in [c["symbol"]]
                if s.startswith(("APP", "SEC", "DAT"))}
    assert software                                    # they are in the replayed baskets
    first = min(v["entry_after"] for t in themes for v in t["versions"])
    assert first < "2021-08-01"                        # history before their real date


def test_an_existing_registry_keeps_its_basis_and_says_a_rebuild_is_waiting(tmp_path):
    first = M.refresh(tmp_path / "state", tmp_path / "site", fixtures=True, as_of=DAY,
                      log=lambda *a: None, profile=TECH, inputs_hook=_added_on(DAY))
    assert first["ok"] and _reg(tmp_path)["full_history_through"] is None
    # The marker is set afterwards: nothing is reinterpreted, and the status says why.
    st = M.refresh(tmp_path / "state", tmp_path / "site", fixtures=True,
                   as_of=dt.date(2021, 11, 1), log=lambda *a: None, profile=TECH,
                   inputs_hook=_added_on(DAY, marker=DAY))
    assert st["ok"], st["errors"]
    assert "themegraph monitor rebuild --confirm" in st["warnings"]["history"]
    reg = _reg(tmp_path)
    assert reg["full_history_through"] is None
    assert any(t["route"] == "newly_covered" for t in reg["themes"].values())


def _args(tmp_path, confirm):
    return NS(state=str(tmp_path / "state"), site=str(tmp_path / "site"), confirm=confirm,
              as_of=None, fixtures=False, no_ingest=True)


def _old_monitor(tmp_path):
    for d in ("state", "site"):
        (tmp_path / d).mkdir()
    (tmp_path / "state" / "registry.json").write_text('{"old": true}')
    (tmp_path / "state" / "relations.json").write_text('{"pairs": {"A>B": {}}}')
    (tmp_path / "site" / "index.html").write_text("old site")


def test_rebuild_without_confirm_changes_nothing(tmp_path, capsys):
    _old_monitor(tmp_path)
    assert cli._rebuild(_args(tmp_path, False), TECH, NS(refresh=None)) == 0
    assert "Nothing was changed" in capsys.readouterr().out
    assert sorted(p.name for p in tmp_path.iterdir()) == ["site", "state"]


def test_rebuild_moves_the_old_monitor_aside_and_keeps_what_companies_said(tmp_path):
    _old_monitor(tmp_path)
    seen = {}

    def refresh(state, site, **kw):
        seen["relations"] = (state / "relations.json").read_text()
        seen["registry"] = (state / "registry.json").exists()
        state.mkdir(parents=True, exist_ok=True)
        site.mkdir(parents=True, exist_ok=True)
        (state / "registry.json").write_text('{"new": true}')
        return {"ok": True, "errors": {}}
    assert cli._rebuild(_args(tmp_path, True), TECH, NS(refresh=refresh)) == 0
    assert seen == {"relations": '{"pairs": {"A>B": {}}}', "registry": False}
    names = sorted(p.name for p in tmp_path.iterdir())
    assert len(names) == 4 and names[0] == "site" and names[2] == "state"
    archive = next(p for p in tmp_path.iterdir() if p.name.startswith("state.archive-"))
    assert (archive / "registry.json").read_text() == '{"old": true}'
    assert (tmp_path / "state" / "registry.json").read_text() == '{"new": true}'


def test_a_failed_rebuild_puts_the_old_monitor_back(tmp_path, capsys):
    _old_monitor(tmp_path)

    def refresh(state, site, **kw):
        state.mkdir(parents=True, exist_ok=True)
        (state / "half.json").write_text("{}")
        return {"ok": False, "errors": {"themes": "out of memory"}}
    assert cli._rebuild(_args(tmp_path, True), TECH, NS(refresh=refresh)) == 1
    assert (tmp_path / "state" / "registry.json").read_text() == '{"old": true}'
    assert (tmp_path / "site" / "index.html").read_text() == "old site"
    assert not any(p.name.startswith(("state.archive-", "site.archive-"))
                   for p in tmp_path.iterdir())
    assert any(p.name.startswith("state.failed-") for p in tmp_path.iterdir())
    assert "put back unchanged" in capsys.readouterr().out

    def boom(state, site, **kw):
        raise MemoryError("killed")
    assert cli._rebuild(_args(tmp_path, True), TECH, NS(refresh=boom)) == 1
    assert (tmp_path / "state" / "registry.json").read_text() == '{"old": true}'


def _names(tmp_path, prefix):
    return [p for p in tmp_path.iterdir() if p.name.startswith(prefix)]


def _intact(tmp_path):
    assert (tmp_path / "state" / "registry.json").read_text() == '{"old": true}'
    assert (tmp_path / "state" / "relations.json").exists()
    assert (tmp_path / "site" / "index.html").read_text() == "old site"
    assert not _names(tmp_path, "state.archive-") and not _names(tmp_path, "site.archive-")


def test_a_failure_before_the_refresh_is_rolled_back_too(tmp_path, monkeypatch, capsys):
    """Moving the site aside, or copying relations.json, can fail as well."""
    _old_monitor(tmp_path)
    real = cli.Path.rename

    def no_site(self, target):
        if self.name == "site":
            raise PermissionError("site is locked")
        return real(self, target)
    monkeypatch.setattr(cli.Path, "rename", no_site)
    never = NS(refresh=lambda *a, **k: (_ for _ in ()).throw(AssertionError("ran")))
    assert cli._rebuild(_args(tmp_path, True), TECH, never) == 1
    monkeypatch.undo()
    _intact(tmp_path)
    out = capsys.readouterr().out
    assert "site is locked" in out and "put back unchanged" in out
    assert not _names(tmp_path, "state.failed-")    # nothing was written: nothing kept

    def no_copy(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr("shutil.copy2", no_copy)
    assert cli._rebuild(_args(tmp_path, True), TECH, never) == 1
    monkeypatch.undo()
    _intact(tmp_path)
    assert "disk full" in capsys.readouterr().out


def test_stopping_a_rebuild_from_the_keyboard_puts_the_old_monitor_back(tmp_path):
    _old_monitor(tmp_path)

    def stop(state, site, **kw):
        (state / "half.json").write_text("{}")
        raise KeyboardInterrupt
    assert cli._rebuild(_args(tmp_path, True), TECH, NS(refresh=stop)) == 130
    _intact(tmp_path)
    assert (_names(tmp_path, "state.failed-")[0] / "half.json").exists()


def test_when_recovery_fails_nothing_is_lost_and_the_report_says_where(
        tmp_path, monkeypatch, capsys):
    _old_monitor(tmp_path)
    real = cli.Path.rename

    def refresh(state, site, **kw):
        site.mkdir()
        (state / "half.json").write_text("{}")

        def stuck(self, target):    # from here on the new state folder cannot be moved
            if self.name == "state":
                raise PermissionError("state is in use")
            return real(self, target)
        monkeypatch.setattr(cli.Path, "rename", stuck)
        raise MemoryError("killed")
    assert cli._rebuild(_args(tmp_path, True), TECH, NS(refresh=refresh)) == 1
    monkeypatch.undo()
    out = capsys.readouterr().out
    assert "RECOVERY WAS NOT COMPLETE" in out and "state is in use" in out
    archive = _names(tmp_path, "state.archive-")[0]
    assert str(archive) in out                               # where the old one is
    assert (archive / "registry.json").read_text() == '{"old": true}'   # not overwritten
    assert (tmp_path / "state" / "half.json").exists()       # the attempt, not deleted
    assert not (tmp_path / "state" / "registry.json").exists()
    assert (tmp_path / "site" / "index.html").read_text() == "old site"  # site did recover
    assert _names(tmp_path, "site.failed-")


def test_an_existing_archive_is_never_written_into(tmp_path, monkeypatch, capsys):
    _old_monitor(tmp_path)

    class Fixed(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 21, 12, 0, 0, tzinfo=tz)
    monkeypatch.setattr(cli.dt, "datetime", Fixed)
    (tmp_path / "state.archive-20261121T120000Z").mkdir()
    assert cli._rebuild(_args(tmp_path, True), TECH, NS(refresh=None)) == 1
    assert "already exists" in capsys.readouterr().out
    assert (tmp_path / "state" / "registry.json").read_text() == '{"old": true}'

