"""Normalised Parquet tables with point-in-time columns, queried via DuckDB.

Tables are append-only by vintage: data/store/<table>/<vintage>.parquet. A new
acquisition batch writes a new vintage file; old vintages are never rewritten.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

from .pit import assert_pit


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.used: dict[str, str] = {}

    def write(self, table: str, df: pd.DataFrame, vintage: str) -> Path:
        assert_pit(df)
        out = self.root / table / f"{vintage}.parquet"
        if out.exists():
            raise FileExistsError(f"{table} vintage {vintage} already exists")
        out.parent.mkdir(parents=True, exist_ok=True)
        df = df.assign(vintage=vintage)
        df.to_parquet(out, index=False)
        return out

    def vintages(self, table: str) -> list[str]:
        return sorted(p.stem for p in (self.root / table).glob("*.parquet"))

    def resolve(self, table: str, vintage: str | None = None,
                upto: str | None = None) -> str:
        """The vintage to read: `vintage` exactly, else the latest one not after
        `upto` (vintage IDs are sortable UTC timestamps), else the latest."""
        vs = self.vintages(table)
        if not vs:
            raise FileNotFoundError(f"no data for table {table!r} under {self.root}")
        if vintage is not None:
            if vintage not in vs:
                raise FileNotFoundError(f"{table} has no vintage {vintage}")
            return vintage
        eligible = [v for v in vs if upto is None or v <= upto]
        if not eligible:
            raise FileNotFoundError(f"{table} has no vintage at or before {upto}")
        return eligible[-1]

    def read(self, table: str, vintage: str | None = None,
             upto: str | None = None) -> pd.DataFrame:
        v = self.resolve(table, vintage, upto)
        self.used[table] = v  # recorded in run manifests
        return pd.read_parquet(self.root / table / f"{v}.parquet")

    def query(self, sql: str) -> pd.DataFrame:
        """Run SQL where each table is exposed as a view over all its vintages."""
        con = duckdb.connect()
        for d in sorted(p for p in self.root.iterdir() if p.is_dir()):
            if any(d.glob("*.parquet")):
                con.execute(
                    f"CREATE VIEW {d.name} AS SELECT * FROM read_parquet('{d}/*.parquet')"
                )
        return con.execute(sql).df()
