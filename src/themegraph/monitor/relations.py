"""Named business relationships between covered companies (docs/MONITOR.md,
"Business relationships").

Question: which covered companies say they do business with which others: as
supplier and customer, partners, competitors, or owner and owned?

1. **Candidates.** In each company's own statements (prepared remarks, management
   answers and filings; analysts' questions are left out) from the last
   `lookback_months`, find passages that name another covered company. Names come
   from the coverage list (`Issuer.name`, a short form of it, and `aliases`);
   tickers are never matched.
2. **Reading.** For each pair (speaker, named company) Claude reads the most recent
   passages and says what the speaker states about the relationship, or that it
   is only a mention. Only the passages are used. An answer must cite one of the
   passages it was shown, and that passage is kept as the evidence.
3. **Links.** Statements are combined into one link per pair and kind. A link
   records who said it and when; when both companies say it, both are kept.

Limits: a customer or supplier that is not named ("a large cloud customer")
cannot be found; a statement can be out of date (the date is shown); only the
covered companies are considered; lenders and banks are out of scope. This is a
description of what companies have said. It feeds nothing in detection, baskets
or any measure.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pandas as pd

PROMPT_VERSION = 1
LOOKBACK_MONTHS = 24     # statements older than this are not read
PER_PAIR = 3             # most recent passages shown for a pair, from different documents
WINDOW_WORDS = 110       # words kept on each side of the name
KINDS = ("supplies", "buys_from", "partner", "competitor", "owns_stake_in", "owned_by", "none")

# Trailing words dropped to get the name people say ("Micron Technology" -> "Micron").
SUFFIXES = {"technology", "technologies", "holdings", "holding", "systems", "networks",
            "electronics", "platforms", "software", "energy", "corporation", "inc",
            "semiconductors", "solutions", "enterprise"}
# Short forms that are ordinary words or shared by several companies: never matched.
TOO_GENERIC = {"Linear", "Applied", "Power", "Silicon", "Advanced", "General", "Western",
               "Gen", "Palo Alto", "Monolithic", "Analog", "United", "Super", "Constellation",
               "Cadence Design", "Hewlett Packard"}

SCHEMA = {
    "type": "object",
    "properties": {
        "relationship": {"type": "string", "enum": list(KINDS)},
        "evidence": {"type": "string"},
        "summary": {"type": "string"},
    },
    "required": ["relationship", "evidence", "summary"],
    "additionalProperties": False,
}

PROMPT = """Below are passages from {speaker}'s own earnings calls and SEC filings that \
name {other}. Decide what {speaker} states about its business relationship with {other}.

Use only the passages. Do not use outside knowledge about either company.

relationship, from {speaker}'s side:
- "supplies": {speaker} sells products or services to {other} ({other} is a customer).
- "buys_from": {speaker} buys products, services, manufacturing or capacity from {other}.
- "partner": they develop, integrate or sell something together, with no clear buyer and seller.
- "competitor": {speaker} names {other} as a competitor.
- "owns_stake_in": {speaker} owns, acquired or is acquiring all or part of {other}.
- "owned_by": {other} owns, acquired or is acquiring all or part of {speaker}.
- "none": {other} is only mentioned (an example, a market comment, a comparison, a \
hypothetical, a denial, a different company or an ordinary word with the same spelling), \
or the passages do not state a relationship.

If more than one applies, choose the one stated most directly. When in doubt, answer "none".

evidence: the ID of the one passage that states it most clearly (empty for "none").
summary: one plain sentence saying what {speaker} states, with no claim beyond the passage.

Passages:
{passages}"""


def short_name(name: str) -> str | None:
    words = re.sub(r"\s*\(.*?\)", "", name).split()
    while len(words) > 1 and words[-1].lower().strip(".,") in SUFFIXES:
        words.pop()
    s = " ".join(words)
    return s if s != name and s not in TOO_GENERIC and len(s) > 2 else None


def aliases(universe) -> dict[str, list[str]]:
    """symbol -> the names it is recognised by, longest first."""
    out = {}
    for i in universe.issuers:
        if not i.name:
            continue
        names = {re.sub(r"\s*\(.*?\)", "", i.name).strip(), *(getattr(i, "aliases", None) or [])}
        inner = re.search(r"\((.+?)\)", i.name)
        if inner:
            names.add(inner.group(1))
        s = short_name(i.name)
        if s:
            names.add(s)
        out[i.symbol] = sorted((n for n in names if n and n not in TOO_GENERIC),
                               key=lambda n: (-len(n), n))
    return out


def _matcher(al: dict[str, list[str]]):
    owner: dict[str, str] = {}
    for sym, names in al.items():
        for n in names:
            owner.setdefault(n, sym)  # a name shared by two companies stays with the first
    if not owner:
        return None, owner
    alt = "|".join(re.escape(n) for n in sorted(owner, key=lambda n: -len(n)))
    # Case-sensitive, whole words: "Coherent" the company, not "coherent optics".
    return re.compile(rf"(?<![A-Za-z0-9])(?:{alt})(?![A-Za-z0-9])"), owner


def _window(text: str, start: int, end: int) -> str:
    before, after = text[:start].split(), text[end:].split()
    cut = " ".join(before[-WINDOW_WORDS:]) + " " + text[start:end] + " " + \
        " ".join(after[:WINDOW_WORDS])
    return ("… " if len(before) > WINDOW_WORDS else "") + cut.strip() + \
        (" …" if len(after) > WINDOW_WORDS else "")


def candidates(docs: pd.DataFrame, universe, as_of: pd.Timestamp, chunk_words: int = 350,
               min_words: int = 40, lookback_months: int = LOOKBACK_MONTHS) -> dict:
    """(speaker, named) -> up to PER_PAIR passages, newest first, each from a
    different document: {passage_id, date, role, doc_type, url, text}."""
    from ..text.passages import passages_from_documents
    from .clusters import passage_roles
    rx, owner = _matcher(aliases(universe))
    if rx is None or not len(docs):
        return {}
    as_of = pd.Timestamp(as_of)
    as_of = as_of.tz_localize("UTC") if as_of.tzinfo is None else as_of
    pub = pd.to_datetime(docs["published_at"], utc=True)
    recent = docs.loc[(pub > as_of - pd.DateOffset(months=lookback_months)) & (pub <= as_of)]
    covered = {i.symbol for i in universe.issuers}
    recent = recent.loc[recent["symbol"].isin(covered)]
    # Cheap first pass on whole documents: most name no other covered company.
    recent = recent.loc[[bool(rx.search(t or "")) for t in recent["text"]]]
    if not len(recent):
        return {}
    p = passages_from_documents(recent, chunk_words, min_words)
    if not len(p):
        return {}
    p = p.assign(role=passage_roles(p))
    p = p.loc[p["role"] != "analyst_question"]
    found: dict[tuple[str, str], list[dict]] = {}
    for row in p.itertuples():
        seen = set()
        for m in rx.finditer(row.text):
            other = owner[m.group(0)]
            if other == row.symbol or other in seen:
                continue
            seen.add(other)
            found.setdefault((row.symbol, other), []).append({
                "passage_id": row.passage_id, "doc_id": row.doc_id,
                "date": str(pd.Timestamp(row.published_at).date()), "role": row.role,
                "doc_type": row.doc_type, "url": row.url if isinstance(row.url, str) else None,
                "text": _window(row.text, m.start(), m.end())})
    out = {}
    for pair, rows in found.items():
        rows.sort(key=lambda r: (r["date"], r["passage_id"]), reverse=True)
        keep, docs_seen = [], set()
        for r in rows:
            if r["doc_id"] in docs_seen:
                continue
            docs_seen.add(r["doc_id"])
            keep.append({k: v for k, v in r.items() if k != "doc_id"})
            if len(keep) == PER_PAIR:
                break
        out[pair] = keep
    return out


def pair_key(rows: list[dict]) -> str:
    text = f"{PROMPT_VERSION}|" + "|".join(r["passage_id"] for r in rows)
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def build_prompt(speaker: str, other: str, rows: list[dict]) -> str:
    ps = "\n\n".join(f"[{r['passage_id']}] {r['date']} · {r['role']}\n{r['text']}" for r in rows)
    return PROMPT.format(speaker=speaker, other=other, passages=ps)


class Relations:
    """What has been read so far, kept in `<state>/relations.json`."""

    def __init__(self, state_dir: Path):
        self.path = Path(state_dir) / "relations.json"
        self.pairs: dict[str, dict] = {}
        if self.path.exists():
            self.pairs = json.loads(self.path.read_text()).get("pairs", {})

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"prompt_version": PROMPT_VERSION, "pairs": self.pairs},
                                  indent=1))
        tmp.replace(self.path)

    def todo(self, cands: dict) -> list[tuple[str, str]]:
        """Pairs whose passages have not been read (new pair, or newer passages)."""
        return sorted(pair for pair, rows in cands.items()
                      if self.pairs.get(">".join(pair), {}).get("key") != pair_key(rows))


def read_pairs(store: Relations, cands: dict, names: dict[str, str], namer, now: str,
               limit: int | None = None, log=lambda *a: None) -> dict:
    """Ask the writer about each unread pair (newest evidence first), in place.
    An answer that cites a passage it was not shown is recorded as "none"."""
    ask = getattr(namer, "relation", None)
    todo = store.todo(cands)
    todo.sort(key=lambda pair: cands[pair][0]["date"], reverse=True)
    counts = {"read": 0, "kept": 0, "none": 0, "waiting": len(todo), "errors": {}}
    if ask is None:
        return counts
    for n, pair in enumerate(todo):
        if limit is not None and counts["read"] >= limit:
            break
        rows = cands[pair]
        try:
            answer, model = ask(build_prompt(names.get(pair[0], pair[0]),
                                             names.get(pair[1], pair[1]), rows))
        except Exception as e:  # noqa: BLE001 - one pair's failure is reported
            counts["errors"][">".join(pair)] = f"{type(e).__name__}: {e}"
            continue
        kind = answer.get("relationship")
        ev = next((r for r in rows if r["passage_id"] == answer.get("evidence")), None)
        if kind not in KINDS or kind == "none" or ev is None:
            rec = {"relationship": "none",
                   "note": None if kind == "none" else "answer cited no passage it was shown"}
        else:
            rec = {"relationship": kind, "evidence": ev,
                   "summary": " ".join(str(answer.get("summary", "")).split())}
        store.pairs[">".join(pair)] = {**rec, "key": pair_key(rows), "model": model, "at": now}
        counts["read"] += 1
        counts["kept" if rec["relationship"] != "none" else "none"] += 1
        if (n + 1) % 25 == 0:
            store.save()
            log(f"relations: {counts['read']} pairs read")
    counts["waiting"] = len(todo) - counts["read"] - len(counts["errors"])
    store.save()
    return counts


def links(store: Relations, covered: set[str]) -> list[dict]:
    """One link per pair and kind, from both companies' statements.
    supply: `a` supplies `b`. ownership: `a` owns (part of) `b`. partner and
    competitor have no direction (a < b)."""
    out: dict[tuple, dict] = {}
    for key, rec in sorted(store.pairs.items()):
        kind = rec.get("relationship")
        if kind in (None, "none"):
            continue
        speaker, other = key.split(">")
        if speaker not in covered or other not in covered:
            continue
        if kind in ("supplies", "buys_from"):
            a, b, k = (speaker, other, "supply") if kind == "supplies" \
                else (other, speaker, "supply")
        elif kind in ("owns_stake_in", "owned_by"):
            a, b, k = (speaker, other, "ownership") if kind == "owns_stake_in" \
                else (other, speaker, "ownership")
        else:
            a, b, k = (*sorted((speaker, other)), kind)
        link = out.setdefault((k, a, b), {"kind": k, "a": a, "b": b, "statements": []})
        ev = rec["evidence"]
        link["statements"].append({"said_by": speaker, "date": ev["date"], "role": ev["role"],
                                   "doc_type": ev["doc_type"], "url": ev.get("url"),
                                   "summary": rec.get("summary"), "text": ev["text"]})
    for link in out.values():
        link["statements"].sort(key=lambda s: s["date"], reverse=True)
        link["last_said"] = link["statements"][0]["date"]
        link["both_sides"] = len({s["said_by"] for s in link["statements"]}) > 1
    return sorted(out.values(), key=lambda x: (x["kind"], x["a"], x["b"]))
