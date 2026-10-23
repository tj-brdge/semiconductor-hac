"""Immutable, content-hashed cache of raw API responses.

A response is keyed by (source, endpoint, credential-free params). Once written,
a key is never overwritten: repeating a request returns the original vintage, and
writing different bytes under an existing key raises. Studying a new vintage
means using a new cache directory.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

SECRET_PARAMS = {"apikey", "api_key", "token", "key"}


class CacheConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class CachedResponse:
    key: str
    body: bytes
    fetched_at: dt.datetime
    sha256: str
    meta: dict


def cache_key(source: str, endpoint: str, params: dict | None = None) -> str:
    clean = {k: params[k] for k in sorted(params or {}) if k.lower() not in SECRET_PARAMS}
    raw = json.dumps([source, endpoint, clean], sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


class RawCache:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _paths(self, key: str) -> tuple[Path, Path]:
        d = self.root / key[:2]
        return d / f"{key}.body", d / f"{key}.json"

    def get(self, key: str) -> CachedResponse | None:
        body_path, meta_path = self._paths(key)
        if not body_path.exists():
            return None
        body = body_path.read_bytes()
        meta = json.loads(meta_path.read_text())
        digest = hashlib.sha256(body).hexdigest()
        if digest != meta["sha256"]:
            raise CacheConflict(f"cache entry {key} is corrupt: hash mismatch")
        return CachedResponse(
            key, body, dt.datetime.fromisoformat(meta["fetched_at"]), digest, meta
        )

    def put(self, key: str, body: bytes, meta: dict) -> CachedResponse:
        digest = hashlib.sha256(body).hexdigest()
        existing = self.get(key)
        if existing is not None:
            if existing.sha256 != digest:
                raise CacheConflict(f"refusing to overwrite cache entry {key} with new bytes")
            return existing
        body_path, meta_path = self._paths(key)
        body_path.parent.mkdir(parents=True, exist_ok=True)
        fetched_at = dt.datetime.now(dt.UTC)
        full = {**meta, "sha256": digest, "fetched_at": fetched_at.isoformat(), "bytes": len(body)}
        full["params"] = {
            k: v for k, v in (meta.get("params") or {}).items() if k.lower() not in SECRET_PARAMS
        }
        body_path.write_bytes(body)
        meta_path.write_text(json.dumps(full, sort_keys=True, default=str, indent=1))
        return CachedResponse(key, body, fetched_at, digest, full)

    def manifest(self) -> list[dict]:
        out = []
        for meta_path in sorted(self.root.glob("*/*.json")):
            meta = json.loads(meta_path.read_text())
            meta["key"] = meta_path.stem
            out.append(meta)
        return out
