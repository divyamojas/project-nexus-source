from __future__ import annotations

import logging
from typing import Any, Dict

import httpx
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import jwt, JWTError

from app.config import settings

logger = logging.getLogger(__name__)

_jwks_cache: Dict[str, Any] = {}


async def _fetch_jwks() -> Dict[str, Any]:
    global _jwks_cache
    if _jwks_cache:
        return _jwks_cache
    url = f"{settings.SUPABASE_URL}/auth/v1/.well-known/jwks.json"
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, timeout=10)
        resp.raise_for_status()
        _jwks_cache = resp.json()
    return _jwks_cache


async def verify_with_jwks(token: str) -> Dict[str, Any]:
    try:
        jwks = await _fetch_jwks()
    except Exception as exc:
        logger.warning("Failed to fetch JWKS: %s", exc)
        raise

    header = jwt.get_unverified_header(token)
    kid = header.get("kid")
    alg = header.get("alg", "RS256")

    key = None
    for k in jwks.get("keys", []):
        if kid and k.get("kid") == kid:
            key = k
            break
        elif not kid:
            key = k
            break

    if key is None:
        raise JWTError("No matching key found in JWKS")

    payload = jwt.decode(token, key, algorithms=[alg], options={"verify_aud": False})
    return payload


async def verify_with_supabase(token: str) -> Dict[str, Any]:
    url = f"{settings.SUPABASE_URL}/auth/v1/user"
    headers = {
        "Authorization": f"Bearer {token}",
        "apikey": settings.SUPABASE_SERVICE_KEY,
    }
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            raise JWTError("Remote verification failed")
        data = resp.json()

    return {"sub": data["id"], "email": data.get("email", "")}


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(HTTPBearer()),
) -> Dict[str, Any]:
    token = credentials.credentials
    try:
        header = jwt.get_unverified_header(token)
        alg = header.get("alg", "")

        if alg in ("RS256", "ES256"):
            payload = await verify_with_jwks(token)
        elif alg == "HS256" and settings.JWT_SECRET:
            payload = jwt.decode(
                token,
                settings.JWT_SECRET,
                algorithms=["HS256"],
                options={"verify_aud": False},
            )
        else:
            payload = await verify_with_supabase(token)

        user_id = payload.get("sub") or payload.get("id", "")
        email = payload.get("email", "")
        return {"id": str(user_id), "email": email}
    except HTTPException:
        raise
    except Exception as exc:
        logger.debug("Token verification failed: %s", exc)
        raise HTTPException(status_code=401, detail="Invalid or expired token")
