"""Deployment access guard (defence in depth behind Cloudflare Access).

Designed for a Cloudflare Tunnel: the app listens on 127.0.0.1 only, cloudflared forwards traffic from your domain, and
Cloudflare Access puts a login in front. Requests that came through the tunnel must carry a valid credential:
- `X-DAR-Secret` equal to DAR_API_SHARED_SECRET (sent only by the Cloudflare Pages proxy function), and/or
- a valid Cloudflare Access JWT (`Cf-Access-Jwt-Assertion`) for DAR_CF_ACCESS_TEAM_DOMAIN + DAR_CF_ACCESS_AUD.
If both are configured, both are required. If neither is configured the guard is off (local development);
DAR_REQUIRE_AUTH=true refuses to start in that state so a misconfigured deployment can't run open.
Optional DAR_ALLOWED_EMAILS (comma separated) restricts the Access identity further.
"""
from __future__ import annotations

import hmac
import threading
import time

import httpx
import jwt
from fastapi import Request
from fastapi.responses import JSONResponse

from .config import get_settings

_jwks: dict = {"keys": None, "at": 0.0}
_lock = threading.Lock()


class AccessConfigError(RuntimeError):
    pass


def check_config() -> None:
    s = get_settings()
    if s.require_auth and not (s.api_shared_secret or (s.cf_access_team_domain and s.cf_access_aud)):
        raise AccessConfigError("DAR_REQUIRE_AUTH=true but no DAR_API_SHARED_SECRET or Cloudflare Access settings are configured")


def _keys(team: str):
    with _lock:
        if _jwks["keys"] and time.time() - _jwks["at"] < 3600:
            return _jwks["keys"]
        data = httpx.get(f"https://{team}/cdn-cgi/access/certs", timeout=10).json()
        _jwks["keys"] = {k["kid"]: jwt.algorithms.RSAAlgorithm.from_jwk(k) for k in data["keys"]}
        _jwks["at"] = time.time()
        return _jwks["keys"]


def verify_access_jwt(token: str) -> dict:
    s = get_settings()
    kid = jwt.get_unverified_header(token).get("kid")
    key = _keys(s.cf_access_team_domain).get(kid)
    if key is None:
        _jwks["keys"] = None  # key rotation: refetch once
        key = _keys(s.cf_access_team_domain).get(kid)
    claims = jwt.decode(token, key=key, algorithms=["RS256"], audience=s.cf_access_aud,
                        issuer=f"https://{s.cf_access_team_domain}")
    allowed = [e.strip().lower() for e in (s.allowed_emails or "").split(",") if e.strip()]
    if allowed and (claims.get("email") or "").lower() not in allowed:
        raise jwt.InvalidTokenError("email not allowed")
    return claims


def _is_local_direct(request: Request) -> bool:
    """A request made on this machine directly (not via Cloudflare). cloudflared always adds Cf-Connecting-Ip,
    so tunnelled traffic never counts as local even though it also arrives from 127.0.0.1."""
    host = request.client.host if request.client else ""
    return host in ("127.0.0.1", "::1", "localhost") and "cf-connecting-ip" not in request.headers and "cf-ray" not in request.headers


async def guard(request: Request, call_next):
    s = get_settings()
    path = request.url.path
    if not path.startswith("/api") or path == "/api/health" or request.method == "OPTIONS":
        return await call_next(request)
    if s.allow_local_unauthenticated and _is_local_direct(request):
        return await call_next(request)
    if s.api_shared_secret:
        got = request.headers.get("x-dar-secret", "")
        if not hmac.compare_digest(got.encode(), s.api_shared_secret.encode()):
            return JSONResponse({"detail": "unauthorised"}, status_code=401)
    if s.cf_access_team_domain and s.cf_access_aud:
        token = request.headers.get("cf-access-jwt-assertion") or request.cookies.get("CF_Authorization")
        if not token:
            return JSONResponse({"detail": "unauthorised"}, status_code=401)
        try:
            verify_access_jwt(token)
        except Exception:
            return JSONResponse({"detail": "unauthorised"}, status_code=401)
    return await call_next(request)
