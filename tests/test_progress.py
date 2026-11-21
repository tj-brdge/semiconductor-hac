"""Progress and memory reporting: visible while a run is going, and reporting only."""
import json
import os

from themegraph import progress


def test_memory_counts_this_process():
    m = progress.memory_mb()
    assert m and m["process_mb"] > 5
    assert m["total_mb"] >= m["process_mb"]


def test_lines_carry_time_and_memory_and_the_file_follows(tmp_path):
    lines = []
    p = progress.Progress(tmp_path, lines.append, every=0)
    p.enter("themes")
    p("text layer")
    assert lines[-1].endswith("text layer") and "memory" in lines[-1] and "peak" in lines[-1]
    body = json.loads((tmp_path / progress.FILE).read_text())
    assert body["stage"] == "themes" and body["running"] and body["pid"] == os.getpid()
    assert body["memory"]["total_mb"] > 0

    progress.HOOK = p.tick
    try:
        assert list(progress.track([1, 2, 3], "Text clustering")) == [1, 2, 3]
    finally:
        progress.HOOK = None
    assert "Text clustering: 3/3" in lines[-1]
    body = json.loads((tmp_path / progress.FILE).read_text())
    assert body["step"]["done"] == 3 and body["step"]["total"] == 3

    out = p.finish()
    assert out["peak_total_mb"] > 0 and "themes" in out["peak_mb_by_stage"]
    assert "Finished" in progress.report(tmp_path)


def test_ticks_are_throttled(tmp_path):
    lines = []
    p = progress.Progress(tmp_path, lines.append, every=3600)
    for i in range(0, 101):
        p.tick("Embedding passages", i, 100)
    assert len(lines) == 1 and "100/100" in lines[0]  # start is silent, the end prints


def test_report_says_when_a_run_died(tmp_path):
    assert "No run" in progress.report(tmp_path)
    p = progress.Progress(tmp_path, lambda *_: None)
    p.enter("themes")
    body = json.loads((tmp_path / progress.FILE).read_text())
    body["pid"] = 2 ** 22 + 12345  # no such process
    (tmp_path / progress.FILE).write_text(json.dumps(body))
    assert "Stopped without finishing" in progress.report(tmp_path)


def test_track_without_a_run_is_a_plain_loop():
    assert progress.HOOK is None
    assert list(progress.track(iter("ab"), "x")) == ["a", "b"]


def test_unknown_memory_stays_unknown(tmp_path, monkeypatch):
    """No `ps` and no `resource`: figures are null and shown as ?, never 0.0 GB."""
    monkeypatch.setattr(progress, "memory_mb", lambda pid=None: None)
    monkeypatch.setattr(progress, "peak_rss_mb", lambda: None)
    lines = []
    p = progress.Progress(tmp_path, lines.append, every=0)
    p.enter("themes")
    p("text layer")
    p.tick("Text clustering", 1, 2)
    assert all("memory ?, peak ?" in x and "0.0 GB" not in x for x in lines)
    body = json.loads((tmp_path / progress.FILE).read_text())
    assert body["memory"]["total_mb"] is None and body["memory"]["peak_total_mb"] is None
    assert body["peak_mb_by_stage"] == {}
    assert p.finish() == {"peak_total_mb": None, "peak_mb_by_stage": {}}
    text = progress.report(tmp_path)
    assert "0.0 GB" not in text and "?" in text


def test_a_missing_sample_does_not_move_the_peak(tmp_path, monkeypatch):
    samples = iter([{"process_mb": 900.0, "workers_mb": 100.0, "total_mb": 1000.0}, None,
                    {"process_mb": 400.0, "workers_mb": 0.0, "total_mb": 400.0}, None])
    monkeypatch.setattr(progress, "memory_mb", lambda pid=None: next(samples))
    lines = []
    p = progress.Progress(tmp_path, lines.append)
    p.stage = "themes"
    p("a")
    p("b")
    assert "memory ?, peak 1.0 GB" in lines[-1]      # unknown now, the real peak kept
    p("c")
    assert "memory 0.4 GB, peak 1.0 GB" in lines[-1]
    assert p.finish()["peak_mb_by_stage"] == {"themes": 1000.0}


def test_only_the_process_peak_is_known(tmp_path, monkeypatch):
    monkeypatch.setattr(progress, "memory_mb", lambda pid=None: None)
    monkeypatch.setattr(progress, "peak_rss_mb", lambda: 2048.0)
    lines = []
    p = progress.Progress(tmp_path, lines.append)
    p("a")
    assert "memory ?, peak 2.0 GB (main process only)" in lines[-1]
    assert p.finish()["peak_total_mb"] is None

