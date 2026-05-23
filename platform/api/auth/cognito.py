"""
Cognito JWT validation for the IDP Platform API.

Validates Bearer tokens issued by the Cognito User Pool.
Enforces OAuth 2.0 scopes: idp:provision, idp:read, idp:destroy.
"""

import os
import time
from functools import lru_cache
from typing import Annotated

import boto3
import jwt
import structlog
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

log = structlog.get_logger(__name__)

security = HTTPBearer(auto_error=True)


class CognitoSettings:
    """Resolved from environment variables — never hardcoded."""

    def __init__(self) -> None:
        self.user_pool_id: str = os.environ["COGNITO_USER_POOL_ID"]
        self.client_id: str = os.environ["COGNITO_CLIENT_ID"]
        self.region: str = os.environ.get("AWS_REGION", "ca-central-1")

    @property
    def jwks_uri(self) -> str:
        return (
            f"https://cognito-idp.{self.region}.amazonaws.com"
            f"/{self.user_pool_id}/.well-known/jwks.json"
        )

    @property
    def issuer(self) -> str:
        return f"https://cognito-idp.{self.region}.amazonaws.com/{self.user_pool_id}"


@lru_cache(maxsize=1)
def get_settings() -> CognitoSettings:
    return CognitoSettings()


@lru_cache(maxsize=1)
def get_jwks_client() -> jwt.PyJWKClient:
    settings = get_settings()
    return jwt.PyJWKClient(settings.jwks_uri, cache_jwk_set=True, lifespan=3600)


class TokenPayload:
    """Validated token payload."""

    def __init__(self, sub: str, scope: str, client_id: str, token_use: str) -> None:
        self.sub = sub
        self.scopes: set[str] = set(scope.split()) if scope else set()
        self.client_id = client_id
        self.token_use = token_use

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes


async def get_token_payload(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
) -> TokenPayload:
    """
    Validate a Cognito JWT and return the decoded payload.

    Raises HTTP 401 on invalid/expired token.
    Raises HTTP 403 on missing scopes.
    """
    settings = get_settings()
    token = credentials.credentials

    try:
        jwks_client = get_jwks_client()
        signing_key = jwks_client.get_signing_key_from_jwt(token)

        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=settings.client_id,
            issuer=settings.issuer,
            options={"verify_exp": True},
        )
    except jwt.ExpiredSignatureError:
        log.warning("auth.token_expired")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.InvalidTokenError as e:
        log.warning("auth.invalid_token", error=str(e))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return TokenPayload(
        sub=payload.get("sub", ""),
        scope=payload.get("scope", ""),
        client_id=payload.get("client_id", ""),
        token_use=payload.get("token_use", ""),
    )


def require_scope(required_scope: str):
    """
    FastAPI dependency factory — enforces a specific OAuth scope.

    Usage:
        @router.post("/provision", dependencies=[Depends(require_scope("idp:provision"))])
    """
    async def _check_scope(
        payload: Annotated[TokenPayload, Depends(get_token_payload)],
    ) -> TokenPayload:
        if not payload.has_scope(required_scope):
            log.warning(
                "auth.insufficient_scope",
                required=required_scope,
                present=list(payload.scopes),
                sub=payload.sub,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Required scope: {required_scope}",
            )
        return payload

    return _check_scope
