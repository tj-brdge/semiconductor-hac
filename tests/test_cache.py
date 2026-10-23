import pytest

from themegraph.data.cache import CacheConflict, RawCache, cache_key


def test_key_ignores_credentials_and_param_order():
    a = cache_key("fmp", "prices", {"symbol": "MU", "apikey": "secret", "from": "2020"})
    b = cache_key("fmp", "prices", {"from": "2020", "symbol": "MU", "apikey": "other"})
    assert a == b


def test_put_get_and_refuse_overwrite(tmp_path):
    c = RawCache(tmp_path)
    k = cache_key("s", "e", {"x": 1})
    c.put(k, b"one", {"params": {"x": 1, "apikey": "secret"}})
    assert c.get(k).body == b"one"
    c.put(k, b"one", {})  # identical bytes are fine
    with pytest.raises(CacheConflict):
        c.put(k, b"two", {})
    assert "secret" not in (tmp_path / k[:2] / f"{k}.json").read_text()


def test_corruption_detected(tmp_path):
    c = RawCache(tmp_path)
    k = cache_key("s", "e")
    c.put(k, b"abc", {})
    (tmp_path / k[:2] / f"{k}.body").write_bytes(b"abd")
    with pytest.raises(CacheConflict):
        c.get(k)
