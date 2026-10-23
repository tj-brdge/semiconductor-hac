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


def test_env_value_strips_smart_quotes_and_rejects_other_unicode(monkeypatch):
    from themegraph.data.http import env_value

    monkeypatch.setenv("EDGAR_USER_AGENT", "“Jane Doe jane@example.com”")
    assert env_value("EDGAR_USER_AGENT") == "Jane Doe jane@example.com"
    monkeypatch.setenv("FMP_API_KEY", "abc–def")
    with pytest.raises(RuntimeError, match="U\\+2013") as e:
        env_value("FMP_API_KEY")
    assert "abc" not in str(e.value)
    monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
    assert env_value("VOYAGE_API_KEY") is None


def test_edgar_client_accepts_smart_quoted_user_agent(tmp_path, monkeypatch):
    from themegraph.data.edgar import EdgarClient

    monkeypatch.setenv("EDGAR_USER_AGENT", "“Jane Doe jane@example.com”")
    c = EdgarClient(tmp_path)
    assert c.f._client.headers["User-Agent"] == "Jane Doe jane@example.com"
