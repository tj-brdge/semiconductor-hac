"""Local adjudication review (`themegraph review RUN_DIR ...`).

A page served on 127.0.0.1 that reads and writes each run's `adjudication.yaml`
directly: one candidate at a time, its as-of keywords and quoted passages, and
optionally Claude's suggested decision (DESIGN.md, "AI-assisted adjudication").

Rules enforced here, not in the page:
- The blind sample (BLIND_SAMPLE unmatched candidates per run, seeded from the
  candidate ledger's hash and stored in `adjudication_review.json`) never shows
  Claude's suggestion until the person has decided; that first decision is kept as
  `blind_human_decision`.
- Theme-match and negative-control entries are decided by the person only.
- Claude's suggestions can be recorded as decisions (`decided_by: ai`) only for
  unmatched candidates outside the sample, and only once the sample is complete.
- Claude sees keywords, quotes, issuers and dates; never returns or report.html.

The server binds to localhost and every API call must carry the session token
printed in the URL, so other pages in the browser cannot read or write decisions.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import random
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pandas as pd
import yaml

from ..eval import adjudication as A

BLIND_SAMPLE = 20
BATCH = 6
STATE = "adjudication_review.json"
PROMPT_VERSION = "adj-prompt-1"
DEFAULT_MODEL = "claude-opus-5-5"
PAGE = Path(__file__).with_name("desk.html")


# -- prompts -------------------------------------------------------------------

UNMATCHED = """You are adjudicating candidates produced by a text-mining system that reads \
earnings-call transcripts and SEC filings of semiconductor and related companies. Each \
candidate is a cluster of passages that grew quickly around its first-flag date. You see \
only the text. You are not told whether any candidate matches a known theme, and you must \
not consider stock prices or later events.

For each candidate decide one of:
- "real_unlabelled_theme": the keywords and quotes describe one coherent, specific business \
subject (a technology, product cycle, end market, supply condition or policy) that several \
of the issuers discuss.
- "noise": boilerplate (risk factors, accounting, legal, compensation, forward-looking \
statements, call logistics), generic financial talk with no specific driver (guidance, \
margins, capex in general), or an incoherent mix.
- "duplicate": the same subject as one of the earlier candidates listed for it; give that \
candidate's id in "duplicate_of".
Be conservative: inventory, guidance or margin talk is noise unless the quotes show a \
specific new driver. Give one decision per candidate, in the order given, with a one-sentence \
reason citing the text; "duplicate_of" is "" unless the decision is duplicate.

"""

MATCHED = """You are checking automatic matches made by a text-mining system that reads \
earnings-call transcripts and SEC filings of semiconductor and related companies. The system \
matched each candidate below to a labelled theme because the companies overlap and the \
timing fits. Overlap alone is not enough: decide from the text whether the candidate is \
really about that theme. Do not consider stock prices or later events.

For each candidate decide "confirmed" (the keywords and quotes are about the named theme) or \
"rejected" (they are about something else involving the same companies). Give one decision \
per candidate, in the order given, with a one-sentence reason citing the text; \
"duplicate_of" is always "".

"""


def _schema(choices: list[str]) -> dict:
    item = {"type": "object",
            "properties": {"id": {"type": "string"},
                           "decision": {"type": "string", "enum": choices},
                           "duplicate_of": {"type": "string"},
                           "reason": {"type": "string"}},
            "required": ["id", "decision", "duplicate_of", "reason"],
            "additionalProperties": False}
    return {"type": "object", "properties": {"decisions": {"type": "array", "items": item}},
            "required": ["decisions"], "additionalProperties": False}


class ClaudeSuggester:
    """Asks Claude for decisions on a batch of entries of one kind. Uses the
    `anthropic` SDK (install with `uv sync --extra review`); credentials come from
    the environment as the SDK resolves them."""

    def __init__(self, model: str = DEFAULT_MODEL):
        import anthropic  # optional dependency
        self.client = anthropic.Anthropic()
        self.model = model

    def __call__(self, prompt: str, choices: list[str]) -> tuple[list[dict], str]:
        r = self.client.beta.messages.create(
            model=self.model, max_tokens=8000,
            betas=["server-side-fallback-2026-07-01"], fallbacks="default",
            output_config={"effort": "medium",
                           "format": {"type": "json_schema", "schema": _schema(choices)}},
            messages=[{"role": "user", "content": prompt}])
        if r.stop_reason == "refusal":
            raise RuntimeError("Claude declined this batch")
        if r.stop_reason == "max_tokens":
            raise RuntimeError("Claude's answer was cut short; try again")
        text = next(b.text for b in r.content if b.type == "text")
        return json.loads(text)["decisions"], r.model


# -- store -----------------------------------------------------------------------

class ReviewStore:
    def __init__(self, runs: dict[str, Path], suggester=None):
        self.runs = {k: Path(v) for k, v in runs.items()}
        self.suggester = suggester
        self.lock = threading.Lock()
        self._cache: dict[str, dict] = {}
        for name in self.runs:
            self._prepare(name)

    # ---- per-run context (read once) ----
    def _prepare(self, run: str) -> None:
        d = self.runs[run]
        ws = d / A.WORKSHEET
        if not ws.exists():
            A.write_worksheet(d)
        led_path = d / "candidate_ledger.parquet"
        led = pd.read_parquet(led_path)
        firsts = led.loc[led["t"] == led["t_first_flag"]].drop_duplicates("candidate_id")
        info = {r.candidate_id: {"first_flag": str(pd.Timestamp(r.t).date()),
                                 "issuers": list(r.issuers)} for r in firsts.itertuples()}
        expl = A._explanations(d)
        state_path = d / STATE
        if state_path.exists():
            state = json.loads(state_path.read_text())
        else:
            seed = hashlib.sha256(led_path.read_bytes()).hexdigest()
            unmatched = sorted(e["id"] for e in A.load(ws)
                               if e["kind"] == "unmatched_candidate" and not e.get("stale"))
            k = min(BLIND_SAMPLE, len(unmatched))
            state = {"seed": seed, "sample_ids": random.Random(seed).sample(unmatched, k),
                     "created": dt.datetime.now(dt.UTC).isoformat(timespec="seconds")}
            state_path.write_text(json.dumps(state, indent=1))
        self._cache[run] = {"info": info, "expl": expl, "sample": set(state["sample_ids"])}

    def _entries(self, run: str) -> list[dict]:
        return A.load(self.runs[run] / A.WORKSHEET)

    def _save(self, run: str, entries: list[dict]) -> None:
        d = self.runs[run]
        tmp = d / (A.WORKSHEET + ".tmp")
        tmp.write_text(yaml.safe_dump(entries, sort_keys=False, allow_unicode=True))
        A.load(tmp)  # validate before replacing the worksheet
        tmp.replace(d / A.WORKSHEET)
        A.write_packet(d, entries)

    def _blind(self, run: str, e: dict) -> bool:
        """True while Claude's suggestion must stay hidden for this entry."""
        return e["id"] in self._cache[run]["sample"] and not e.get("blind_human_decision")

    # ---- reads ----
    def view(self, run: str) -> dict:
        c = self._cache[run]
        out = []
        for e in self._entries(run):
            if e.get("stale"):
                continue
            x = c["expl"].get(e["candidate_id"], {})
            i = c["info"].get(e["candidate_id"], {})
            v = {"id": e["id"], "kind": e["kind"], "theme": e.get("theme"),
                 "candidate_id": e["candidate_id"], "first_flag": i.get("first_flag"),
                 "issuers": i.get("issuers", []), "keywords": list(x.get("keywords", [])),
                 "quotes": [{"symbol": q.get("symbol"), "date": str(q.get("available_at"))[:10],
                             "passage_id": q.get("passage_id"), "text": q.get("text", "")}
                            for q in x.get("quotes", [])],
                 "decision": e["decision"], "reviewer": e.get("reviewer"),
                 "reviewed_on": e.get("reviewed_on"), "notes": e.get("notes") or "",
                 "decided_by": e.get("decided_by") or ("human" if e["decision"] != "pending"
                                                       else None),
                 "in_sample": e["id"] in c["sample"],
                 "blind_human_decision": e.get("blind_human_decision"),
                 "has_ai": bool(e.get("ai_suggestion"))}
            if not self._blind(run, e):
                v.update({k: e.get(k) for k in ("ai_suggestion", "ai_reason", "ai_model",
                                                 "ai_prompt_version")})
            out.append(v)
        return {"run": run, "entries": out, "stats": self.stats(run),
                "suggestions_available": self.suggester is not None}

    def stats(self, run: str) -> dict:
        es = [e for e in self._entries(run) if not e.get("stale")]
        sample = [e for e in es if e["id"] in self._cache[run]["sample"]]
        paired = [e for e in sample if e.get("blind_human_decision") and e.get("ai_suggestion")]
        return {"n": len(es),
                "decided_by_human": sum(e["decision"] != "pending"
                                        and e.get("decided_by") != "ai" for e in es),
                "decided_by_ai": sum(e.get("decided_by") == "ai" for e in es),
                "pending": sum(e["decision"] == "pending" for e in es),
                "need_ai": sum(not e.get("ai_suggestion") for e in es),
                "sample_n": len(sample),
                "sample_done": sum(bool(e.get("blind_human_decision")) for e in sample),
                "paired": len(paired),
                "agree": sum(e["blind_human_decision"] == e["ai_suggestion"] for e in paired),
                "acceptable": len(self._acceptable(run, es))}

    def _acceptable(self, run: str, es: list[dict]) -> list[dict]:
        sample = self._cache[run]["sample"]
        done = all(e.get("blind_human_decision") and e.get("ai_suggestion")
                   for e in es if e["id"] in sample)
        if not done:
            return []
        return [e for e in es if e["kind"] == "unmatched_candidate" and e["id"] not in sample
                and e["decision"] == "pending" and e.get("ai_suggestion")
                and not e.get("stale")]

    # ---- writes ----
    def decide(self, run: str, entry_id: str, decision: str, reviewer: str,
               notes: str = "") -> None:
        if not reviewer.strip():
            raise ValueError("enter your name before deciding")
        with self.lock:
            entries = self._entries(run)
            e = next((x for x in entries if x["id"] == entry_id and not x.get("stale")), None)
            if e is None:
                raise ValueError(f"no entry {entry_id} in {run}")
            if decision == "pending":
                e.update(decision="pending", reviewer=None, reviewed_on=None, decided_by=None)
            else:
                if decision not in A.DECISIONS[e["kind"]]:
                    raise ValueError(f"{decision!r} is not a decision for {e['kind']}")
                if self._blind(run, e):
                    e["blind_human_decision"] = decision
                e.update(decision=decision, reviewer=reviewer.strip(),
                         reviewed_on=dt.date.today().isoformat(), decided_by="human")
            e["notes"] = notes
            self._save(run, entries)

    def accept_ai(self, run: str) -> int:
        with self.lock:
            entries = self._entries(run)
            todo = {e["id"] for e in self._acceptable(run, entries)}
            for e in entries:
                if e["id"] in todo:
                    e.update(decision=e["ai_suggestion"], reviewer=e.get("ai_model") or "claude",
                             reviewed_on=dt.date.today().isoformat(), decided_by="ai",
                             notes=e.get("ai_reason") or "")
            self._save(run, entries)
            return len(todo)

    def suggest_next(self, run: str) -> dict:
        """Ask Claude about the next batch of entries without a suggestion (theme
        matches first). Returns how many were done and how many remain."""
        if self.suggester is None:
            raise RuntimeError("Claude suggestions are off: install the review extra "
                               "(uv sync --extra review) and set ANTHROPIC_API_KEY")
        entries = [e for e in self._entries(run) if not e.get("stale")]
        todo = [e for e in entries if not e.get("ai_suggestion")]
        if not todo:
            return {"done": 0, "remaining": 0}
        todo.sort(key=lambda e: e["kind"] == "unmatched_candidate")
        kind_is_unmatched = todo[0]["kind"] == "unmatched_candidate"
        batch = [e for e in todo if (e["kind"] == "unmatched_candidate") == kind_is_unmatched][
            :BATCH]
        prompt = self._prompt(run, batch, entries, kind_is_unmatched)
        choices = sorted(A.DECISIONS["unmatched_candidate"] if kind_is_unmatched
                         else A.DECISIONS["matched_theme"])
        answers, model = self.suggester(prompt, choices)
        by_id = {a.get("id"): a for a in answers}
        with self.lock:
            fresh = self._entries(run)
            got = 0
            for e in fresh:
                a = by_id.get(e["id"])
                if e["id"] in {b["id"] for b in batch} and a and \
                        a.get("decision") in A.DECISIONS[e["kind"]]:
                    reason = str(a.get("reason", ""))[:600]
                    if a.get("duplicate_of"):
                        reason = f"duplicate of {a['duplicate_of']}: {reason}"
                    e.update(ai_suggestion=a["decision"], ai_reason=reason, ai_model=model,
                             ai_prompt_version=PROMPT_VERSION)
                    got += 1
            self._save(run, fresh)
        return {"done": got, "missing": len(batch) - got,
                "remaining": len(todo) - got}

    def _block(self, run: str, e: dict) -> str:
        c = self._cache[run]
        x = c["expl"].get(e["candidate_id"], {})
        i = c["info"].get(e["candidate_id"], {})
        quotes = [f"  [{q.get('symbol')} {str(q.get('available_at'))[:10]}] "
                  f"{str(q.get('text', ''))[:700]}" for q in x.get("quotes", [])[:6]]
        return "\n".join([f"id: {e['id']}", f"first flag: {i.get('first_flag')}",
                          f"issuers: {', '.join(i.get('issuers', []))}",
                          f"keywords: {', '.join(x.get('keywords', []))}", "quotes:", *quotes])

    def _prompt(self, run: str, batch: list[dict], entries: list[dict], unmatched: bool) -> str:
        if not unmatched:
            return MATCHED + "\n\n---\n\n".join(
                f"labelled theme: {e['theme'].replace('_', ' ')}\n" + self._block(run, e)
                for e in batch)
        c = self._cache[run]

        def earlier(e):
            t = c["info"].get(e["candidate_id"], {}).get("first_flag")
            if not t:
                return "  none"
            t0 = pd.Timestamp(t)
            rows = []
            for o in entries:  # keywords only: no theme names, no decisions
                f = c["info"].get(o["candidate_id"], {}).get("first_flag")
                if o["id"] != e["id"] and f and 0 < (t0 - pd.Timestamp(f)).days <= 365:
                    kw = c["expl"].get(o["candidate_id"], {}).get("keywords", [])[:8]
                    rows.append(f"  - {o['candidate_id']} ({f}): {', '.join(kw)}")
            return "\n".join(rows[-25:]) or "  none"
        return UNMATCHED + "\n\n---\n\n".join(
            self._block(run, e) + "\nearlier candidates (past 365 days, keywords only):\n"
            + earlier(e) for e in batch)


# -- HTTP ------------------------------------------------------------------------

def make_handler(store: ReviewStore, token: str):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):  # quiet
            pass

        def _send(self, code: int, body: bytes, ctype: str = "application/json") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj) -> None:
            self._send(code, json.dumps(obj, default=str).encode())

        def _authorised(self) -> bool:
            return secrets.compare_digest(self.headers.get("X-Review-Token", ""), token)

        def do_GET(self):
            path = self.path.split("?")[0]
            if path in ("/", "/index.html"):
                self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            elif not self._authorised():
                self._json(403, {"error": "missing or wrong session token"})
            elif path == "/api/runs":
                self._json(200, {"runs": list(store.runs)})
            elif path.startswith("/api/run/"):
                run = path[len("/api/run/"):]
                if run not in store.runs:
                    self._json(404, {"error": "unknown run"})
                else:
                    self._json(200, store.view(run))
            else:
                self._json(404, {"error": "not found"})

        def do_POST(self):
            if not self._authorised():
                return self._json(403, {"error": "missing or wrong session token"})
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
                run = body.get("run")
                if run not in store.runs:
                    return self._json(404, {"error": "unknown run"})
                if self.path == "/api/decide":
                    store.decide(run, body["id"], body["decision"], body.get("reviewer", ""),
                                 body.get("notes", ""))
                    return self._json(200, store.view(run))
                if self.path == "/api/suggest":
                    out = store.suggest_next(run)
                    return self._json(200, {**out, **store.view(run)})
                if self.path == "/api/accept_ai":
                    k = store.accept_ai(run)
                    return self._json(200, {"accepted": k, **store.view(run)})
                return self._json(404, {"error": "not found"})
            except (ValueError, KeyError) as e:
                return self._json(400, {"error": str(e)})
            except Exception as e:  # noqa: BLE001 - surface API/SDK failures to the page
                return self._json(502, {"error": f"{type(e).__name__}: {e}"})
    return Handler


def serve(runs: dict[str, Path], port: int = 8765, model: str = DEFAULT_MODEL,
          open_browser: bool = True) -> None:
    try:
        suggester = ClaudeSuggester(model)
    except Exception as e:  # noqa: BLE001 - missing SDK or credentials: run without it
        print(f"Claude suggestions off ({type(e).__name__}: {e}).")
        suggester = None
    store = ReviewStore(runs, suggester)
    token = secrets.token_urlsafe(16)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), make_handler(store, token))
    url = f"http://127.0.0.1:{httpd.server_address[1]}/#t={token}"
    print(f"Review page: {url}\nDecisions are written to each run's {A.WORKSHEET}. "
          "Press Ctrl+C to stop.")
    if open_browser:
        import webbrowser
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
