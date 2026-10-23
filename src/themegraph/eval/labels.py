"""Hand-labelled theme file: schema, validation, and freeze readiness."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict

from ..config import ROOT

LABELS_PATH = ROOT / "labels" / "themes.yaml"


class Marker(BaseModel):
    model_config = ConfigDict(extra="forbid")
    date: dt.date | None = None
    source: str | None = None

    @property
    def complete(self) -> bool:
        return self.date is not None and bool(self.source)


class Theme(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    period: Literal["dev", "holdout"]
    kind: Literal["theme", "negative_control"]
    issuers: list[str]
    emergence_marker: Marker
    consensus_marker: Marker


class LabelFile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    frozen: bool = False
    themes: list[Theme]

    def for_period(self, period: str) -> list[Theme]:
        return [t for t in self.themes if t.period == period]

    def problems(self, universe_symbols: set[str] | None = None) -> list[str]:
        out = []
        ids = [t.id for t in self.themes]
        out += [f"duplicate id {i}" for i in sorted({i for i in ids if ids.count(i) > 1})]
        for t in self.themes:
            for name in ("emergence_marker", "consensus_marker"):
                if not getattr(t, name).complete:
                    out.append(f"{t.id}: {name} needs a date and a source")
            e, c = t.emergence_marker.date, t.consensus_marker.date
            if e and c and e > c:
                out.append(f"{t.id}: emergence after consensus")
            if universe_symbols is not None:
                unknown = sorted(set(t.issuers) - universe_symbols)
                if unknown:
                    out.append(f"{t.id}: issuers not in universe {unknown}")
        return out


def load_labels(path: Path | None = None) -> LabelFile:
    return LabelFile.model_validate(yaml.safe_load(Path(path or LABELS_PATH).read_text()))
