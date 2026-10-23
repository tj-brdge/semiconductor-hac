"""Split documents into passages: transcript speaker turns, filing sections, chunks."""

from __future__ import annotations

import hashlib
import re

import pandas as pd

SPEAKER = re.compile(r"^([A-Z][\w.'\- ]{1,60}):\s", re.M)
QA_START = re.compile(r"question[- ]and[- ]answer|questions and answers|\bQ&A\b|first question",
                      re.I)


def text_hash(text: str) -> str:
    return hashlib.sha256(" ".join(text.split()).lower().encode()).hexdigest()


def chunk_words(text: str, size: int, min_words: int) -> list[str]:
    words = text.split()
    if len(words) < min_words:
        return []
    chunks = [words[i:i + size] for i in range(0, len(words), size)]
    if len(chunks) > 1 and len(chunks[-1]) < min_words:
        tail = chunks.pop()
        chunks[-1] = chunks[-1] + tail
    return [" ".join(c) for c in chunks]


def transcript_turns(text: str) -> list[tuple[str, str, str]]:
    """(speaker, part, text) per speaker turn; part is 'prepared' or 'qa'."""
    marks = list(SPEAKER.finditer(text))
    if not marks:
        return [("unknown", "prepared", text)]
    turns, part = [], "prepared"
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        body = text[m.end():end].strip()
        if part == "prepared" and QA_START.search(body):
            part = "qa_next"
        turns.append((m.group(1).strip(), "qa" if part == "qa" else "prepared", body))
        if part == "qa_next":
            part = "qa"
    return turns


def passages_from_documents(docs: pd.DataFrame, size: int, min_words: int) -> pd.DataFrame:
    """docs columns: symbol, doc_id, doc_type, published_at, available_at, text."""
    recs = []
    for d in docs.itertuples():
        if d.doc_type == "transcript":
            units = [(f"{spk}|{part}", body) for spk, part, body in transcript_turns(d.text)]
            units = [(u, b) for u, b in units if "operator" not in u.lower()]
        else:
            units = [(d.doc_type, d.text)]
        n = 0
        for unit, body in units:
            for chunk in chunk_words(body, size, min_words):
                recs.append({
                    "passage_id": f"{d.doc_id}#{n}",
                    "doc_id": d.doc_id,
                    "symbol": d.symbol,
                    "doc_type": d.doc_type,
                    "unit": unit,
                    "published_at": d.published_at,
                    "available_at": d.available_at,
                    "text": chunk,
                    "text_hash": text_hash(chunk),
                })
                n += 1
    return pd.DataFrame(recs)


def drop_exact_duplicates(passages: pd.DataFrame) -> pd.DataFrame:
    """Keep only the first available occurrence of identical text per issuer."""
    p = passages.sort_values(["available_at", "passage_id"])
    return p.drop_duplicates(["symbol", "text_hash"], keep="first").reset_index(drop=True)
