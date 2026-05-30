"""eBay user-token OAuth — pure logic (no network)."""
from __future__ import annotations

import pytest

from lib.ebay_oauth import EbayUserAuth, EbayUserAuthError


def _auth(tmp_path, **kw):
    kw.setdefault("client_id", "cid")
    kw.setdefault("client_secret", "sec")
    kw.setdefault("redirect_uri", "MyRuName")
    kw.setdefault("env", "production")
    kw.setdefault("token_path", str(tmp_path / "tok.json"))
    return EbayUserAuth(**kw)


def test_extract_code_from_full_url():
    code = EbayUserAuth.extract_code("https://example.com/cb?code=ABC123&state=x")
    assert code == "ABC123"


def test_extract_code_from_bare_code():
    assert EbayUserAuth.extract_code("ABC123") == "ABC123"


def test_extract_code_missing_raises():
    with pytest.raises(EbayUserAuthError):
        EbayUserAuth.extract_code("https://example.com/cb?state=x")


def test_build_consent_url(tmp_path):
    url = _auth(tmp_path).build_consent_url()
    assert url.startswith("https://auth.ebay.com/oauth2/authorize?")
    assert "client_id=cid" in url
    assert "redirect_uri=MyRuName" in url
    assert "sell.inventory" in url


def test_sandbox_uses_sandbox_auth_host(tmp_path):
    url = _auth(tmp_path, env="sandbox").build_consent_url()
    assert url.startswith("https://auth.sandbox.ebay.com/oauth2/authorize?")


def test_missing_redirect_uri_raises(tmp_path):
    auth = _auth(tmp_path, redirect_uri="")
    with pytest.raises(EbayUserAuthError, match="EBAY_REDIRECT_URI"):
        auth.build_consent_url()


def test_unauthorized_until_token_present(tmp_path):
    auth = _auth(tmp_path)
    assert auth.is_authorized() is False
