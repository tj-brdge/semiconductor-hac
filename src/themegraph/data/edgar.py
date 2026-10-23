"""SEC EDGAR client: ticker->CIK map, filing lists, documents, and text extraction.

The SEC requires a User-Agent naming the requester and a contact email; it is read
from EDGAR_USER_AGENT (e.g. "Your Org research you@example.com"). Requests are
kept under the SEC's 10 requests/second limit.

`acceptanceDateTime` in the submissions API carries a trailing "Z" but reflects
US/Eastern wall-clock time; it is interpreted as Eastern and converted to UTC,
which can only make availability later (the conservative direction).
"""

from __future__ import annotations

import datetime as dt
import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pandas as pd

from ..config import Availability
from .cache import RawCache
from .http import CachedFetcher, env_value
from .pit import filing_available_at

WWW = "https://www.sec.gov"
DATA = "https://data.sec.gov"
FORMS = {"10-K", "10-Q", "8-K", "20-F", "6-K", "40-F", "10-K/A", "20-F/A"}


class EdgarClient:
    """The ticker map and each issuer's filing list grow over time and are cached per
    `vintage` (default: today, UTC); filing documents never change and are cached once."""

    def __init__(self, cache_dir: Path, user_agent: str | None = None, offline: bool = False,
                 fetcher: CachedFetcher | None = None, vintage: str | None = None):
        self.vintage = vintage or dt.datetime.now(dt.UTC).date().isoformat()
        ua = env_value("EDGAR_USER_AGENT", user_agent)
        if fetcher is None and not offline and (not ua or "@" not in ua):
            raise RuntimeError(
                "EDGAR_USER_AGENT must name you and a contact email, per SEC fair-access policy"
            )
        self.f = fetcher or CachedFetcher(
            "edgar", RawCache(cache_dir), headers={"User-Agent": ua or ""},
            min_interval=0.15, offline=offline,
        )

    def _json(self, url: str, mutable: bool = False) -> dict:
        return json.loads(self.f.fetch(url, vintage=self.vintage if mutable else None).body)

    def ticker_map(self) -> dict[str, int]:
        data = self._json(f"{WWW}/files/company_tickers.json", mutable=True)
        return {v["ticker"].upper(): int(v["cik_str"]) for v in data.values()}

    def filings(self, cik: int) -> pd.DataFrame:
        sub = self._json(f"{DATA}/submissions/CIK{cik:010d}.json", mutable=True)
        frames = [pd.DataFrame(sub["filings"]["recent"])]
        for extra in sub["filings"].get("files", []):
            frames.append(pd.DataFrame(self._json(f"{DATA}/submissions/{extra['name']}")))
        df = pd.concat(frames, ignore_index=True)
        df["cik"] = cik
        return df

    def document(self, cik: int, accession: str, name: str) -> bytes:
        acc = accession.replace("-", "")
        return self.f.fetch(f"{WWW}/Archives/edgar/data/{cik}/{acc}/{name}").body

    def filing_index(self, cik: int, accession: str) -> list[str]:
        acc = accession.replace("-", "")
        idx = self._json(f"{WWW}/Archives/edgar/data/{cik}/{acc}/index.json")
        return [item["name"] for item in idx.get("directory", {}).get("item", [])]


def accepted_utc(values: pd.Series) -> pd.Series:
    naive = pd.to_datetime(values.astype(str).str.replace("Z", "", regex=False))
    return naive.dt.tz_localize("America/New_York", ambiguous="NaT",
                                nonexistent="shift_forward").dt.tz_convert("UTC")


def select_filings(filings: pd.DataFrame, start, end, forms=FORMS) -> pd.DataFrame:
    df = filings.loc[filings["form"].isin(forms)].copy()
    df["accepted_at"] = accepted_utc(df["acceptanceDateTime"])
    lo, hi = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1)
    df = df.loc[(df["accepted_at"] >= lo) & (df["accepted_at"] < hi)]
    return df.sort_values("accepted_at").reset_index(drop=True)


EX99 = re.compile(r"ex-?99", re.I)


def exhibit_names(index_names: list[str]) -> list[str]:
    return [n for n in index_names if EX99.search(n) and n.lower().endswith((".htm", ".html",
                                                                             ".txt"))]


# ------------------------------------------------------------ text extraction

class _TextParser(HTMLParser):
    SKIP = {"script", "style", "head", "title"}
    BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table"}
    VOID = {"br", "hr", "img", "meta", "link", "input", "col", "area", "base", "wbr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.stack: list[tuple[str, bool]] = []  # (tag, suppresses text)

    def _suppressed(self) -> bool:
        return any(s for _, s in self.stack)

    def handle_starttag(self, tag, attrs):
        style = (dict(attrs).get("style") or "").replace(" ", "").lower()
        hides = tag in self.SKIP or tag == "ix:header" or "display:none" in style
        if tag not in self.VOID:
            self.stack.append((tag, hides))
        if tag in self.BLOCK:
            self.out.append("\n")

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):  # tolerate unclosed inner tags
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break
        if tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self._suppressed():
            self.out.append(data)


def html_to_text(raw: bytes | str) -> str:
    html = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else raw
    p = _TextParser()
    p.feed(html)
    text = "".join(p.out).replace("\xa0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


SECTION_PATTERNS = {
    "10-K": {
        "business": r"item\s*1\.?\s*business",
        "risk_factors": r"item\s*1a\.?\s*risk\s*factors",
        "mdna": r"item\s*7\.?\s*management.{0,3}s\s*discussion",
        "_end": r"item\s*(1b|2|7a|8)\.?\s",
    },
    "10-Q": {
        "mdna": r"item\s*2\.?\s*management.{0,3}s\s*discussion",
        "risk_factors": r"item\s*1a\.?\s*risk\s*factors",
        "_end": r"item\s*(3|4|5|6)\.?\s",
    },
    "20-F": {
        "business": r"item\s*4\.?\s*information\s*on\s*the\s*company",
        "mdna": r"item\s*5\.?\s*operating\s*and\s*financial\s*review",
        "risk_factors": r"risk\s*factors",
        "_end": r"item\s*(4a|6|7)\.?\s",
    },
}


def split_sections(text: str, form: str) -> dict[str, str]:
    """Return named sections. Tables of contents repeat the headings, so for each
    section the occurrence followed by the longest body is chosen."""
    pats = SECTION_PATTERNS.get(form.replace("/A", ""))
    if not pats:
        return {"body": text}
    heads = []
    for name, pat in pats.items():
        for m in re.finditer(pat, text, re.I):
            heads.append((m.start(), name))
    heads.sort()
    best: dict[str, str] = {}
    for i, (pos, name) in enumerate(heads):
        if name == "_end":
            continue
        nxt = heads[i + 1][0] if i + 1 < len(heads) else len(text)
        body = text[pos:nxt]
        if len(body) > len(best.get(name, "")):
            best[name] = body
    return {k: v.strip() for k, v in best.items() if len(v.split()) > 50} or {"body": text}


def filing_documents(filings: pd.DataFrame, symbol: str, texts: dict[str, dict[str, str]],
                     rules: Availability) -> pd.DataFrame:
    """Build the documents table from selected filings and their extracted sections.
    `texts` maps accession -> {section: text}."""
    recs = []
    for r in filings.itertuples():
        for section, body in texts.get(r.accessionNumber, {}).items():
            recs.append({
                "symbol": symbol,
                "doc_id": f"edgar:{r.accessionNumber}:{section}",
                "doc_type": f"{r.form}:{section}",
                "period": str(getattr(r, "reportDate", "") or r.filingDate),
                "published_at": r.accepted_at,
                "text": body,
                "url": f"{WWW}/Archives/edgar/data/{r.cik}/"
                       f"{r.accessionNumber.replace('-', '')}/{r.primaryDocument}",
            })
    df = pd.DataFrame(recs)
    if not df.empty:
        df["available_at"] = filing_available_at(df["published_at"], rules)
    return df
