"""Normalised Parquet tables with point-in-time columns, queried via DuckDB.

Tables are append-only by vintage: data/store/<table>/<vintage>.parquet. A new
acquisition batch writes a new vintage file; old vintages are never rewritten.

Reading a table "as of" a vintage ID combines every vintage up to it: each record
(identified by the table's key) takes its latest version, so a partial batch (EDGAR
for a few symbols) does not hide other records and a later revision replaces an
earlier value. Prices are the exception: each symbol's whole series comes from the
latest vintage that has the symbol (WHOLE_SERIES).
`first_seen_at` is recomputed from the vintages themselves: the earliest vintage
that already held a record with exactly this content. An unchanged
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
# Tables read as whole series: adjusted prices are restated after every split and
# dividend, so a symbol's history must come from one download (its latest), never
# stitched from two (a fake jump where they meet).
WHOLE_SERIES = {"prices": ["symbol"]}


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

    def read(self, table: str, vintage: str | None = None, upto: str | None = None,
             symbols: set[str] | None = None) -> pd.DataFrame:
        """`vintage`: exactly that file. Otherwise the as-of view over all vintages
        at or before `upto` (default: all). `symbols`: only these symbols' rows
        (profiles share one store), filtered before anything large is loaded."""
        if vintage is not None or table not in KEYS:
            v = self.resolve(table, vintage, upto)
            self.used[table] = [v]  # recorded in run manifests
            df = pd.read_parquet(self.root / table / f"{v}.parquet")
            return df.loc[df["symbol"].isin(symbols)].reset_index(drop=True) \
                if symbols is not None and "symbol" in df else df
        last = self.resolve(table, None, upto)
        vs = [v for v in self.vintages(table) if v <= last]
        self.used[table] = vs
        return self._as_of(table, vs, symbols)

    def _index(self, table: str, v: str) -> pd.DataFrame:
        """A vintage's small index: record key, content hash, row position (and
        symbol). Written once beside the data (a vintage never changes), so later
        reads decide which rows they need without loading every vintage's text."""
        path = self.root / table / ".index" / f"{v}.parquet"
        if path.exists():
            return pd.read_parquet(path)
        df = pd.read_parquet(self.root / table / f"{v}.parquet")
        idx = df[[c for c in dict.fromkeys(KEYS[table] + ["symbol"]) if c in df]].copy()
        idx["_hash"] = content_hash(df)
        idx["_pos"] = range(len(df))
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        idx.to_parquet(tmp, index=False)
        tmp.replace(path)
        return idx

    def _as_of(self, table: str, vs: list[str], symbols: set[str] | None) -> pd.DataFrame:
        """Latest version of each record across vintages `vs` (ascending), with
        `first_seen_at` = the earliest vintage holding exactly that content.

        Works newest to oldest on the small indexes; a vintage's data is loaded only
        if it supplies rows, and only those rows are kept. For tables in WHOLE_SERIES
        the unit is the whole series (every row of a symbol comes from the latest
        vintage that has the symbol), never a mix of vintages."""
        key = KEYS[table]
        unit = WHOLE_SERIES.get(table, key)
        won: list[pd.DataFrame] = []   # per vintage: winners' key, _hash, _pos, _seen
        taken = pd.DataFrame()
        for v in reversed(vs):
            idx = self._index(table, v)
            if symbols is not None and "symbol" in idx:
                idx = idx.loc[idx["symbol"].isin(symbols)]
            t = vintage_time(v)
            # Older copies with the same content move a winner's first-seen time back.
            for w in won:
                same = w.merge(idx[key + ["_hash"]].drop_duplicates(), on=key + ["_hash"],
                               how="left", indicator=True)["_merge"].to_numpy() == "both"
                w.loc[same, "_seen"] = t
            new = idx
            if len(taken):
                new = idx.merge(taken.assign(_old=True), on=unit, how="left")
                new = new.loc[new["_old"].isna()].drop(columns="_old")
            new = new.drop_duplicates(key, keep="last")
            if len(new):
                won.append(new.assign(_seen=t, _v=v).reset_index(drop=True))
                taken = pd.concat([taken, new[unit].drop_duplicates()], ignore_index=True)
        frames = []
        for w in reversed(won):  # oldest first, as rows were written
            df = pd.read_parquet(self.root / table / f"{w['_v'].iat[0]}.parquet")
            w = w.sort_values("_pos")
            part = df.iloc[w["_pos"].to_numpy()].reset_index(drop=True)
            del df
            part["vintage"] = w["_v"].iat[0]
            part["first_seen_at"] = pd.to_datetime(w["_seen"].to_numpy(), utc=True)
            frames.append(part)
        if not frames:
            return pd.read_parquet(self.root / table / f"{vs[-1]}.parquet").iloc[:0]
        return pd.concat(frames, ignore_index=True)

    def query(self, sql: str) -> pd.DataFrame:
        """Run SQL where each table is exposed as a view over all its vintages."""
        con = duckdb.connect()
        for d in sorted(p for p in self.root.iterdir() if p.is_dir()):
            if any(d.glob("*.parquet")):
                con.execute(
                    f"CREATE VIEW {d.name} AS SELECT * FROM read_parquet('{d}/*.parquet')"
                )
        return con.execute(sql).df()


def content_hash(df: pd.DataFrame) -> pd.Series:
    """Per-row hash of what was observed (not bookkeeping columns)."""
    content = sorted(c for c in df.columns if c not in NOT_CONTENT)
    return pd.util.hash_pandas_object(df[content].astype(str), index=False)
