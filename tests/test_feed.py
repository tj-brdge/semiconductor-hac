"""`themegraph monitor feed`: one file of themes to hand to a reviewer."""

import datetime as dt
import json

from themegraph.monitor import refresh as M
from themegraph.monitor.feed import feed, label_setup


def test_feed_lists_every_theme_and_says_why_titles_are_keywords(tmp_path):
    state, site = tmp_path / "state", tmp_path / "site"
    st = M.refresh(state, site, fixtures=True, as_of=dt.date(2021, 6, 30),
                   log=lambda *a: None)
    assert st["ok"], st["errors"]
    reg = json.loads((state / "registry.json").read_text())
    pub = [t for t in reg["themes"].values() if t["published"]]
    assert pub
    # One rejected answer, as a refresh would record it.
    t = pub[0]
    t["label"].update(status="ambiguous", note="evidence is ambiguous: two subjects",
                      rejected={"title": "Mixed supply and demand talk", "summary": "x"})
    (state / "registry.json").write_text(json.dumps(reg))
    status = site / "data" / "status.json"
    doc = json.loads(status.read_text())
    doc["warnings"] = {"labels": "Claude labels unavailable (ModuleNotFoundError: anthropic)"}
    status.write_text(json.dumps(doc))

    text = feed(state, site, env={"ANTHROPIC_API_KEY": "sk-secret-value"})
    for th in pub:
        assert f"## {th['id']} — " in text
    assert "ANTHROPIC_API_KEY in this shell: set" in text
    assert "sk-secret-value" not in text                 # the key itself is never written
    assert "labels warning: Claude labels unavailable" in text
    assert "Not used because: evidence is ambiguous: two subjects" in text
    assert 'Claude\'s title: "Mixed supply and demand talk"' in text
    assert "Passages:" in text and "Keywords:" in text


def test_label_setup_names_the_missing_piece():
    lines = "\n".join(label_setup(None, env={}))
    assert "ANTHROPIC_API_KEY in this shell: NOT set" in lines
