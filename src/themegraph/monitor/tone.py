"""Headwind or tailwind: what kind of news a theme's discussion is (docs/MONITOR.md,
"Headwinds").

A theme about weaker demand, inventory corrections or pricing pressure is a
different kind of idea from one about a product ramp, even if both are growing.
Each passage is scored with two short phrase lists of business language (below):
negative when it has more headwind phrases than tailwind phrases, positive when
the reverse. A theme's headwind share is the share of its company passages
(management and filings; analyst questions are attention, not company statements)
that are negative, compared with what its mix of genres would give at the window's
own rates. Filings' risk factors are full of negative words, so a theme made of
filings is compared with filings, not with call transcripts.

Direction, per month-end (thresholds in config `tone`, declared 2026-10-29 from
the text alone, never from returns):
- headwind: at least `min_share` of its company passages negative, at least
  `min_ratio` times the genre-matched rate, and more negative than positive;
- tailwind: the same with positive passages;
- mixed otherwise, or unknown with fewer than `min_passages` company passages.

Limits: phrase lists do not understand negation ("no sign of weakness" counts as
negative) or context; the matched phrases are shown with the theme so a reader
can check. This describes what companies say, not where prices go.
"""

from __future__ import annotations

import re
from collections import Counter

import numpy as np

# Phrases a company uses when describing a problem in its business. Written for
# this monitor (not a licensed dictionary). Ambiguous words are left out:
# "uncertain(ty)" (every safe-harbour statement), "shortage" (often good for
# pricing), "tariff" and "inventory" alone (neutral without a direction).
HEADWIND = [
    r"headwinds?", r"weak(?:er|ness|ening)?\s+demand", r"demand\s+(?:weakness|softness|"
    r"slowdown|decline)", r"soft(?:er|ness)", r"slow(?:er|down|ing)", r"declin(?:e|ed|es|ing)",
    r"lower\s+demand", r"reduced\s+demand", r"inventory\s+(?:correction|digestion|"
    r"adjustment|build|overhang)", r"excess\s+inventor(?:y|ies)", r"de-?stocking",
    r"digestion", r"pricing\s+pressure", r"price\s+(?:erosion|pressure|declines?)",
    r"margin\s+(?:pressure|compression|erosion)", r"cancell?ations?", r"push-?outs?",
    r"delay(?:s|ed)?", r"deferr?(?:al|als|ed)", r"lower(?:ed)?\s+(?:guidance|outlook)",
    r"guid(?:e|ed|ing)\s+down", r"impairments?", r"write-?downs?", r"restructuring",
    r"lay-?offs?", r"headcount\s+reductions?", r"share\s+loss", r"los(?:e|ing|t)\s+share",
    r"competitive\s+pressure", r"export\s+(?:controls?|restrictions?)", r"downturn",
    r"recession", r"contraction", r"oversupply", r"under-?utili[sz]ation", r"churn",
    r"budget\s+(?:cuts|scrutiny|constraints?)", r"elongated\s+(?:sales\s+)?cycles?",
    r"deal\s+slippage", r"challenging", r"weakness", r"shortfall", r"miss(?:ed)?\s+"
    r"(?:expectations|guidance)",
]
TAILWIND = [
    r"tailwinds?", r"strong(?:er)?\s+demand", r"robust\s+demand", r"record\s+(?:revenue|"
    r"quarter|year|bookings|backlog|demand)", r"accelerat(?:e|ed|es|ing|ion)",
    r"ramp(?:s|ed|ing)?", r"rais(?:e|ed|ing)\s+(?:guidance|outlook)", r"share\s+gains?",
    r"gain(?:ed|ing)?\s+share", r"backlog\s+(?:growth|grew|increased)", r"sold\s+out",
    r"momentum", r"upside", r"outperform(?:ed|ing)?", r"expan(?:d|ded|ding|sion)",
    r"exceed(?:ed)?\s+(?:expectations|guidance)", r"beat\s+(?:expectations|guidance)",
    r"strong\s+growth", r"growth\s+accelerat\w*",
]


def _rx(words: list[str]) -> re.Pattern:
    return re.compile(r"\b(?:" + "|".join(words) + r")\b", re.IGNORECASE)


NEG, POS = _rx(HEADWIND), _rx(TAILWIND)


def score(texts) -> tuple[np.ndarray, np.ndarray]:
    """Per passage: -1 negative, 1 positive, 0 neither; and the headwind hit count."""
    out = np.zeros(len(texts), dtype=np.int8)
    hits = np.zeros(len(texts), dtype=np.int32)
    for i, t in enumerate(texts):
        n = len(NEG.findall(t))
        p = len(POS.findall(t))
        hits[i] = n
        out[i] = -1 if n > p else 1 if p > n else 0
    return out, hits


def terms(texts, k: int = 5) -> list[str]:
    """The most common headwind phrases in these passages, by passages using them."""
    c = Counter()
    for t in texts:
        c.update(list(dict.fromkeys(" ".join(m.lower().split()) for m in NEG.findall(t))))
    return [w for w, _ in c.most_common(k)]


def theme_tone(sign: np.ndarray, genres: np.ndarray, rates: dict[str, tuple[float, float]],
               cfg, texts=None) -> dict:
    """Tone of one theme's company passages. `sign` and `genres` are per passage;
    `rates` gives each genre's (negative, positive) share in the window."""
    n = len(sign)
    if n < cfg.min_passages:
        return {"direction": "unknown", "passages": int(n)}
    neg, pos = float(np.mean(sign < 0)), float(np.mean(sign > 0))
    gs, cnt = np.unique(genres, return_counts=True)
    exp_neg = float(sum(c * rates.get(g, (0.0, 0.0))[0] for g, c in zip(gs, cnt,
                                                                          strict=True)) / n)
    exp_pos = float(sum(c * rates.get(g, (0.0, 0.0))[1] for g, c in zip(gs, cnt,
                                                                          strict=True)) / n)
    r_neg = neg / exp_neg if exp_neg else None
    r_pos = pos / exp_pos if exp_pos else None
    direction = "mixed"
    if neg >= cfg.min_share and r_neg is not None and r_neg >= cfg.min_ratio and neg > pos:
        direction = "headwind"
    elif pos >= cfg.min_share and r_pos is not None and r_pos >= cfg.min_ratio and pos > neg:
        direction = "tailwind"
    out = {"direction": direction, "passages": int(n), "negative_share": round(neg, 4),
           "positive_share": round(pos, 4), "expected_negative": round(exp_neg, 4),
           "expected_positive": round(exp_pos, 4),
           "negative_ratio": None if r_neg is None else round(r_neg, 3),
           "positive_ratio": None if r_pos is None else round(r_pos, 3)}
    if texts is not None:
        out["headwind_terms"] = terms([t for t, s in zip(texts, sign, strict=True) if s < 0])
    return out
