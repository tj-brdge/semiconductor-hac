"""Stack per-snapshot layers into one long edge table: (t, layer, a, b, weight)."""

from __future__ import annotations

import numpy as np
import pandas as pd


def matrix_edges(t, layer: str, M: pd.DataFrame | None, min_weight: float = 0.0) -> pd.DataFrame:
    if M is None or M.empty:
        return pd.DataFrame(columns=["t", "layer", "a", "b", "weight"])
    names = M.index.to_numpy()
    iu = np.triu_indices(len(names), 1)
    w = M.to_numpy()[iu]
    keep = w > min_weight
    return pd.DataFrame({"t": t, "layer": layer, "a": names[iu[0]][keep],
                         "b": names[iu[1]][keep], "weight": w[keep]})


def text_affinity(exposure_t: pd.DataFrame) -> pd.DataFrame:
    """Cosine similarity of issuers' topic-exposure vectors at one snapshot."""
    if exposure_t.empty:
        return pd.DataFrame()
    E = exposure_t.pivot_table(index="symbol", columns="track_id", values="exposure",
                               fill_value=0.0)
    V = E.to_numpy()
    n = np.linalg.norm(V, axis=1, keepdims=True)
    n[n == 0] = 1.0
    U = V / n
    return pd.DataFrame(U @ U.T, index=E.index, columns=E.index)
