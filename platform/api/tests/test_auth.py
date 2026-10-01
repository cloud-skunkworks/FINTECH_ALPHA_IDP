"""Tests for Cognito JWT validation (real get_token_payload, RSA-signed tokens)."""

import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from ..auth import cognito
from ..main import app

ISSUER = "https://cognito-idp.ca-central-1.amazonaws.com/ca-central-1_test123"


@pytest.fixture(scope="module")
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def auth_client(rsa_key, monkeypatch):
    """Client with the real auth dependency and a stubbed JWKS client."""

    class _Jwks:
        def get_signing_key_from_jwt(self, token):
            return SimpleNamespace(key=rsa_key.public_key())

    monkeypatch.setattr(cognito, "get_jwks_client", lambda: _Jwks())
    app.dependency_overrides.clear()
    with TestClient(app, base_url="http://localhost") as c:
        yield c


def _token(key, **overrides):
    claims = {
        "sub": "user-1",
        "iss": ISSUER,
        "client_id": "test-client-id",
        "token_use": "access",
        "scope": "idp:read",
        "exp": int(time.time()) + 300,
        **overrides,
    }
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key, algorithm="RS256")


def _get(client, token):
    return client.get("/v1/catalog", headers={"Authorization": f"Bearer {token}"})


def test_valid_access_token_without_aud_claim(auth_client, rsa_key):
    # Cognito access tokens carry client_id, not aud.
    assert _get(auth_client, _token(rsa_key)).status_code == 200


def test_missing_authorization_header_is_401(auth_client):
    response = auth_client.get("/v1/catalog")
    assert response.status_code == 401


def test_non_bearer_scheme_is_401(auth_client):
    assert auth_client.get("/v1/catalog", headers={"Authorization": "Basic abc"}).status_code == 401


def test_garbage_token_is_401(auth_client):
    response = _get(auth_client, "not-a-jwt")
    assert response.status_code == 401
    assert response.headers["WWW-Authenticate"] == "Bearer"


def test_expired_token_is_401(auth_client, rsa_key):
    response = _get(auth_client, _token(rsa_key, exp=int(time.time()) - 10))
    assert response.status_code == 401
    assert response.json()["detail"] == "Token has expired"


@pytest.mark.parametrize(
    "overrides",
    [
        {"iss": "https://evil.example.com"},
        {"client_id": "someone-else"},
        {"token_use": "id"},
    ],
)
def test_bad_claims_are_401(auth_client, rsa_key, overrides):
    assert _get(auth_client, _token(rsa_key, **overrides)).status_code == 401


def test_wrong_signing_key_is_401(auth_client):
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    assert _get(auth_client, _token(other)).status_code == 401


def test_jwks_lookup_failure_is_401_not_500(rsa_key, monkeypatch):
    class _Broken:
        def get_signing_key_from_jwt(self, token):
            raise jwt.PyJWKClientError("jwks unreachable")

    monkeypatch.setattr(cognito, "get_jwks_client", lambda: _Broken())
    app.dependency_overrides.clear()
    with TestClient(app, base_url="http://localhost") as c:
        assert _get(c, _token(rsa_key)).status_code == 401


def test_token_without_scope_claim_is_403(auth_client, rsa_key):
    assert _get(auth_client, _token(rsa_key, scope=None)).status_code == 403


def test_insufficient_scope_is_403(auth_client, rsa_key):
    assert _get(auth_client, _token(rsa_key, scope="idp:provision")).status_code == 403


def test_scope_is_exact_match_not_substring(auth_client, rsa_key):
    assert _get(auth_client, _token(rsa_key, scope="idp:readonly")).status_code == 403


def test_multiple_scopes_parsed(auth_client, rsa_key):
    assert _get(auth_client, _token(rsa_key, scope="openid idp:read idp:provision")).status_code == 200


def test_token_payload_helpers():
    payload = cognito.TokenPayload("s", "a b", "c", "access")
    assert payload.scopes == {"a", "b"}
    assert payload.has_scope("a") and not payload.has_scope("z")
    assert cognito.TokenPayload("s", "", "c", "access").scopes == set()


def test_settings_urls_and_jwks_client():
    cognito.get_settings.cache_clear()
    cognito.get_jwks_client.cache_clear()
    settings = cognito.get_settings()
    assert settings.issuer == ISSUER
    assert settings.jwks_uri == ISSUER + "/.well-known/jwks.json"
    assert isinstance(cognito.get_jwks_client(), jwt.PyJWKClient)
