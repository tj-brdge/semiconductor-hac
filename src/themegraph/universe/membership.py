"""Point-in-time universe membership."""

from __future__ import annotations

import datetime as dt

import pandas as pd

from ..config import Issuer, UniverseConfig


def members_as_of(cfg: UniverseConfig, t: dt.date | pd.Timestamp,
                  roles: tuple[str, ...] = ("core", "adjacent")) -> list[Issuer]:
    d = pd.Timestamp(t).date()
    return [
        i for i in cfg.issuers
        if i.role in roles
        and (i.listed is None or i.listed <= d)
        and (i.delisted is None or i.delisted > d)
    ]


def symbols_as_of(cfg: UniverseConfig, t, roles=("core", "adjacent")) -> list[str]:
    return sorted(i.symbol for i in members_as_of(cfg, t, roles))


def segments(cfg: UniverseConfig) -> dict[str, str]:
    return {i.symbol: i.segment for i in cfg.issuers}


def roles(cfg: UniverseConfig) -> dict[str, str]:
    return {i.symbol: i.role for i in cfg.issuers}
