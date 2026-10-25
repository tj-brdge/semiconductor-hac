"""Run provenance: what produced a run, recorded in its manifest.

Source identity is a content hash of src/ (so uncommitted edits are covered), plus the
git commit and whether tracked files differed from it. Labels, dependency lockfile,
Python and key package versions, and the effective configuration (after any command
line overrides) are recorded so two runs can be shown to differ only where declared.
"""

from __future__ import annotations

import hashlib
import importlib.metadata as md
import json
import platform
import subprocess
from pathlib import Path

from .config import ROOT, PipelineConfig

PACKAGES = ("numpy", "pandas", "scipy", "scikit-learn", "pyarrow", "voyageai")


def _sha_files(paths: list[Path], root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(paths):
        h.update(str(p.relative_to(root)).encode())
        h.update(b"\0")
        h.update(p.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def _git(root: Path, *args: str) -> str | None:
    try:
        return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True,
                              timeout=10, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def _version(pkg: str) -> str | None:
    try:
        return md.version(pkg)
    except md.PackageNotFoundError:
        return None


def run_provenance(cfg: PipelineConfig, root: Path = ROOT) -> dict:
    lock = root / "uv.lock"
    labels = root / "labels" / "themes.yaml"
    dirty = _git(root, "status", "--porcelain", "--untracked-files=no", "--", "src",
                 "config", "labels", "uv.lock")
    return {
        "source_sha256": _sha_files(list((root / "src").rglob("*.py")), root),
        "git_commit": _git(root, "rev-parse", "HEAD"),
        "git_dirty": None if dirty is None else bool(dirty),
        "labels_sha256": hashlib.sha256(labels.read_bytes()).hexdigest()
        if labels.exists() else None,
        "lockfile_sha256": hashlib.sha256(lock.read_bytes()).hexdigest()
        if lock.exists() else None,
        "python": platform.python_version(),
        "platform": platform.platform(terse=True),
        "packages": {p: _version(p) for p in PACKAGES},
        "effective_config": json.loads(cfg.model_dump_json()),
    }
