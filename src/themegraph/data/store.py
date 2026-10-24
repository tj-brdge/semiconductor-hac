"""Normalised Parquet tables with point-in-time columns, queried via DuckDB.

Tables are append-only by vintage: data/store/<table>/<vintage>.parquet. A new
acquisition batch writes a new vintage file; old vintages are never rewritten.

Reading a table "as of" a vintage ID combines every vintage up to it: each record
(identified by the table's key) takes its latest version, so a partial batch (EDGAR
for a few symbols) does not hide other records and a later revision replaces an
earlier value. `first_seen_at` is recomputed from the vintages themselves: the
earliest vintage that already held a record with exactly this content. An unchanged
record keeps its original first-seen time across refreshes; a revised value is first
seen when the revision was downloaded and is never backdated.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd

from .pit import assert_pit

# Record identity per table; tables not listed are read one vintage at a time.
KEYS = {"prices": ["symbol", "date"], "macro": ["series", "date"],
        "documents_transcripts": ["doc_id"], "documents_edgar": ["doc_id"]}
# Bookkeeping or derived from other columns (availability = publication + lag rules):
# not part of what was observed, so not part of a record's content.
NOT_CONTENT = {"vintage", "first_seen_at", "available_at"}


def vintage_time(v: str) -> pd.Timestamp:
    return pd.to_datetime(v, format="%Y%m%dT%H%M%SZ", utc=True)


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.used: dict[str, list[str]] = {}

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
        """`vintage`: exactly that file. Otherwise the as-of view over all vintages
        at or before `upto` (default: all)."""
        if vintage is not None or table not in KEYS:
            v = self.resolve(table, vintage, upto)
            self.used[table] = [v]  # recorded in run manifests
            return pd.read_parquet(self.root / table / f"{v}.parquet")
        last = self.resolve(table, None, upto)
        vs = [v for v in self.vintages(table) if v <= last]
        self.used[table] = vs
        return as_of(pd.concat([pd.read_parquet(self.root / table / f"{v}.parquet")
                                .assign(vintage=v) for v in vs], ignore_index=True),
                     KEYS[table])

    def query(self, sql: str) -> pd.DataFrame:
        """Run SQL where each table is exposed as a view over all its vintages."""
        con = duckdb.connect()
        for d in sorted(p for p in self.root.iterdir() if p.is_dir()):
            if any(d.glob("*.parquet")):
                con.execute(
                    f"CREATE VIEW {d.name} AS SELECT * FROM read_parquet('{d}/*.parquet')"
                )
        return con.execute(sql).df()


def as_of(df: pd.DataFrame, key: list[str]) -> pd.DataFrame:
    """Latest version of each record across vintages, with `first_seen_at` = the
    earliest vintage holding exactly that version's content."""
    if not len(df):
        return df
    content = sorted(c for c in df.columns if c not in NOT_CONTENT)
    h = pd.util.hash_pandas_object(df[content].astype(str), index=False)
    seen = df["vintage"].map(vintage_time)
    first = seen.groupby([df[k] for k in key] + [h]).transform("min")
    out = df.assign(first_seen_at=first, _seen=seen).sort_values("_seen", kind="stable")
    out = out.drop_duplicates(key, keep="last").drop(columns="_seen")
    return out.sort_index().reset_index(drop=True)
