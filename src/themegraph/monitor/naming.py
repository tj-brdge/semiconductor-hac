"""Readable theme titles and summaries (docs/MONITOR.md, "Theme labels").

The topic layer names a theme by its top keywords ("ethernet · transceivers ·
enterprise"). Readers need a title and a one- or two-sentence explanation. Each
published theme gets a label from the first source that applies:

1. curated:  config/theme_labels.yaml, written by a person, keyed by theme ID;
2. claude:   written by Claude from the theme's quoted evidence (optional: needs
             the `anthropic` package and credentials on the machine running the
             refresh; never in the browser). Claude must cite the passages that
             support the label, and may answer that the evidence is ambiguous;
3. keywords: an automatic title from the keywords, plus a factual summary (who
             discusses it, in which sectors, from what kind of text). No thesis.

A label is (re)written only when a theme has none, or its keyword name changed
(the registry renames a theme only when its keywords drift), so a rerun calls
nothing. Every label is kept in `label_history` with its source, time and the
keyword name it was written for. Theme IDs never change. Labels describe
discussion only; they are not written from, or about, stock returns.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import yaml

LABELS = Path(__file__).resolve().parents[3] / "config" / "theme_labels.yaml"
DEFAULT_MODEL = "claude-opus-5-5"
MAX_QUOTES = 10
TITLE_WORDS = 8


def _now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")


def _latest(th: dict) -> dict:
    return next((r for r in reversed(th["measures"]) if r.get("present")), {})


def _join(words: list[str]) -> str:
    words = [w for w in words if w]
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " and " + words[-1]


def keyword_label(th: dict) -> dict:
    """Honest automatic label: says what is measured, claims nothing more."""
    kw = [k for k in th.get("keywords", []) if k][:3]
    title = _join(kw).capitalize() if kw else th["id"]
    m = _latest(th)
    if m:
        roles = m.get("evidence_roles", {})
        mgmt = roles.get("management_remarks", 0) + roles.get("management_answer", 0)
        mix = max([("management commentary", mgmt), ("SEC filings", roles.get("filing", 0)),
                   ("analyst questions", roles.get("analyst_question", 0))],
                  key=lambda x: x[1])[0]
        sectors = m.get("sectors", [])
        summary = (f"Discussed by {m['n_issuers']} monitored companies"
                   f"{' in ' + _join(sectors) if sectors else ''}, mostly in {mix}.")
    else:
        summary = "Not discussed in the latest month."
    return {"title": title, "summary": summary, "source": "keywords", "status": "automatic",
            "evidence": [], "model": None}


def curated_labels(path: Path = LABELS) -> dict[str, dict]:
    if not path.exists():
        return {}
    raw = yaml.safe_load(path.read_text()) or {}
    out = {}
    for tid, v in raw.items():
        if not v.get("title") or not v.get("summary"):
            raise ValueError(f"{path.name}: {tid} needs a title and a summary")
        out[tid] = {"title": v["title"], "summary": v["summary"], "source": "curated",
                    "status": "ok", "evidence": list(v.get("evidence", [])),
                    "model": None, "by": v.get("by")}
    return out


# -- Claude -------------------------------------------------------------------------

SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["clear", "ambiguous"]},
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "evidence": {"type": "array", "items": {"type": "string"}},
        "reason": {"type": "string"},
    },
    "required": ["status", "title", "summary", "evidence", "reason"],
    "additionalProperties": False,
}

PROMPT = """You are labelling a theme found by a text-clustering system in the earnings \
calls and SEC filings of a monitored set of public companies. Write a label a \
general reader understands.

Rules:
- Use only the passages below. Do not use outside knowledge to add claims, numbers, \
forecasts or company names that the passages do not support.
- Title: at most {words} words, plain English, no tickers, no hype, no investment \
view (not "boom", "winners", "bullish").
- Summary: one or two sentences on what the companies are discussing. Describe \
discussion, not stock performance, and make no prediction.
- Passages marked analyst_question are questions from analysts, not company \
statements; never present them as what a company said.
- evidence: the passage IDs that support the title and summary (at least two, from \
at least two companies).
- If the passages do not share one clear subject, answer status "ambiguous" with \
your best short title, and say in reason what is mixed.

Theme {tid}. Distinctive terms: {keywords}.
Companies: {companies}.

Passages:
{passages}"""


def evidence_for(th: dict) -> list[dict]:
    seen, out = set(), []
    for q in th["evidence"].get("latest", []) + th["evidence"].get("first", []):
        if q["passage_id"] not in seen:
            seen.add(q["passage_id"])
            out.append(q)
    return out[:MAX_QUOTES]


def build_prompt(th: dict, sectors: dict[str, str]) -> str:
    m = _latest(th)
    comp = ", ".join(f"{s} ({sectors.get(s, '?')})" for s in m.get("issuers", [])) or "—"
    ps = "\n\n".join(f"[{q['passage_id']}] {q['symbol']} · {q['date']} · {q['role']}\n"
                     f"{q['text']}" for q in evidence_for(th))
    return PROMPT.format(words=TITLE_WORDS, tid=th["id"],
                         keywords=", ".join(th.get("keywords", [])[:8]),
                         companies=comp, passages=ps)


class ClaudeNamer:
    """Calls Claude with structured output. Credentials come from the environment
    as the `anthropic` SDK resolves them (server side only)."""

    def __init__(self, model: str = DEFAULT_MODEL):
        import anthropic  # optional dependency: `pip install -e '.[review]'`
        self.client = anthropic.Anthropic()
        self.model = model

    def __call__(self, prompt: str) -> tuple[dict, str]:
        r = self.client.beta.messages.create(
            model=self.model, max_tokens=2000,
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            output_config={"effort": "low",
                           "format": {"type": "json_schema", "schema": SCHEMA}},
            messages=[{"role": "user", "content": prompt}])
        if r.stop_reason in ("refusal", "max_tokens"):
            raise RuntimeError(f"no label ({r.stop_reason})")
        text = next(b.text for b in r.content if b.type == "text")
        return json.loads(text), r.model


def checked(th: dict, answer: dict, model: str) -> dict:
    """Accept Claude's label only if it is grounded in the passages it was shown;
    otherwise fall back to the automatic label and say why."""
    quotes = {q["passage_id"]: q for q in evidence_for(th)}
    cited = [p for p in answer.get("evidence", []) if p in quotes]
    firms = {quotes[p]["symbol"] for p in cited}
    title = " ".join(str(answer.get("title", "")).split())
    problems = []
    if answer.get("status") != "clear":
        problems.append("evidence is ambiguous: " + str(answer.get("reason", ""))[:200])
    if len(cited) < 2 or len(firms) < 2:
        problems.append("fewer than two cited passages from two companies")
    if len(cited) < len(answer.get("evidence", [])):
        problems.append("cited passages it was not shown")
    if not title or len(title.split()) > TITLE_WORDS:
        problems.append("title missing or too long")
    if problems:
        lab = keyword_label(th)
        lab.update(status="ambiguous", note="; ".join(problems), model=model,
                   rejected={"title": title, "summary": answer.get("summary")})
        return lab
    return {"title": title, "summary": " ".join(str(answer["summary"]).split()),
            "source": "claude", "status": "ok", "evidence": cited, "model": model}


def needs_label(th: dict, writer_available: bool = False) -> bool:
    """No label, keywords drifted since it was written, or only an automatic label
    while a writer is available (an ambiguous verdict is not retried)."""
    lab = th.get("label")
    return lab is None or lab.get("for_name") != th["name"] or (
        writer_available and lab.get("status") == "automatic")


def apply_labels(themes: list[dict], sectors: dict[str, str], namer=None,
                 curated: dict | None = None, now: str | None = None,
                 limit: int | None = None) -> dict:
    """Label published themes in place. `namer` is a callable prompt -> (answer,
    model), or None for automatic labels only. Returns counts and errors; an error
    for one theme leaves its previous label (or an automatic one) in place."""
    curated = curated_labels() if curated is None else curated
    now = now or _now()
    counts = {"curated": 0, "claude": 0, "ambiguous": 0, "keywords": 0, "errors": {}}
    calls = 0
    for th in themes:
        cur = curated.get(th["id"])
        if cur is not None:
            if (th.get("label") or {}).get("source") == "curated" and \
                    {k: th["label"].get(k) for k in ("title", "summary")} == \
                    {k: cur[k] for k in ("title", "summary")}:
                continue
            lab = cur
        elif not needs_label(th, namer is not None):
            continue
        elif namer is not None and (limit is None or calls < limit):
            calls += 1
            try:
                answer, model = namer(build_prompt(th, sectors))
                lab = checked(th, answer, model)
            except Exception as e:  # noqa: BLE001 - one theme's failure is reported
                counts["errors"][th["id"]] = f"{type(e).__name__}: {e}"
                if th.get("label"):
                    continue
                lab = keyword_label(th)
        else:
            # No writer available (or over this refresh's limit): a label written for
            # earlier keywords would describe a topic that has since drifted.
            lab = keyword_label(th)
        old = th.get("label") or {}
        if all(old.get(k) == lab.get(k) for k in ("title", "summary", "source", "status")) \
                and old.get("for_name") == th["name"]:
            continue  # nothing new (keeps reruns idempotent)
        lab = {**lab, "for_name": th["name"], "at": now}
        th["label"] = lab
        th.setdefault("label_history", []).append(lab)
        key = "ambiguous" if lab["status"] == "ambiguous" else lab["source"]
        counts[key] += 1
    return counts
