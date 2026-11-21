"""TikTok sign-in helpers. Network calls are stubbed."""
import json
import time
from types import SimpleNamespace

import pytest

from clipagent import tiktok_auth
from clipagent.config import settings


@pytest.fixture(autouse=True)
def cfg(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "output_dir", tmp_path)
    monkeypatch.setattr(settings, "tiktok_client_key", "ck")
    monkeypatch.setattr(settings, "tiktok_client_secret", "cs")
    monkeypatch.setattr(settings, "tiktok_access_token", None)


def _fake_post(calls, reply):
    def post(url, data=None, **kw):
        calls.append(data)
        return SimpleNamespace(json=lambda: reply, raise_for_status=lambda: None)
    return post


def test_authorize_url_carries_scopes_and_redirect():
    url, state = tiktok_auth.authorize_url("abc")
    assert state == "abc" and "client_key=ck" in url and "state=abc" in url
    assert "video.upload" in url and "callback.html" in url


def test_code_exchange_stores_token(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(tiktok_auth.requests, "post", _fake_post(calls, {
        "access_token": "act.1", "refresh_token": "rft.1", "expires_in": 86400, "scope": "video.upload"}))
    tiktok_auth.exchange_code(" thecode ")
    assert calls[0]["code"] == "thecode" and calls[0]["grant_type"] == "authorization_code"
    assert tiktok_auth.access_token() == "act.1"


def test_expired_token_is_refreshed(monkeypatch, tmp_path):
    (tmp_path / "tiktok_token.json").write_text(json.dumps({
        "access_token": "old", "refresh_token": "rft.1", "expires_in": 86400,
        "obtained_at": time.time() - 90000}))
    calls = []
    monkeypatch.setattr(tiktok_auth.requests, "post", _fake_post(calls, {
        "access_token": "new", "refresh_token": "rft.2", "expires_in": 86400}))
    assert tiktok_auth.access_token() == "new"
    assert calls[0]["grant_type"] == "refresh_token" and calls[0]["refresh_token"] == "rft.1"


def test_refusal_is_reported(monkeypatch):
    monkeypatch.setattr(tiktok_auth.requests, "post", _fake_post([], {
        "error": "invalid_grant", "error_description": "Authorization code is expired."}))
    with pytest.raises(RuntimeError, match="expired"):
        tiktok_auth.exchange_code("x")


def test_not_signed_in():
    assert tiktok_auth.access_token() is None
