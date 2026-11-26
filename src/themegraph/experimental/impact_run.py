"""Economic-impact extraction pilot: the steps the owner runs (scripts/impact_pilot.py).

Protocol: docs/experiments/ECONOMIC_IMPACT_PILOT.md (version 1, frozen). This module
adds no rule of its own: the sample, labels, metrics and thresholds are those of
`impact_pilot`, and the texts shown to readers and printed before the model run
(guidelines, cost table, prices) are read from the protocol file at run time.

EXPERIMENTAL. Nothing here is imported by the monitor or feeds a published figure.

Steps, each a function here and a subcommand of the script:
- `export`  links stored passages to published themes for the registry's latest
            month-ends, read-only (see `export` for how and what it can cost);
- `sample`  draws the sample once and writes the blind reader sheets;
- `run`     asks the model once per sampled passage, with a cache (the only step
            that calls a paid API, and only with `confirm_cost`);
- `score`   reader agreement, adjudication, both methods' labels, the declared
            criteria and `result.md`;
- `status`  which steps are done.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import shutil
import tempfile
from pathlib import Path

import pandas as pd

from ..config import ROOT
from . import impact_pilot as P

DOC = ROOT / "docs" / "experiments" / "ECONOMIC_IMPACT_PILOT.md"
WORK = ROOT / "runs" / "impact_pilot"
MODEL = "claude-opus-5-5"          # protocol section 10: one model, no fallback
MAX_TOKENS = 16000                 # as monitor/naming.py: thinking counts towards it
READERS = ("a", "b")
LABELS = {"speaker_effect": P.EFFECTS, "opposite_party": ("yes", "no"),
          "statement_type": (*P.STATEMENT_TYPES, "none")}
SHEET_COLUMNS = ("passage_id", "company", "role", "date", "text", *LABELS)
EXPORT_EXTRA = ("company", "theme_tone", "month_end", "latest_month_end")
MAX_CONSECUTIVE_ERRORS = 3


class Refused(RuntimeError):
    """A step that will not run as asked. The message says why and what to do."""


def answers_path(state_dir: Path) -> Path:
    """Where model answers are kept (protocol section 8): outside git."""
    return Path(state_dir) / "experimental" / "impact_pilot" / "answers.jsonl"


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _csv(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, dtype=str, keep_default_na=False)


# -- the protocol file, read at run time ---------------------------------------------

def protocol_section(number: int, doc: Path = DOC) -> str:
    """The text of one numbered section of the protocol, heading included."""
    m = re.search(rf"^## {number}\. .*?(?=^## |\Z)", Path(doc).read_text(), re.M | re.S)
    if not m:
        raise Refused(f"section {number} not found in {doc}")
    return m.group(0).rstrip() + "\n"


def reader_guide(doc: Path = DOC) -> str:
    """The reader's guide: the labels and guidelines of the protocol's adjudication
    section, copied word for word."""
    section = protocol_section(5, doc).split("\n### ")[0]
    bullets = {m.group(1): m.group(0).strip() for m in re.finditer(
        r"^- \*\*(\w+)\.\*\*.*?(?=^- \*\*|\Z)", section, re.M | re.S)}
    missing = [k for k in ("Labels", "Guidelines") if k not in bullets]
    if missing:
        raise Refused(f"{doc}: adjudication section has no {' or '.join(missing)} entry")
    cols = "; ".join(f"`{c}`: {' / '.join(v)}" for c, v in LABELS.items())
    return "\n".join([
        "# Reader guide: economic-impact pilot", "",
        "Copied from the protocol (docs/experiments/ECONOMIC_IMPACT_PILOT.md, section 5) "
        "when the sample was drawn. The protocol is the authority.", "",
        "Label every row of your own sheet, alone. Do not discuss passages with the other "
        "reader, and do not open any other file in this folder, before both sheets are "
        "final.", "",
        f"Fill these columns, using exactly these words: {cols}.", "",
        bullets["Labels"], "", bullets["Guidelines"], ""])


def prices(doc: Path = DOC) -> tuple[float, float]:
    """US dollars per million input and output tokens, as stated in the protocol."""
    text = " ".join(protocol_section(9, doc).split())
    m = re.search(r"([\d.]+) US dollars per million input tokens and ([\d.]+) per million "
                  r"output tokens", text)
    if not m:
        raise Refused(f"{doc}: the prices sentence in section 9 was not found")
    return float(m.group(1)), float(m.group(2))


def dollars(input_tokens: int, output_tokens: int, doc: Path = DOC) -> float:
    pin, pout = prices(doc)
    return (input_tokens * pin + output_tokens * pout) / 1e6


# -- 1. export -----------------------------------------------------------------------

class _NoEmbed:
    """Stands in for the embedder so that the export cannot spend money: the
    embedding cache asks it only for passages it does not already hold."""

    def __init__(self, inner):
        self.model_id = inner.model_id

    def embed(self, texts):
        raise Refused(
            f"the embedding cache is incomplete: at least {len(texts)} stored passages "
            "have no cached embedding, and computing them is a paid API call. Nothing was "
            "written. Run again with --allow-embed to compute them, or first run "
            "`themegraph monitor refresh --no-ingest`, which fills the same cache.")


def _state_hashes(state_dir: Path) -> dict[str, str]:
    return {str(p.relative_to(state_dir)): _sha(p)
            for p in sorted(Path(state_dir).rglob("*")) if p.is_file()}


def _fingerprint(n_passages, by_issuer, n_documents) -> tuple:
    return (int(n_passages), tuple(sorted((str(k), int(v)) for k, v in by_issuer.items())),
            int(n_documents))


def link_clusters(reg, clusters_at: dict, mcfg) -> tuple[list[dict], list[dict]]:
    """Which rebuilt cluster is which published theme, at each month-end.

    Nothing is approximated. A cluster is linked to a theme at a month-end only when
    it equals what the registry recorded for that theme at that month-end: the same
    number of passages, the same number of passages for every company, and the same
    number of documents (`measures` in registry.json, written by `Registry.process`
    from the cluster it had matched). At the month-ends for which the registry still
    holds the state it matches against (a theme's `last_members` and its vector in
    vectors.npz are those of the month it was last seen), the registry's own rule,
    `Registry._match`, is run as well, and a link is kept only if both agree.

    Returns (links, unlinked): a link is {t, theme_id, cluster}; anything that is not
    an exact, unambiguous equal is listed in `unlinked` with the reason.
    """
    links, unlinked = [], []
    published = {t["id"]: t for t in reg.published()}
    for ts, clusters in clusters_at.items():
        prints: dict[tuple, list[int]] = {}
        for j, c in enumerate(clusters):
            prints.setdefault(_fingerprint(c.n_passages, c.passages_by_issuer,
                                           c.n_documents), []).append(j)
        # The registry's state is that of this month-end for themes last seen at it.
        cands = [k for k, th in reg.themes.items() if th["last_seen"] == ts]
        by_rule = {tid: j for j, tid in reg._match(cands, clusters, mcfg)[0].items()}
        for tid, th in published.items():
            row = next((r for r in th["measures"] if r["t"] == ts and r.get("present")), None)
            if row is None:
                continue
            hits = prints.get(_fingerprint(row["n_passages"], row["passages_by_issuer"],
                                           row["n_documents"]), [])
            why = None
            if len(hits) != 1:
                why = ("no rebuilt cluster equals the registry's record" if not hits else
                       f"{len(hits)} rebuilt clusters equal the registry's record")
            elif th["last_seen"] == ts and by_rule.get(tid) != hits[0]:
                why = "the registry's matching rule and its record disagree"
            if why:
                unlinked.append({"t": ts, "theme_id": tid, "reason": why,
                                 "n_passages": row["n_passages"]})
            else:
                links.append({"t": ts, "theme_id": tid, "cluster": clusters[hits[0]]})
    return links, unlinked


def export(state_dir: Path, work: Path = WORK, *, profile=None, fixtures: bool = False,
           months: int = 12, allow_embed: bool = False, allow_partial: bool = False,
           log=print) -> dict:
    """The approved one-off export: stored passages linked to published themes for
    the registry's latest `months` month-ends, written to `<work>/export.parquet`.

    Read-only. The registry keeps counts, not passage IDs, so the clusters of those
    month-ends are rebuilt from the stored documents and the cached embeddings with
    the code a refresh uses (`pipeline.build`, `SnapshotReader`), and each is linked
    to a theme as `link_clusters` describes. The registry is opened from a temporary
    copy and is never saved; the state directory's files are hashed before and after
    and the export fails if any changed. No document or price is downloaded.

    COST. Rebuilding reads embeddings from the cache. If a stored passage has no
    cached embedding, computing it is a paid API call: by default the export stops
    there and writes nothing; `allow_embed` permits the calls. Clustering is local.
    """
    from .. import pipeline
    from ..monitor import refresh as M
    from ..monitor.clusters import SnapshotReader
    from ..monitor.config import load_monitor
    from ..monitor.profiles import DEFAULT, load_profile
    from ..monitor.registry import Registry

    state_dir, work = Path(state_dir), Path(work)
    profile = profile or load_profile(DEFAULT)
    if not (state_dir / "registry.json").exists():
        raise Refused(f"no registry in {state_dir}: nothing to export")
    if (work / "sample.csv").exists():
        raise Refused(f"{work / 'sample.csv'} exists: the sample was drawn from the export "
                      "already there, so the export is not replaced")
    before = _state_hashes(state_dir)
    mcfg = load_monitor(profile.monitor)
    with tempfile.TemporaryDirectory() as tmp:
        for name in ("registry.json", "vectors.npz"):
            if (state_dir / name).exists():
                shutil.copyfile(state_dir / name, Path(tmp) / name)
        reg = Registry(Path(tmp), id_prefix=profile.id_prefix)   # a copy; never saved
    snaps = reg.data.get("snapshots", [])[-months:]
    if not snaps:
        raise Refused("the registry has processed no month-end")
    last = pd.Timestamp(snaps[-1])
    log(f"registry: {len(reg.published())} published themes; month-ends "
        f"{snaps[0][:10]} to {snaps[-1][:10]} ({len(snaps)})")

    d = M.load_inputs(fixtures, last.date(), log, profile)
    M._history_basis(reg, d)     # the universe as this registry reads it (copy only)
    embedder = d["embedder"] if allow_embed or fixtures else _NoEmbed(d["embedder"])
    log("rebuilding clusters from stored documents (no download; embeddings from the "
        "cache" + ("; missing ones WILL be computed, which costs money)" if allow_embed
                   else "; stops if any is missing)"))
    ctx = pipeline.build(d["prices"], d["docs"], d["cfg"], d["universe"], embedder,
                         d.get("cache", state_dir / "cache"), "forward", log=log,
                         end=last.date())
    if ctx.vectors is None:
        raise Refused("no stored documents: nothing to export")
    reader = SnapshotReader(ctx, mcfg)
    have = set(ctx.snapshots)
    clusters_at = {ts: reader.clusters(pd.Timestamp(ts)) if pd.Timestamp(ts) in have else []
                   for ts in snaps}
    links, unlinked = link_clusters(reg, clusters_at, mcfg)

    tones = P.theme_tones(reg.published())
    sector = M.coverage(d, mcfg).sector
    names = {i.symbol: i.name or i.symbol for i in d["universe"].issuers}
    frames = []
    for k in links:
        idx = reader.pos.reindex(k["cluster"].members).dropna().astype(int).to_numpy()
        p = reader.p.iloc[idx]
        frames.append(pd.DataFrame({
            "passage_id": p["passage_id"].astype(str).to_numpy(), "theme_id": k["theme_id"],
            "symbol": p["symbol"].to_numpy(),
            "sector": [sector.get(s, "?") for s in p["symbol"]],
            "role": reader.role[idx], "text": reader.text[idx],
            "available_at": pd.to_datetime(p["available_at"], utc=True).to_numpy(),
            "company": [names.get(s, s) for s in p["symbol"]],
            "theme_tone": tones.get(k["theme_id"], ""), "month_end": k["t"],
            "latest_month_end": snaps[-1]}))
    cols = [*P.SAMPLE_COLUMNS, *EXPORT_EXTRA]
    out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=cols)
    # One row per passage and theme: the latest month-end at which it was a member.
    out = out.sort_values(["passage_id", "theme_id", "month_end"], kind="stable") \
        .drop_duplicates(["passage_id", "theme_id"], keep="last")[cols].reset_index(drop=True)

    if _state_hashes(state_dir) != before:
        raise Refused(f"files in {state_dir} changed during the export; nothing was "
                      "written. Is a refresh running? Run the export again afterwards.")
    per = {ts: {"linked": sum(k["t"] == ts for k in links),
                "unlinked": sum(u["t"] == ts for u in unlinked)} for ts in snaps}
    for ts, c in per.items():
        log(f"  {ts[:10]}: {c['linked']} of {c['linked'] + c['unlinked']} published themes "
            "present were linked exactly")
    report = {"protocol_version": P.PROTOCOL_VERSION, "state": str(state_dir),
              "profile": profile.name, "month_ends": snaps, "by_month_end": per,
              "unlinked": unlinked, "rows": len(out),
              "passages": int(out["passage_id"].nunique()),
              "themes": int(out["theme_id"].nunique()),
              "rows_by_tone": out["theme_tone"].value_counts().to_dict(),
              "rows_by_role": out["role"].value_counts().to_dict(),
              "embeddings_computed_allowed": bool(allow_embed), "state_unchanged": True}
    if unlinked and not allow_partial:
        lines = "\n".join(f"  {u['t'][:10]} {u['theme_id']}: {u['reason']}"
                          for u in unlinked[:40])
        raise Refused(
            f"{len(unlinked)} theme-months could not be linked exactly (of "
            f"{len(unlinked) + len(links)}); nothing was written.\n{lines}\n"
            "The usual cause is a month-end processed from different stored text than is "
            "there now (documents downloaded later, or a changed universe). The matching "
            "is never approximated. --allow-partial writes the exact links only and lists "
            "the rest in export_report.json; the sample is then drawn from those only, "
            "which the result must say.")
    work.mkdir(parents=True, exist_ok=True)
    out.to_parquet(work / "export.parquet", index=False)
    (work / "export_report.json").write_text(json.dumps(report, indent=1))
    log(f"wrote {work / 'export.parquet'}: {len(out)} rows, {report['passages']} passages, "
        f"{report['themes']} themes; by theme tone {report['rows_by_tone']}")
    return report


# -- 2. sample and reader sheets -----------------------------------------------------

def _export(work: Path) -> pd.DataFrame:
    path = Path(work) / "export.parquet"
    if not path.exists():
        raise Refused(f"{path} is missing: run `export` first")
    return pd.read_parquet(path)


def _passages(work: Path) -> pd.DataFrame:
    """The sampled passages with what the readers and the model are shown."""
    s = _csv(Path(work) / "sample.csv")
    e = _export(work).drop_duplicates("passage_id").set_index("passage_id")
    gone = sorted(set(s["passage_id"]) - set(e.index))
    if gone:
        raise Refused(f"{len(gone)} sampled passages are not in export.parquet (e.g. "
                      f"{gone[0]}): the export was replaced after the sample was drawn")
    s["text"] = e["text"].reindex(s["passage_id"]).to_numpy()
    s["company"] = e["company"].reindex(s["passage_id"]).to_numpy()
    s["date"] = pd.to_datetime(e["available_at"].reindex(s["passage_id"]), utc=True) \
        .dt.strftime("%Y-%m-%d").to_numpy()
    return s


def sheet_paths(work: Path) -> dict[str, Path]:
    return {r: Path(work) / f"labels_reader_{r}.csv" for r in READERS}


def sample(work: Path = WORK, *, doc: Path = DOC, seed: int = P.SAMPLE_SEED, log=print,
           **draw) -> pd.DataFrame:
    """Draw the sample once (`impact_pilot.draw_sample`, protocol seed) and write
    `sample.csv` (IDs, stratum, tags; no text), the two blind reader sheets and
    `READER_GUIDE.md`. A shortfall raises `SampleShortfall` and writes nothing.
    `draw` passes smaller quotas to `draw_sample` in tests; the script never does."""
    work = Path(work)
    if (work / "sample.csv").exists():
        raise Refused(f"{work / 'sample.csv'} exists: the sample is drawn once and is "
                      "not replaced")
    e = _export(work)
    if e.empty:
        raise Refused("export.parquet has no rows: nothing to sample")
    tones = {t: tone for t, tone in zip(e["theme_id"], e["theme_tone"], strict=True) if tone}
    s = P.draw_sample(e, tones, seed=seed, as_of=e["latest_month_end"].iloc[0], **draw)
    guide = reader_guide(doc)
    s.to_csv(work / "sample.csv", index=False)
    p = _passages(work)
    for r, path in sheet_paths(work).items():
        order = sorted(range(len(p)), key=lambda i, r=r: P._h(seed, "reader", r,
                                                               p["passage_id"].iat[i]))
        sheet = p.iloc[order].reset_index(drop=True).assign(**dict.fromkeys(LABELS, ""))
        sheet[list(SHEET_COLUMNS)].to_csv(path, index=False)
    (work / "READER_GUIDE.md").write_text(guide)
    (work / "sample_meta.json").write_text(json.dumps({
        "protocol_version": P.PROTOCOL_VERSION, "seed": seed, "n": len(s),
        "export_sha256": _sha(work / "export.parquet"),
        "sample_sha256": _sha(work / "sample.csv")}, indent=1))
    log(f"wrote {work / 'sample.csv'}: {len(s)} passages; by stratum "
        f"{s['stratum'].value_counts().to_dict()}; hard cases "
        f"{int((s['selected_as'] == 'hard_case').sum())}")
    log("wrote " + ", ".join(x.name for x in sheet_paths(work).values())
        + " and READER_GUIDE.md")
    return s


# -- 3. the model run ----------------------------------------------------------------

def load_answers(path: Path) -> dict[str, dict]:
    """Cached answers by cache key (the last line for a key wins)."""
    path = Path(path)
    if not path.exists():
        return {}
    return {r["key"]: r for r in map(json.loads, filter(None, path.read_text().splitlines()))}


def request(prompt: str, model: str = MODEL) -> dict:
    """The arguments of one call, as monitor/naming.py makes it, without a fallback
    model: structured output, low effort, the whole prompt in one user message."""
    return {"model": model, "max_tokens": MAX_TOKENS,
            "output_config": {"effort": "low",
                              "format": {"type": "json_schema", "schema": P.SCHEMA}},
            "messages": [{"role": "user", "content": prompt}]}


def _record(resp, text: str, model: str) -> dict:
    """What is stored for one response; `invalid_reason` when it cannot be an answer."""
    usage = getattr(resp, "usage", None)
    rec = {"model_served": getattr(resp, "model", None),
           "stop_reason": getattr(resp, "stop_reason", None),
           "input_tokens": int(getattr(usage, "input_tokens", 0) or 0),
           "output_tokens": int(getattr(usage, "output_tokens", 0) or 0),
           "raw": None, "answer": None, "problems": [], "invalid_reason": None}
    served = str(rec["model_served"] or "")
    if rec["stop_reason"] in ("refusal", "max_tokens"):
        rec["invalid_reason"] = f"stopped: {rec['stop_reason']}"
        return rec
    rec["raw"] = next((b.text for b in resp.content if b.type == "text"), None)
    if served != model and not served.startswith(model + "-"):
        rec["invalid_reason"] = f"served by another model: {served or 'unknown'}"
    elif rec["raw"] is None:
        rec["invalid_reason"] = "no text in the response"
    else:
        try:
            rec["answer"] = json.loads(rec["raw"])
            rec["problems"] = P.validate_answer(rec["answer"], text)
        except ValueError:
            rec["invalid_reason"] = "the response is not JSON"
    return rec


def run(work: Path, answers: Path, *, create=None, model: str = MODEL,
        limit: int | None = None, confirm_cost: bool = False, doc: Path = DOC,
        now=None, log=print) -> dict:
    """Ask the model once per sampled passage and append each answer to `answers`.

    `create` is the SDK's `client.messages.create` (tests pass a fake); by default
    it is made from `anthropic.Anthropic()`. One model and no fallback: an answer
    served by another model, a refusal or a `max_tokens` stop is stored as invalid
    with the reason, and is not asked again. A failed call (network, rate limit)
    stores nothing and is asked again on the next run. The passage is untrusted
    text: it is sent only inside the <passage> tags of `impact_pilot.PROMPT`, in
    the user message, and a passage containing `</passage>` is not sent at all.
    """
    work, answers = Path(work), Path(answers)
    missing = [p.name for p in (work / "sample.csv", *sheet_paths(work).values())
               if not p.exists()]
    if missing:
        raise Refused(f"missing in {work}: {', '.join(missing)}. Run `sample` first: the "
                      "sample and the reader sheets are fixed before any model answer")
    p = _passages(work)
    cache = load_answers(answers)
    todo, seen = [], set(cache)
    for r in p.to_dict("records"):      # one call per distinct text (the cache key)
        key = P.cache_key(r["text"], model)
        if key not in seen:
            seen.add(key)
            todo.append(r)
    n = len(todo) if limit is None else min(limit, len(todo))
    log(f"{len(p)} sampled passages; {len(todo)} still to ask (estimated calls: {len(todo)})"
        + (f"; this run asks at most {n}" if limit is not None else "")
        + f". Model {model}, no fallback. Answers: {answers}")
    log("\nFrom the protocol (the pilot row is the one that applies):\n")
    log(protocol_section(9, doc))
    if not confirm_cost:
        raise Refused("nothing was sent. This step calls a paid API: run it again with "
                      "--confirm-cost to accept the cost above (re-check the prices first)")
    out = {"asked": 0, "invalid": 0, "errors": 0, "not_sent": 0, "input_tokens": 0,
           "output_tokens": 0}
    if create is None and n:
        import anthropic
        create = anthropic.Anthropic().messages.create
    answers.parent.mkdir(parents=True, exist_ok=True)
    streak = 0
    with open(answers, "a") as f:
        for r in todo:
            base = {"key": P.cache_key(r["text"], model), "passage_id": r["passage_id"],
                    "model_requested": model, "prompt_version": P.PROMPT_VERSION,
                    "schema_version": P.SCHEMA_VERSION,
                    "protocol_version": P.PROTOCOL_VERSION}
            if "</passage>" in r["text"]:
                rec = {"model_served": None, "stop_reason": None, "input_tokens": 0,
                       "output_tokens": 0, "raw": None, "answer": None, "problems": [],
                       "invalid_reason": "passage contains </passage>: not sent"}
                out["not_sent"] += 1
            elif out["asked"] + out["errors"] >= n:
                continue
            else:
                prompt = P.PROMPT.format(company=r["company"], role=r["role"],
                                         date=r["date"], passage=r["text"])
                try:
                    rec = _record(create(**request(prompt, model)), r["text"], model)
                except Exception as e:  # noqa: BLE001 - reported; asked again next run
                    out["errors"] += 1
                    streak += 1
                    log(f"  {r['passage_id']}: call failed ({type(e).__name__}: {e})")
                    if streak >= MAX_CONSECUTIVE_ERRORS:
                        log(f"  stopping after {streak} failed calls in a row")
                        break
                    continue
                streak = 0
                out["asked"] += 1
                out["input_tokens"] += rec["input_tokens"]
                out["output_tokens"] += rec["output_tokens"]
            out["invalid"] += bool(rec["invalid_reason"] or rec["problems"])
            at = now or dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
            f.write(json.dumps({**base, "at": at, **rec}) + "\n")
            f.flush()
            if out["asked"] and out["asked"] % 20 == 0 and rec["stop_reason"] is not None:
                log(f"  {out['asked']} asked: {out['input_tokens']:,} input and "
                    f"{out['output_tokens']:,} output tokens so far")
    cache = load_answers(answers)
    keys = {P.cache_key(t, model) for t in p["text"]}
    done = [cache[k] for k in keys if k in cache]
    tin, tout = (sum(x[k] for x in done) for k in ("input_tokens", "output_tokens"))
    pin, pout = prices(doc)
    out.update(answered=len(done), remaining=len(keys) - len(done),
               total_input_tokens=tin, total_output_tokens=tout,
               cost=dollars(out["input_tokens"], out["output_tokens"], doc),
               total_cost=dollars(tin, tout, doc))
    log(f"this run: {out['asked']} calls, {out['errors']} failed, {out['not_sent']} not "
        f"sent, {out['invalid']} stored as invalid; {out['input_tokens']:,} input and "
        f"{out['output_tokens']:,} output tokens = ${out['cost']:.2f}")
    log(f"all answers so far: {len(done)} of {len(keys)}; {tin:,} input and {tout:,} "
        f"output tokens = ${out['total_cost']:.2f} at the protocol's prices (${pin:g} and "
        f"${pout:g} per million tokens)")
    return out


# -- 4. scoring ----------------------------------------------------------------------

def read_labels(work: Path) -> tuple[pd.DataFrame, list[str]]:
    """Both readers' labels side by side (columns `<field>_a`, `<field>_b`), in
    sample order, and every problem that makes a sheet unusable."""
    s = _csv(Path(work) / "sample.csv")
    out, problems = s[["passage_id"]].copy(), []
    for r, path in sheet_paths(work).items():
        if not path.exists():
            problems.append(f"{path.name} is missing")
            continue
        d = _csv(path)
        gone = [c for c in ("passage_id", *LABELS) if c not in d.columns]
        if gone:
            problems.append(f"{path.name}: missing columns {', '.join(gone)}")
            continue
        if sorted(d["passage_id"]) != sorted(s["passage_id"]):
            problems.append(f"{path.name}: its passages are not exactly the sample's")
            continue
        d = d.set_index("passage_id").reindex(s["passage_id"])
        for c, allowed in LABELS.items():
            v = d[c].str.strip().str.lower()
            empty, bad = int((v == "").sum()), sorted(set(v) - set(allowed) - {""})
            if empty:
                problems.append(f"{path.name}: {empty} rows have no {c}")
            if bad:
                problems.append(f"{path.name}: {c} has values that are not allowed: "
                                f"{', '.join(bad[:5])} (allowed: {', '.join(allowed)})")
            out[f"{c}_{r}"] = v.to_numpy()
    return out, problems


def _adjudicated(work: Path, lab: pd.DataFrame, differ: pd.Series) -> pd.DataFrame:
    """Final labels: the readers' where they agree, `adjudicated.csv` where not."""
    final = pd.DataFrame({"passage_id": lab["passage_id"], "reason": "",
                          **{c: lab[f"{c}_a"] for c in LABELS}})
    if not differ.any():
        return final
    a = _csv(Path(work) / "adjudicated.csv")
    need = ["passage_id", *(f"adjudicated_{c}" for c in LABELS), "reason"]
    gone = [c for c in need if c not in a.columns]
    if gone or a["passage_id"].duplicated().any():
        raise Refused("adjudicated.csv: " + (f"missing columns {', '.join(gone)}" if gone
                                             else "a passage appears twice"))
    a = a.set_index("passage_id")
    problems = []
    for i in lab.index[differ]:
        pid = lab.at[i, "passage_id"]
        if pid not in a.index:
            problems.append(f"{pid}: not in the file")
            continue
        if not a.at[pid, "reason"].strip():
            problems.append(f"{pid}: no reason given")
        for c, allowed in LABELS.items():
            v = a.at[pid, f"adjudicated_{c}"].strip().lower()
            ra, rb = lab.at[i, f"{c}_a"], lab.at[i, f"{c}_b"]
            if v not in allowed:
                problems.append(f"{pid}: {c} is empty or not an allowed value")
            elif ra == rb and v != ra:
                problems.append(f"{pid}: {c} differs from the label both readers gave")
            final.at[i, c] = v
        final.at[i, "reason"] = a.at[pid, "reason"].strip()
    if problems:
        raise Refused("adjudicated.csv is not complete:\n  " + "\n  ".join(problems[:30]))
    return final


def _f(x, digits: int = 3) -> str:
    return "n/a" if x is None or (isinstance(x, float) and x != x) else f"{x:.{digits}f}"


def _table(head: list[str], rows: list[list]) -> list[str]:
    return ["| " + " | ".join(head) + " |", "|" + "---|" * len(head),
            *("| " + " | ".join(str(c) for c in r) + " |" for r in rows), ""]


def _by(d: pd.DataFrame, groups: dict) -> list[list]:
    rows = []
    for name, mask in groups.items():
        g = d.loc[mask & d["adjudicated"].isin(P.SCORED)]
        f1 = [_f(P.class_metrics(g["adjudicated"], g[m])["macro_f1"]) if len(g) else "n/a"
              for m in ("baseline", "extraction")]
        rows.append([name, int(mask.sum()), len(g), *f1])
    return rows


def result_markdown(res: dict, d: pd.DataFrame, kappas: dict, extra: dict) -> str:
    """`result.md`: every declared criterion with its threshold, the measured value
    and PASS or FAIL; the hard cases separately; the verdict."""
    head = ["# Economic-impact extraction pilot: result", "",
            f"Protocol version {res['protocol_version']} "
            "(docs/experiments/ECONOMIC_IMPACT_PILOT.md). **The sample, labels, metrics and "
            "thresholds below were fixed in the protocol before any result existed; none was "
            "changed after results were seen.** No price or return was used.", ""]
    agreement = _table(["Field", "Kappa, all passages", "Kappa, hard cases"],
                       [[k, _f(v["all"]), _f(v["hard"])] for k, v in kappas.items()])
    if res["verdict"] == "labels_not_good_enough":
        return "\n".join([
            *head, "## Verdict: none", "",
            f"Reader agreement (Cohen's kappa on the effect on the speaker, {res['n']} "
            f"passages) is {_f(res['kappa'])}, below the floor of {P.KAPPA_FLOOR:.2f}: "
            "criterion i FAILS. By the protocol the labels cannot judge anything: no "
            "comparison is reported, and a new protocol version must fix the guidelines.",
            "", *agreement])
    c, diff, ext, hard = res["criteria"], res["difference"], res["extraction"], res["hard_case"]

    def mark(ok) -> str:
        return "PASS" if ok else "FAIL"
    out = [*head,
           f"Passages: {res['n']}; scored (adjudicated benefits, harmed or neutral): "
           f"{res['n_scored']}; adjudicated unclear: "
           f"{_f(100 * res['adjudicated_unclear_share'], 1)}%. Model: {extra['model']}.", ""]
    if extra.get("partial_export"):
        out += [f"**Limitation.** {extra['partial_export']} theme-months of the registry "
                "could not be linked exactly by the export (export_report.json); the "
                "sample was drawn from the exactly linked ones only.", ""]
    out += [f"## Verdict: {'PASS' if res['passed'] else 'FAIL'}", "",
            "Extraction passes only if every criterion holds." if res["passed"] else
            "At least one criterion failed, so the phrase lists stay (protocol, section 7).",
            "", "## Acceptance criteria", ""]
    out += _table(["", "Criterion", "Threshold", "Measured", "Result"], [
        ["i", "Reader kappa, effect on the speaker", f">= {P.KAPPA_FLOOR:.2f}",
         _f(res["kappa"]), mark(c["i_reader_agreement"])],
        ["ii", "Macro-F1 gain over the phrase lists",
         f">= {P.MIN_F1_GAIN:.2f} and 95% interval above zero",
         f"{_f(diff['difference'])} (interval {_f(diff['ci95'][0])} to {_f(diff['ci95'][1])}; "
         f"{diff['n_boot']} resamples of {diff['n_companies']} companies)",
         mark(c["ii_macro_f1_gain"])],
        ["iii", "Precision on harmed", f">= {P.MIN_PRECISION:.2f}",
         _f(ext["classes"]["harmed"]["precision"]), mark(c["iii_precision_harmed"])],
        ["iii", "Precision on benefits", f">= {P.MIN_PRECISION:.2f}",
         _f(ext["classes"]["benefits"]["precision"]), mark(c["iii_precision_benefits"])],
        ["iv", "Hard cases: macro-F1 not below the lists'", ">= the lists' macro-F1",
         f"{_f(hard['extraction_macro_f1'])} against {_f(hard['baseline_macro_f1'])} "
         f"({hard['n_scored']} scored of {hard['n']})" if hard else "n/a",
         mark(c["iv_hard_cases_not_worse"])],
        ["v", "Answers failing validation", f"<= {100 * P.MAX_INVALID_SHARE:.0f}%",
         f"{_f(100 * res['extraction_invalid_share'], 1)}%", mark(c["v_spans_verbatim"])],
        ["vi", "Extraction unclear (including insufficient)",
         f"<= {100 * P.MAX_UNCLEAR_SHARE:.0f}%",
         f"{_f(100 * res['extraction_unclear_share'], 1)}%", mark(c["vi_unclear_share"])]])
    out += ["## Hard cases (the quota passages), separately", "",
            "Point estimates on small groups; the protocol's power note applies.", ""]
    hard_mask = d["hard_case"]
    out += _table(["Group", "Passages", "Scored", "Lists macro-F1", "Extraction macro-F1"],
                  _by(d, {"all hard cases": hard_mask,
                          **{t: hard_mask & (d["quota_tag"] == t) for t in P.HARD_ORDER}}))
    out += ["## Also reported (not criteria)", ""]
    rows = []
    for name, m in (("Phrase lists", res["baseline"]), ("Extraction", ext)):
        for k in P.SCORED:
            x = m["classes"][k]
            rows.append([name, k, _f(x["precision"]), _f(x["recall"]), _f(x["f1"])])
        rows.append([name, "macro-F1 / accuracy", "", "",
                     f"{_f(m['macro_f1'])} / {_f(m['accuracy'])}"])
    out += _table(["Method", "Class", "Precision", "Recall", "F1"], rows)
    out += [f"- Four-label macro-F1 (all {res['n']} passages, unclear included): lists "
            f"{_f(extra['four_label']['baseline'])}, extraction "
            f"{_f(extra['four_label']['extraction'])}.",
            f"- Phrase lists with analyst questions given their raw phrase score "
            f"(sensitivity check): macro-F1 {_f(extra['baseline_raw_analyst'])}.",
            f"- Opposite party (extraction only): precision "
            f"{_f(extra['opposite']['precision'])}, recall {_f(extra['opposite']['recall'])} "
            f"({extra['opposite']['n_yes']} adjudicated yes).",
            f"- Statement type (extraction only): accuracy {_f(extra['statement_accuracy'])}.",
            f"- Invalid answers by reason: {extra['invalid_reasons'] or 'none'}.", ""]
    out += agreement
    out += _table(["Group", "Passages", "Scored", "Lists macro-F1", "Extraction macro-F1"],
                  _by(d, {**{f"stratum {s}": d["stratum"] == s for s in P.STRATA},
                          **{f"role {r}": d["role"] == r for r in sorted(set(d["role"]))},
                          **{f"tag {t}": d["hard_case_tags"].str.split(";").map(
                              lambda v, t=t: t in v) for t in P.HARD_ORDER}}))
    return "\n".join(out)


def _kappa(a, b) -> float | None:
    return P.cohen_kappa(a, b) if len(a) else None


def score(work: Path, answers: Path, *, model: str = MODEL, n_boot: int = P.N_BOOT,
          log=print) -> dict:
    """Agreement, adjudication, both methods' labels and the declared criteria.

    Returns {"status": ...}: "needs_adjudication" (wrote `disagreements.csv`),
    "labels_not_good_enough" (kappa below the floor: `result.md` gives no verdict),
    or "scored" with the result of `impact_pilot.score`. Raises `Refused` when a
    reader sheet is not fully labelled or a model answer is missing.
    """
    work, answers = Path(work), Path(answers)
    if not (work / "sample.csv").exists():
        raise Refused(f"{work / 'sample.csv'} is missing: run `sample` first")
    lab, problems = read_labels(work)
    if problems:
        raise Refused("the reader sheets are not fully labelled:\n  " + "\n  ".join(problems))
    p = _passages(work)
    hard = (p["selected_as"] == "hard_case").to_numpy()
    kappas = {c: {"all": _kappa(lab[f"{c}_a"], lab[f"{c}_b"]),
                  "hard": _kappa(lab.loc[hard, f"{c}_a"], lab.loc[hard, f"{c}_b"])}
              for c in LABELS}
    kappa = kappas["speaker_effect"]["all"]
    log(f"reader agreement (kappa, effect on the speaker, {len(lab)} passages): {kappa:.3f} "
        f"(floor {P.KAPPA_FLOOR:.2f})")
    if kappa < P.KAPPA_FLOOR:
        res = {"protocol_version": P.PROTOCOL_VERSION, "n": len(lab), "kappa": kappa,
               "verdict": "labels_not_good_enough", "passed": False}
        (work / "result.md").write_text(result_markdown(res, p, kappas, {}))
        log(f"below the floor: no verdict. Wrote {work / 'result.md'}")
        return {"status": "labels_not_good_enough", **res}

    differ = pd.Series(False, index=lab.index)
    for c in LABELS:
        differ |= lab[f"{c}_a"] != lab[f"{c}_b"]
    if differ.any() and not (work / "adjudicated.csv").exists():
        d = p.loc[differ.to_numpy(), ["passage_id", "company", "role", "date", "text"]].copy()
        for c in LABELS:
            a, b = lab.loc[differ, f"{c}_a"].to_numpy(), lab.loc[differ, f"{c}_b"].to_numpy()
            d[f"{c}_a"], d[f"{c}_b"] = a, b
            d[f"adjudicated_{c}"] = [x if x == y else "" for x, y in zip(a, b, strict=True)]
        d["reason"] = ""
        d.to_csv(work / "disagreements.csv", index=False)
        log(f"the readers differ on {len(d)} passages. Wrote {work / 'disagreements.csv'}")
        return {"status": "needs_adjudication", "kappa": kappa, "disagreements": len(d)}
    final = _adjudicated(work, lab, differ)

    cache = load_answers(answers)
    recs = [cache.get(P.cache_key(t, model)) for t in p["text"]]
    absent = [pid for pid, r in zip(p["passage_id"], recs, strict=True) if r is None]
    if absent:
        raise Refused(f"{len(absent)} sampled passages have no model answer in {answers} "
                      f"(e.g. {absent[0]}): finish `run` first")
    preds, reasons = [], []
    for text, role, rec in zip(p["text"], p["role"], recs, strict=True):
        bad = {"speaker_effect": P.INVALID, "opposite_party": None,
               "statement_type": P.INVALID, "problems": [rec["invalid_reason"]]}
        pr = bad if rec["invalid_reason"] else P.prediction(rec["answer"], text)
        reasons += pr["problems"][:1]
        preds.append({"baseline": P.baseline_label(text, role),
                      "baseline_raw_analyst": P.baseline_label(
                          text, role, score_analyst_questions=True),
                      "extraction": pr["speaker_effect"],
                      "extraction_opposite_party": {True: "yes", False: "no"}.get(
                          pr["opposite_party"], P.INVALID),
                      "extraction_statement_type": pr["statement_type"]})
    d = pd.concat([p.drop(columns=["text"]).reset_index(drop=True), pd.DataFrame(preds)],
                  axis=1)
    d["reader_a"], d["reader_b"] = lab["speaker_effect_a"], lab["speaker_effect_b"]
    d["adjudicated"], d["hard_case"] = final["speaker_effect"].to_numpy(), hard
    res = P.score(d[["passage_id", "symbol", "reader_a", "reader_b", "adjudicated",
                     "hard_case"]], d[["passage_id", "baseline", "extraction"]],
                  n_boot=n_boot)
    yes = (final["opposite_party"] == "yes").to_numpy()
    said = (d["extraction_opposite_party"] == "yes").to_numpy()
    scored = d["adjudicated"].isin(P.SCORED)
    report = _export_report(work)
    extra = {
        "model": model, "partial_export": len(report.get("unlinked", [])),
        "four_label": {m: P.class_metrics(d["adjudicated"], d[m], P.EFFECTS)["macro_f1"]
                       for m in ("baseline", "extraction")},
        "baseline_raw_analyst": P.class_metrics(
            d.loc[scored, "adjudicated"], d.loc[scored, "baseline_raw_analyst"])["macro_f1"],
        "opposite": {"precision": float((yes & said).sum() / said.sum()) if said.any()
                     else None,
                     "recall": float((yes & said).sum() / yes.sum()) if yes.any() else None,
                     "n_yes": int(yes.sum())},
        "statement_accuracy": float((d["extraction_statement_type"].to_numpy()
                                     == final["statement_type"].to_numpy()).mean()),
        "invalid_reasons": "; ".join(f"{k} ({v})" for k, v in
                                     pd.Series(reasons, dtype=object).value_counts().items())}
    (work / "result.md").write_text(result_markdown(res, d, kappas, extra))
    # For the repository (protocol section 8): no passage text in either file.
    out = lab.merge(final.rename(columns={c: f"{c}_adjudicated" for c in LABELS}),
                    on="passage_id")
    out.to_csv(work / "labels_final.csv", index=False)
    d[["passage_id", "baseline", "extraction", "extraction_opposite_party",
       "extraction_statement_type"]].to_csv(work / "predictions.csv", index=False)
    log(f"verdict: {res['verdict'].upper()}. Wrote {work / 'result.md'}, labels_final.csv "
        "and predictions.csv")
    return {"status": "scored", **res, "extra": extra}


def _export_report(work: Path) -> dict:
    path = Path(work) / "export_report.json"
    return json.loads(path.read_text()) if path.exists() else {}


# -- 5. status -----------------------------------------------------------------------

def status(work: Path, answers: Path, model: str = MODEL) -> list[tuple[str, bool, str]]:
    """(step, done, detail) for each step, in order. Reads files only."""
    work, answers = Path(work), Path(answers)
    out = []
    rep = _export_report(work)
    done = (work / "export.parquet").exists()
    out.append(("export", done, f"{rep.get('rows', '?')} rows, "
                f"{len(rep.get('unlinked', []))} theme-months not linked" if done
                else "run `export`"))
    drawn = (work / "sample.csv").exists()
    sheets = drawn and all(x.exists() for x in sheet_paths(work).values())
    out.append(("sample and reader sheets", bool(sheets),
                f"{len(_csv(work / 'sample.csv'))} passages" if sheets else "run `sample`"))
    if not sheets:
        return [*out, ("reader labels", False, "waiting for the sample"),
                ("model answers", False, "waiting for the sample"),
                ("adjudication", False, "waiting for the labels"),
                ("result", False, "waiting")]
    lab, problems = read_labels(work)
    out.append(("reader labels", not problems, "both sheets fully labelled" if not problems
                else "; ".join(problems[:4])))
    cache, p = load_answers(answers), None
    if done:
        p = _passages(work)
        got = sum(P.cache_key(t, model) in cache for t in p["text"])
        out.append(("model answers", got == len(p), f"{got} of {len(p)} in {answers}"))
    else:
        out.append(("model answers", False, "export.parquet is missing"))
    if problems:
        out.append(("adjudication", False, "waiting for the labels"))
    else:
        n = int(sum(any(lab.at[i, f"{c}_a"] != lab.at[i, f"{c}_b"] for c in LABELS)
                    for i in lab.index))
        have = (work / "adjudicated.csv").exists()
        out.append(("adjudication", have or not n,
                    "no disagreement" if not n else f"{n} disagreements; adjudicated.csv "
                    + ("present" if have else "missing (run `score` for disagreements.csv)")))
    res = (work / "result.md").exists()
    out.append(("result", res, str(work / "result.md") if res else "run `score`"))
    return out
