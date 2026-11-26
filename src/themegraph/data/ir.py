"""Company investor-relations documents: a third document source beside transcripts
and SEC filings (docs/OPERATIONS.md).

A manifest (`config/ir_documents.yaml`) lists documents on companies' own sites:
earnings releases, presentations, call scripts, business reports. Each is fetched
once, turned into text, and stored with its own document type (`ir:<kind>`), its
own URL and the company's publication date, so a passage from one is never shown
as a transcript or an SEC filing.

Only documents the manifest lists are read. Nothing here signs in, fills a form or
follows links.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import io
from pathlib import Path

import pandas as pd
import yaml
from pydantic import BaseModel, ConfigDict

from .cache import RawCache
from .edgar import html_to_text
from .http import CachedFetcher
from .pit import filing_available_at

KINDS = ("earnings_release", "presentation", "call_script", "business_report", "news")
MANIFEST = Path(__file__).resolve().parents[3] / "config" / "ir_documents.yaml"


class IrDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str
    date: dt.date
    kind: str
    url: str
    title: str = ""
    period: str = ""


def load_manifest(path: Path = MANIFEST) -> list[IrDocument]:
    if not Path(path).exists():
        return []
    raw = yaml.safe_load(Path(path).read_text()) or {}
    docs = [IrDocument.model_validate(d) for d in raw.get("documents") or []]
    for d in docs:
        if d.kind not in KINDS:
            raise ValueError(f"{d.url}: kind must be one of {', '.join(KINDS)}")
        if not d.url.lower().startswith("https://"):
            raise ValueError(f"{d.url}: only https links are read")
    urls = [d.url for d in docs]
    dupes = sorted({u for u in urls if urls.count(u) > 1})
    if dupes:
        raise ValueError(f"listed twice in {path}: {dupes}")
    return docs


def doc_id(d: IrDocument) -> str:
    return f"ir:{d.symbol}:{hashlib.sha256(d.url.encode()).hexdigest()[:12]}"


def is_pdf(body: bytes, url: str = "", content_type: str | None = None) -> bool:
    return body[:5] == b"%PDF-" or "pdf" in (content_type or "").lower() \
        or url.lower().split("?")[0].endswith(".pdf")


def pdf_to_text(body: bytes) -> str:
    """Text of a PDF, page by page. A scanned or image-only PDF yields nothing."""
    from pypdf import PdfReader
    reader = PdfReader(io.BytesIO(body))
    return "\n\n".join((page.extract_text() or "").strip() for page in reader.pages).strip()


def extract_text(body: bytes, url: str = "", content_type: str | None = None) -> str:
    return pdf_to_text(body) if is_pdf(body, url, content_type) else html_to_text(body)


class IrClient:
    """Fetches listed documents once each (a published document does not change)."""

    def __init__(self, cache_dir: Path, fetcher: CachedFetcher | None = None,
                 offline: bool = False):
        import httpx
        self.f = fetcher or CachedFetcher(
            "ir", RawCache(cache_dir), min_interval=1.0, offline=offline,
            client=httpx.Client(follow_redirects=True, timeout=60.0,
                                headers={"User-Agent": "themegraph/0.1 (research reader)"}))

    def text(self, d: IrDocument) -> str:
        resp = self.f.fetch(d.url, endpoint=f"ir/{d.symbol}")
        return extract_text(resp.body, d.url, (resp.meta or {}).get("content_type"))


def ir_documents(entries: list[IrDocument], texts: dict[str, str], rules) -> pd.DataFrame:
    """The documents table for entries whose text was read. A company gives a date,
    not a time: the document counts as published at the end of that day (UTC), then
    the same lag as a filing applies, so it is never used on its publication day."""
    recs = []
    for d in entries:
        body = texts.get(d.url, "")
        if len(body.split()) < 40:
            continue
        recs.append({"symbol": d.symbol, "doc_id": doc_id(d), "doc_type": f"ir:{d.kind}",
                     "period": d.period or d.date.isoformat(),
                     "published_at": pd.Timestamp(d.date, tz="UTC") + pd.Timedelta(days=1),
                     "text": body, "url": d.url, "title": d.title})
    df = pd.DataFrame(recs)
    if not df.empty:
        df["available_at"] = filing_available_at(df["published_at"], rules)
    return df
