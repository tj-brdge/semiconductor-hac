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
