"""Embedding providers with a persistent cache keyed by (model, text hash).

`VoyageEmbedder` is the research embedder. `HashingEmbedder` is a deterministic,
dependency-free stand-in for tests and offline development only; runs record the
model id, and reports flag any run that did not use the configured research model.
"""

from __future__ import annotations

import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Protocol

import httpx
import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import HashingVectorizer
from tqdm.auto import tqdm

from ..config import Embedding
from ..data.http import env_value

ENDPOINTS = {
    "voyage": "https://api.voyageai.com/v1/embeddings",
    "mongodb": "https://ai.mongodb.com/v1/embeddings",
}


class Embedder(Protocol):
    model_id: str

    def embed(self, texts: list[str]) -> np.ndarray: ...


def _normalise(v: np.ndarray) -> np.ndarray:
    n = np.linalg.norm(v, axis=1, keepdims=True)
    n[n == 0] = 1.0
    return (v / n).astype(np.float32)


class HashingEmbedder:
    """Word uni/bi-gram hashing + sublinear tf, L2-normalised. Not semantic."""

    def __init__(self, dimensions: int = 1024):
        self.model_id = f"hashing-{dimensions}"
        self.v = HashingVectorizer(n_features=dimensions, ngram_range=(1, 2),
                                   alternate_sign=False, norm=None, stop_words="english")

    def embed(self, texts: list[str]) -> np.ndarray:
        X = self.v.transform(texts).toarray()
        return _normalise(np.log1p(X))


RETRYABLE_STATUS = (429, 500, 502, 503, 504)
MAX_ATTEMPTS = 8
MAX_BACKOFF = 120.0


class VoyageEmbedder:
    """Batches of `batch_size` texts, up to `concurrency` requests in flight. Rate
    limits (429), server errors and network timeouts are retried with exponential
    backoff (honouring Retry-After); other errors stop the run with the API's own
    message. Throughput is set by the account's rate limits: on Voyage's free tier
    (no payment method) it is a few requests per minute whatever this code does."""

    def __init__(self, model: str, batch_size: int = 64, api_key: str | None = None,
                 client: httpx.Client | None = None, endpoint: str = "voyage",
                 concurrency: int = 4, sleep=time.sleep):
        self.model_id = model
        self.endpoint = endpoint
        self.url = ENDPOINTS[endpoint]
        self.batch_size = batch_size
        self.concurrency = max(1, concurrency)
        self._sleep = sleep
        key = env_value("VOYAGE_API_KEY", api_key)
        if not key and client is None:
            raise RuntimeError("VOYAGE_API_KEY is not set (add it as an environment secret)")
        self.client = client or httpx.Client(
            headers={"Authorization": f"Bearer {key}"},
            timeout=httpx.Timeout(300.0, connect=30.0),
        )

    def _post(self, batch: list[str]) -> np.ndarray:
        last = "no attempt made"
        for attempt in range(MAX_ATTEMPTS):
            wait = min(MAX_BACKOFF, 2.0 ** (attempt + 1))
            try:
                r = self.client.post(self.url, json={
                    "input": batch, "model": self.model_id, "input_type": "document"})
            except (httpx.TimeoutException, httpx.TransportError) as e:
                last = f"{type(e).__name__} (network)"
                self._sleep(wait)
                continue
            if r.status_code == 200:
                data = sorted(r.json()["data"], key=lambda d: d["index"])
                return np.array([d["embedding"] for d in data], dtype=np.float32)
            if r.status_code in RETRYABLE_STATUS:
                last = f"HTTP {r.status_code}"
                retry_after = r.headers.get("retry-after")
                try:
                    wait = max(wait, float(retry_after)) if retry_after else wait
                except ValueError:
                    pass
                self._sleep(min(MAX_BACKOFF, wait))
                continue
            raise RuntimeError(_voyage_error(r, self.model_id, self.endpoint))
        raise RuntimeError(f"Voyage embeddings: {MAX_ATTEMPTS} attempts failed, last: {last}. "
                           "Already-embedded passages are cached; re-run to resume.")

    def embed(self, texts: list[str]) -> np.ndarray:
        batches = [texts[i:i + self.batch_size] for i in range(0, len(texts), self.batch_size)]
        if not batches:
            return np.zeros((0, 0), np.float32)
        if self.concurrency == 1 or len(batches) == 1:
            out = [self._post(b) for b in batches]
        else:
            with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
                out = list(pool.map(self._post, batches))  # map keeps input order
        return _normalise(np.vstack(out))


def _voyage_error(r: httpx.Response, model: str, endpoint: str = "voyage") -> str:
    """Voyage's own explanation (never contains the key), plus a hint for auth errors."""
    try:
        body = r.json()
        detail = body.get("detail") or body.get("message") or body.get("error") or body
    except ValueError:
        detail = r.text
    msg = (f"Voyage embeddings HTTP {r.status_code} (model {model}, endpoint {endpoint}): "
           f"{str(detail)[:400]}")
    if "cannot access this endpoint" in str(detail):
        other = "mongodb" if endpoint == "voyage" else "voyage"
        msg += (f"\nThis key belongs to the other service: set `endpoint: {other}` under "
                "embedding in config/pipeline.yaml.")
    elif r.status_code in (401, 403):
        msg += ("\nCheck that VOYAGE_API_KEY is current, that the account has a payment "
                "method, and that it can use this model (embedding.model in "
                "config/pipeline.yaml).")
    return msg


def make_embedder(cfg: Embedding) -> Embedder:
    if cfg.provider == "hashing":
        return HashingEmbedder(cfg.dimensions)
    return VoyageEmbedder(cfg.model, cfg.batch_size, endpoint=cfg.endpoint,
                          concurrency=cfg.concurrency)


class EmbeddingCache:
    """Parquet shards under <root>/<model_id>/; never recomputes a cached hash.

    New vectors are written every `save_every` passages, so an interrupted or failed
    run keeps (and does not pay again for) everything embedded before the failure.
    """

    save_every = 1024

    def __init__(self, root: Path, embedder: Embedder):
        self.embedder = embedder
        safe = re.sub(r"[^\w.-]", "_", embedder.model_id)
        self.dir = Path(root) / safe
        self.dir.mkdir(parents=True, exist_ok=True)
        self._mem: dict[str, np.ndarray] = {}
        for shard in sorted(self.dir.glob("*.parquet")):
            df = pd.read_parquet(shard)
            for h, v in zip(df["text_hash"], df["vector"], strict=True):
                self._mem[h] = np.asarray(v, dtype=np.float32)

    def get(self, hashes: list[str], texts: list[str]) -> np.ndarray:
        todo = {}
        for h, t in zip(hashes, texts, strict=True):
            if h not in self._mem and h not in todo:
                todo[h] = t
        if todo:
            items = list(todo.items())
            with tqdm(total=len(items), desc=f"Embedding ({self.embedder.model_id})",
                      unit="passage", disable=None, dynamic_ncols=True) as bar:
                for i in range(0, len(items), self.save_every):
                    chunk = items[i:i + self.save_every]
                    vecs = self.embedder.embed([t for _, t in chunk])
                    keys = [h for h, _ in chunk]
                    self._write_shard(keys, vecs)
                    for h, v in zip(keys, vecs, strict=True):
                        self._mem[h] = v
                    bar.update(len(chunk))
        return np.vstack([self._mem[h] for h in hashes]) if hashes else np.zeros((0, 0))

    def _write_shard(self, keys: list[str], vecs: np.ndarray) -> None:
        n = len(list(self.dir.glob("*.parquet")))
        tmp = self.dir / f"{n:06d}.parquet.tmp"
        pd.DataFrame({"text_hash": keys, "vector": list(vecs)}).to_parquet(tmp, index=False)
        tmp.rename(self.dir / f"{n:06d}.parquet")  # atomic: no half-written shards
