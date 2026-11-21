"""Where a long run is and how much memory it holds, visible while it runs.

A refresh that rebuilds ten years can take hours and most of a laptop's memory. The
progress bars only draw in a terminal; this writes the same position, with memory, to
every log line and to `<state>/progress.json`, which `themegraph monitor progress`
reads from another terminal. Reporting only: nothing here changes a result.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys
import time
from pathlib import Path

FILE = "progress.json"
HOOK = None  # set by a running refresh: called as HOOK(desc, done, total)


def tick(desc: str, done: int, total: int | None) -> None:
    if HOOK is not None:
        HOOK(desc, done, total)


def track(items, desc: str, total: int | None = None):
    """Yield `items`, reporting the position after each one."""
    if total is None:
        try:
            total = len(items)
        except TypeError:
            total = None
    tick(desc, 0, total)
    for i, x in enumerate(items, 1):
        yield x
        tick(desc, i, total)


def peak_rss_mb() -> float | None:
    """Peak resident memory of this process so far (whole process, not per step)."""
    try:
        import resource
    except ImportError:  # Windows
        return None
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return round(r / (1024 * 1024) if sys.platform == "darwin" else r / 1024, 1)


def memory_mb(pid: int | None = None) -> dict | None:
    """Resident memory now: this process, and the worker processes it started (text
    clustering runs in workers, which the process's own figure does not include)."""
    pid = pid or os.getpid()
    try:
        out = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,rss="], capture_output=True,
                             text=True, timeout=5, check=True).stdout
    except Exception:  # noqa: BLE001 - no ps (Windows) or it failed: memory is unknown
        return None
    rss, kids = {}, {}
    for line in out.splitlines():
        parts = line.split()
        if len(parts) != 3 or not all(x.isdigit() for x in parts):
            continue
        p, parent, kb = map(int, parts)
        rss[p] = kb
        kids.setdefault(parent, []).append(p)
    if pid not in rss:
        return None
    seen, stack, workers = {pid}, list(kids.get(pid, [])), 0
    while stack:
        p = stack.pop()
        if p in seen:
            continue
        seen.add(p)
        workers += rss.get(p, 0)
        stack.extend(kids.get(p, []))
    return {"process_mb": round(rss[pid] / 1024, 1), "workers_mb": round(workers / 1024, 1),
            "total_mb": round((rss[pid] + workers) / 1024, 1)}


def clock(seconds: float) -> str:
    s = int(max(seconds, 0))
    return f"{s // 3600}:{s % 3600 // 60:02d}:{s % 60:02d}"


def gb(mb: float | None) -> str:
    return "?" if mb is None else f"{mb / 1024:.1f} GB"


class Progress:
    """Wraps a refresh's `log`: every line gains elapsed time and memory, and the
    current position is kept in `<state>/progress.json`."""

    def __init__(self, state_dir: Path, log=print, every: float = 15.0):
        self.path = Path(state_dir) / FILE
        self.out = log
        self.every = every
        self.started = time.time()
        self.stage = "starting"
        self.step: dict | None = None
        self.message = ""
        self.peak_total: float | None = None   # None until memory was really measured
        self.by_stage: dict[str, float] = {}
        self._last = 0.0
        self._last_desc = None

    def _memory(self) -> dict:
        """One sample. Where memory cannot be measured (no `ps`), the figures stay
        unknown (None): an unknown is never recorded as zero or counted as a peak."""
        m = memory_mb() or {}
        total = m.get("total_mb")
        if total is not None:
            self.peak_total = total if self.peak_total is None else max(self.peak_total,
                                                                        total)
            self.by_stage[self.stage] = max(self.by_stage.get(self.stage, total), total)
        return {"process_mb": m.get("process_mb"), "workers_mb": m.get("workers_mb"),
                "total_mb": total, "peak_process_mb": peak_rss_mb(),
                "peak_total_mb": self.peak_total}

    def _write(self, mem: dict, done: bool = False) -> None:
        body = {"pid": os.getpid(), "running": not done,
                "started_at": dt.datetime.fromtimestamp(self.started, dt.UTC)
                .isoformat(timespec="seconds"),
                "updated_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
                "elapsed_seconds": round(time.time() - self.started, 1),
                "stage": self.stage, "step": self.step, "message": self.message,
                "memory": mem, "peak_mb_by_stage": self.by_stage}
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(body, indent=1))
            tmp.replace(self.path)
        except OSError:  # progress is a convenience: never fail a run over it
            pass

    def _stamp(self, mem: dict) -> str:
        peak = gb(self.peak_total)
        if self.peak_total is None and mem.get("peak_process_mb") is not None:
            # No sample of the whole run, but the system still reports this process.
            peak = f"{gb(mem['peak_process_mb'])} (main process only)"
        return (f"[{clock(time.time() - self.started)} | memory {gb(mem.get('total_mb'))}, "
                f"peak {peak}]")

    def __call__(self, message="") -> None:
        mem = self._memory()
        self.message = str(message).strip()
        self.out(f"{self._stamp(mem)} {message}")
        self._write(mem)

    def enter(self, stage: str) -> None:
        self.stage, self.step = stage, None
        self._write(self._memory())

    def tick(self, desc: str, done: int, total: int | None) -> None:
        """A position inside a stage. Printed and saved at most every `every` seconds,
        and always at the start and the end of a loop."""
        now = time.time()
        if desc != self._last_desc:
            self._t0, self._last_desc = now, desc
        edge = done == 0 or (total is not None and done >= total)
        if not edge and now - self._last < self.every:
            return
        self._last = now
        took = now - self._t0
        left = took / done * (total - done) if done and total else None
        self.step = {"name": desc, "done": done, "total": total,
                     "seconds": round(took, 1),
                     "seconds_left": None if left is None else round(left)}
        mem = self._memory()
        if done:
            of = f"{done}/{total}" if total else str(done)
            eta = f", about {clock(left)} left" if left else ""
            self.out(f"{self._stamp(mem)}   {desc}: {of}{eta}")
        self._write(mem)

    def finish(self) -> dict:
        mem = self._memory()
        self.stage, self.step = "finished", None
        self._write(mem, done=True)
        return {"peak_total_mb": self.peak_total, "peak_mb_by_stage": dict(self.by_stage)}


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        return True
    return True


def report(state_dir: Path) -> str:
    """What `themegraph monitor progress` prints."""
    path = Path(state_dir) / FILE
    if not path.exists():
        return f"No run has recorded progress in {state_dir} yet."
    p = json.loads(path.read_text())
    running = p.get("running") and alive(int(p.get("pid", -1)))
    mem = p.get("memory") or {}
    if running:
        live = memory_mb(int(p["pid"]))
        if live:        # an unavailable reading must not hide the last recorded one
            mem = {**mem, **live}
        head = f"Running (process {p['pid']}), started {p['started_at']}"
    elif p.get("running"):
        head = f"Stopped without finishing (process {p.get('pid')} is gone)"
    else:
        head = "Finished"
    lines = [head, f"  elapsed    {clock(p.get('elapsed_seconds', 0))}"
             f"   (last update {p.get('updated_at')})",
             f"  stage      {p.get('stage')}"]
    s = p.get("step")
    if s:
        of = f"{s['done']}/{s['total']}" if s.get("total") else str(s.get("done"))
        left = f", about {clock(s['seconds_left'])} left in this step" \
            if s.get("seconds_left") else ""
        lines.append(f"  step       {s['name']}: {of}{left}")
    if p.get("message"):
        lines.append(f"  last line  {p['message']}")
    if mem:
        peak = gb(mem.get("peak_total_mb"))
        if mem.get("peak_total_mb") is None and mem.get("peak_process_mb") is not None:
            peak = f"{gb(mem['peak_process_mb'])} (main process only)"
        lines.append(f"  memory     {gb(mem.get('total_mb'))} "
                     f"{'now' if running else 'at the last update'} "
                     f"(main process {gb(mem.get('process_mb'))}, "
                     f"workers {gb(mem.get('workers_mb'))}); peak {peak}")
    for k, v in (p.get("peak_mb_by_stage") or {}).items():
        lines.append(f"    peak in {k:<18} {gb(v)}")
    return "\n".join(lines)
