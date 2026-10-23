"""Acquisition jobs: raw API responses -> immutable cache -> normalised store tables."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
from tqdm.auto import tqdm

from .config import PipelineConfig, UniverseConfig
from .data import edgar as E
from .data import fmp as F
from .data.store import Store
from .market.macro import build_factors


def _log(msg: str) -> None:
    """Print without breaking an active progress bar."""
    tqdm.write(msg)


def _bar(items, desc: str, unit: str, **kw):
    """Progress bar on terminals only (disable=None turns it off for logs and CI)."""
    return tqdm(items, desc=desc, unit=unit, disable=None, dynamic_ncols=True, **kw)


def _combine(frames: list[pd.DataFrame], what: str) -> pd.DataFrame:
    frames = [f for f in frames if len(f)]
    if not frames:
        raise RuntimeError(f"no {what} were fetched; see the messages above for why")
    return pd.concat(frames, ignore_index=True)


def vintage_id() -> str:
    return dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%SZ")


def _stamp(df: pd.DataFrame) -> pd.DataFrame:
    return df.assign(first_seen_at=pd.Timestamp.now(tz="UTC"))


def ingest_fmp_prices(client: F.FMPClient, cfg: PipelineConfig, universe: UniverseConfig,
                      store: Store, end: dt.date, log=_log) -> Path:
    start = cfg.periods.start - dt.timedelta(days=400)  # warm-up for the first windows
    fx = {}
    for cur, pair in F.FX_PAIRS.items():
        if any(i.currency == cur for i in universe.issuers):
            fx[cur] = F.normalise_prices(client.prices(pair, start, end), pair, cfg.availability)
    frames = []
    currency = {i.symbol: i.currency for i in universe.issuers}
    bar = _bar(universe.benchmarks + [i.symbol for i in universe.issuers], "FMP prices",
               "symbol")
    for sym in bar:
        bar.set_postfix_str(sym)
        try:
            df = F.normalise_prices(client.prices(sym, start, end), sym, cfg.availability)
        except Exception as e:  # keep going; coverage report shows the gap
            log(f"  {sym}: {e}")
            continue
        cur = currency.get(sym, "USD")
        if cur != "USD":
            df = F.to_usd(df, fx[cur])
        frames.append(df)
        log(f"  {sym}: {len(df)} sessions")
    return store.write("prices", _stamp(_combine(frames, "prices")), vintage_id())


def ingest_fmp_macro(client: F.FMPClient, cfg: PipelineConfig, store: Store,
                     end: dt.date) -> Path:
    start = cfg.periods.start - dt.timedelta(days=cfg.macro.window_months * 31 + 400)
    frames = []
    treasury = client.treasury(start, end)
    for name, (kind, ident) in _bar(F.MACRO_SERIES.items(), "FMP macro", "series"):
        if name not in cfg.macro.factors:
            continue
        if kind == "treasury":
            df = F.normalise_treasury(treasury, ident, cfg.availability)
        elif kind == "economic":
            df = F.normalise_monthly_economic(client.economic(ident, start, end),
                                              cfg.availability)
        else:
            df = F.normalise_prices(client.prices(ident, start, end), ident, cfg.availability)
            df = df.rename(columns={"close": "value"}).drop(columns="symbol")
        frames.append(df.assign(series=name, kind=kind))
    return store.write("macro", _stamp(_combine(frames, "macro series")), vintage_id())


def ingest_fmp_transcripts(client: F.FMPClient, cfg: PipelineConfig, universe: UniverseConfig,
                           store: Store, log=_log) -> Path:
    frames = []
    bar = _bar(universe.issuers, "FMP transcripts", "issuer")
    for iss in bar:
        bar.set_postfix_str(iss.symbol)
        try:
            dates = client.transcript_dates(iss.symbol)
        except Exception as e:
            log(f"  {iss.symbol}: {e}")
            continue
        rows = []
        for d in _bar(dates, f"  {iss.symbol}", "call", leave=False):
            year, q = d.get("fiscalYear") or d.get("year"), d.get("quarter")
            if year is None or q is None or int(year) < cfg.periods.start.year - 1:
                continue
            rows += client.transcript(iss.symbol, int(year), int(q))
        df = F.normalise_transcripts(rows, iss.symbol, cfg.availability)
        log(f"  {iss.symbol}: {len(df)} transcripts")
        frames.append(df)
    out = _combine(frames, "transcripts")
    return store.write("documents_transcripts", _stamp(out), vintage_id())


def ingest_edgar(client: E.EdgarClient, cfg: PipelineConfig, universe: UniverseConfig,
                 store: Store, end: dt.date, symbols: list[str] | None = None,
                 forms=("10-K", "10-Q", "20-F", "8-K"), log=_log) -> Path:
    tickers = client.ticker_map()
    frames = []
    wanted = [i for i in universe.issuers
              if i.sec_filer and (not symbols or i.symbol in symbols)]
    bar = _bar(wanted, "EDGAR", "issuer")
    for iss in bar:
        bar.set_postfix_str(iss.symbol)
        cik = iss.cik or tickers.get(iss.symbol.replace(".", "-"))
        if cik is None:
            log(f"  {iss.symbol}: no CIK (set cik in universe.yaml)")
            continue
        filings = E.select_filings(client.filings(cik), cfg.periods.start, end, set(forms))
        texts: dict[str, dict[str, str]] = {}
        for r in _bar(list(filings.itertuples()), f"  {iss.symbol}", "filing", leave=False):
            try:
                if r.form == "8-K":
                    names = E.exhibit_names(client.filing_index(cik, r.accessionNumber))
                    body = "\n\n".join(E.html_to_text(client.document(cik, r.accessionNumber, n))
                                       for n in names)
                    if body.strip():
                        texts[r.accessionNumber] = {"ex99": body}
                else:
                    raw = client.document(cik, r.accessionNumber, r.primaryDocument)
                    texts[r.accessionNumber] = E.split_sections(E.html_to_text(raw), r.form)
            except Exception as e:
                log(f"  {iss.symbol} {r.accessionNumber}: {e}")
        df = E.filing_documents(filings, iss.symbol, texts, cfg.availability)
        log(f"  {iss.symbol}: {len(filings)} filings, {len(df)} sections")
        frames.append(df)
    out = _combine(frames, "EDGAR documents")
    return store.write("documents_edgar", _stamp(out), vintage_id())


# ------------------------------------------------------------------ loaders

def load_prices(store: Store) -> pd.DataFrame:
    return store.read("prices")


def load_documents(store: Store) -> pd.DataFrame:
    frames = []
    for table in ("documents_transcripts", "documents_edgar"):
        if store.vintages(table):
            frames.append(store.read(table))
    if not frames:
        return pd.DataFrame(columns=["symbol", "doc_id", "doc_type", "published_at",
                                     "available_at", "text"])
    return pd.concat(frames, ignore_index=True)


def load_factors(store: Store, cfg: PipelineConfig) -> pd.DataFrame | None:
    if not store.vintages("macro"):
        return None
    m = store.read("macro")
    daily, kinds, cpi = {}, {}, None
    for name, g in m.groupby("series"):
        s = g.set_index(pd.to_datetime(g["date"]))["value"].sort_index()
        if name == "cpi":
            cpi = s
        else:
            daily[name] = s
            kinds[name] = "diff" if g["kind"].iloc[0] == "treasury" else "logret"
    return build_factors(daily, kinds, cpi, cfg.availability.macro_release_lag_months)


def coverage(store: Store, universe: UniverseConfig) -> pd.DataFrame:
    rows = []
    prices = store.read("prices") if store.vintages("prices") else pd.DataFrame()
    docs = load_documents(store)
    for iss in universe.issuers:
        p = prices.loc[prices["symbol"] == iss.symbol] if len(prices) else prices
        d = docs.loc[docs["symbol"] == iss.symbol] if len(docs) else docs
        rows.append({
            "symbol": iss.symbol, "role": iss.role, "segment": iss.segment,
            "price_first": p["date"].min() if len(p) else None,
            "price_last": p["date"].max() if len(p) else None,
            "sessions": len(p),
            "transcripts": int((d["doc_type"] == "transcript").sum()) if len(d) else 0,
            "filing_sections": int((d["doc_type"] != "transcript").sum()) if len(d) else 0,
            "first_doc": d["published_at"].min() if len(d) else None,
        })
    return pd.DataFrame(rows)
