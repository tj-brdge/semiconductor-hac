"""Market layer v2: single-index shrinkage (Ledoit & Wolf 2003), co-movement beyond
the index, geometric linking, and the monitor's "trading together" measure."""

import numpy as np
import pandas as pd
import pytest

from themegraph.config import Issuer, UniverseConfig
from themegraph.market.hac import single_index_shrinkage
from themegraph.monitor.baskets import PriceBook
from themegraph.monitor.comovement import trading_together


def _lw_loops(X: np.ndarray, m: np.ndarray) -> float:
    """The paper's shrinkage intensity written element by element (independent of
    the vectorised implementation): pi, rho and gamma from Ledoit & Wolf (2003)."""
    t, n = X.shape
    y = X - X.mean(0)
    ym = m - m.mean()
    s = y.T @ y / t
    sm = y.T @ ym / t
    smm = ym @ ym / t
    f = np.outer(sm, sm) / smm
    np.fill_diagonal(f, np.diag(s))
    pi = sum(np.mean((y[:, i] * y[:, j] - s[i, j]) ** 2) for i in range(n) for j in range(n))
    rho = sum(np.mean((y[:, i] * y[:, i] - s[i, i]) ** 2) for i in range(n))
    for i in range(n):
        for j in range(n):
            if i == j:
                continue
            r = ((sm[j] * smm * y[:, i] + sm[i] * smm * y[:, j] - sm[i] * sm[j] * ym)
                 * ym * y[:, i] * y[:, j] / smm ** 2 - f[i, j] * s[i, j])
            rho += np.mean(r)
    gamma = ((f - s) ** 2).sum()
    return float(np.clip((pi - rho) / gamma / t, 0, 1))


def _sample(T, seed=1, noise=12):
    rng = np.random.default_rng(seed)
    m = rng.normal(0, 0.012, T)
    g = rng.normal(0, 0.008, T)
    cols = {f"G{i}": m + g + rng.normal(0, 0.012, T) for i in range(4)}
    cols |= {f"H{i}": 1.8 * m + rng.normal(0, 0.012, T) for i in range(4)}
    cols |= {f"X{i}": rng.uniform(.6, 1.4) * m + rng.normal(0, 0.015, T) for i in range(noise)}
    return pd.DataFrame(cols), pd.Series(m)


def test_intensity_matches_the_papers_formulas():
    R, m = _sample(40, noise=1)
    est = single_index_shrinkage(R, m)
    assert est["shrinkage"] == pytest.approx(_lw_loops(R.to_numpy(), m.to_numpy()), abs=1e-10)


def test_beyond_index_correlation_is_the_sample_one_scaled_by_one_minus_shrinkage():
    R, m = _sample(126)
    est = single_index_shrinkage(R, m)
    x = (R - R.mean()).to_numpy()
    xm = (m - m.mean()).to_numpy()
    S, cm, vm = x.T @ x / len(x), x.T @ xm / len(x), xm @ xm / len(x)
    res = S - np.outer(cm, cm) / vm
    d = np.sqrt(np.diag(res))
    sample_excess = res / np.outer(d, d)
    off = ~np.eye(len(R.columns), dtype=bool)
    assert np.allclose(est["excess_corr"].to_numpy()[off],
                       (1 - est["shrinkage"]) * sample_excess[off])


def test_shrinkage_falls_as_the_window_grows():
    assert single_index_shrinkage(*_sample(2000))["shrinkage"] < \
        single_index_shrinkage(*_sample(63))["shrinkage"]


def test_theme_beyond_the_market_ranks_above_high_beta_stocks():
    R, m = _sample(126)
    c = single_index_shrinkage(R, m)["excess_corr"]
    tot = R.corr()

    def avg(M, s):
        return M.loc[s, s].to_numpy()[np.triu_indices(len(s), 1)].mean()
    G, H = [f"G{i}" for i in range(4)], [f"H{i}" for i in range(4)]
    assert avg(tot, H) > avg(tot, G)  # total correlation favours high beta
    assert avg(c, G) > 5 * abs(avg(c, H))  # beyond the index it does not


# -- monitor measure ----------------------------------------------------------------

def _book(T=300, seed=3, gaps=False):
    rng = np.random.default_rng(seed)
    cal = pd.bdate_range("2021-01-04", periods=T, tz="UTC") + pd.Timedelta(hours=22)
    m = rng.normal(0, 0.01, T)
    g = rng.normal(0, 0.008, T) * (np.arange(T) >= T // 2)  # theme starts mid-sample
    px = {"SPY": 100 * np.cumprod(1 + m)}
    seg = {}
    for i in range(6):
        px[f"G{i}"] = 100 * np.cumprod(1 + m + g + rng.normal(0, 0.012, T))
        seg[f"G{i}"] = "a" if i < 3 else "b"
    for i in range(24):
        px[f"X{i}"] = 100 * np.cumprod(1 + rng.uniform(.6, 1.6) * m + rng.normal(0, 0.014, T))
        seg[f"X{i}"] = "a" if i % 2 else "b"
    df = pd.DataFrame(px, index=cal)
    if gaps:  # a foreign-style stock without closes on some days
        df.loc[df.index[::15], "G0"] = np.nan
    u = UniverseConfig(issuers=[Issuer(symbol=s, segment=v, role="core") for s, v in seg.items()],
                       benchmarks=["SPY"], sectors={"a": "A", "b": "B"})
    return PriceBook(df, "SPY", {c: "USD" for c in df}, None, None, 5), u


def test_trading_together_finds_the_theme_and_its_onset():
    book, u = _book()
    out = trading_together(book, u, {"th-g": [f"G{i}" for i in range(6)],
                                     "th-x": [f"X{i}" for i in range(6)]},
                           126, 0.9, 500, "SPY")
    g, x = out["th-g"], out["th-x"]
    assert g["now"]["percentile"] > 0.95 and g["now"]["companies"] == 6
    assert g["before"]["percentile"] < g["now"]["percentile"]  # before the theme began
    assert 0.05 < x["now"]["percentile"] < 0.95                 # unrelated stocks: ordinary
    assert 0 < g["now"]["shrinkage"] < 1
    again = trading_together(book, u, {"th-g": [f"G{i}" for i in range(6)]},
                             126, 0.9, 500, "SPY")
    assert again["th-g"] == g  # deterministic per theme


def test_gaps_are_linked_geometrically_not_dropped():
    book, u = _book(gaps=True)
    out = trading_together(book, u, {"th-g": [f"G{i}" for i in range(6)]},
                           126, 0.9, 200, "SPY")
    assert out["th-g"]["now"]["companies"] == 6  # 93% of own closes: kept
    days = book.cal[-127:]
    r = book.carry["G0"].reindex(days).pct_change().iloc[1:]
    total = book.carry["G0"].reindex(days)
    assert np.prod(1 + r) - 1 == pytest.approx(total.iloc[-1] / total.iloc[0] - 1)
