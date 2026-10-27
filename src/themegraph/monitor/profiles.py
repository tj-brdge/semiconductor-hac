"""Monitor profiles (docs/MONITOR.md, "Profiles"): one named set of configuration
files, state and output, so that pilots with different coverage run side by side.

A profile names its universe, pipeline and monitor files and where its registry
(state) and site go. Profiles share the document and price store, read only their
own companies from it, and share the content-addressed embedding and cluster
caches (keyed by text, vectors and settings, so they cannot mix results). The
default profile, `semis`, uses the original files and directories, so existing
commands behave exactly as before.

`fork_from` starts a profile's registry as a copy of another profile's registry
(theme IDs, basket versions and history kept) the first time it runs; from then on
the two are independent. Themes the fork creates get `id_prefix` IDs, so IDs never
collide between profiles.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict

from ..config import CONFIG_DIR, ROOT, file_sha256, load_universe, universe_sources
from .config import monitor_sources

PROFILES = CONFIG_DIR / "profiles"
DEFAULT = "semis"


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    title: str
    description: str = ""
    universe: Path
    pipeline: Path
    monitor: Path
    state: Path
    site: Path
    fixture: Literal["semis", "tech"] = "semis"  # synthetic data for --fixtures runs
    fork_from: str | None = None
    id_prefix: str = "th"


def load_profile(name: str = DEFAULT) -> Profile:
    path = PROFILES / f"{name}.yaml"
    if not path.exists():
        names = sorted(p.stem for p in PROFILES.glob("*.yaml"))
        raise SystemExit(f"unknown profile {name!r}; available: {', '.join(names)}")
    raw = yaml.safe_load(path.read_text())
    for k in ("universe", "pipeline", "monitor", "state", "site"):
        raw[k] = ROOT / raw[k]
    p = Profile.model_validate(raw)
    if p.name != name:
        raise ValueError(f"{path.name}: name must be {name!r}")
    return p


def effective(p: Profile) -> dict:
    """What a run used: the files behind each setting (with their hashes) and the
    universe version. Written into every release and status file."""
    def files(paths):
        return [{"path": str(Path(x).relative_to(ROOT)), "sha256": file_sha256(x)}
                for x in paths]
    u = load_universe(p.universe)
    return {"profile": p.name, "title": p.title, "universe_name": u.name,
            "universe_version": u.version, "n_issuers": len(u.issuers),
            "fork_from": p.fork_from,
            "files": {"universe": files(universe_sources(p.universe)),
                      "pipeline": files([p.pipeline]),
                      "monitor": files(monitor_sources(p.monitor))}}
