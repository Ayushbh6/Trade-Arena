"""Token auth for the UI/API (Phase 11).

Goals:
- No external dependencies.
- Works with CORS allow-origins="*" (token in Authorization header).
- Supports WebSocket auth via `?token=...`.
"""

from __future__ import annotations

import base64
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass
from hashlib import sha256
from typing import Any, Dict, Optional


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def _b64url_decode(txt: str) -> bytes:
    pad = "=" * (-len(txt) % 4)
    return base64.urlsafe_b64decode((txt + pad).encode("utf-8"))


def _sign(data: bytes, *, secret: str) -> str:
    mac = hmac.new(secret.encode("utf-8"), data, sha256).digest()
    return _b64url_encode(mac)


def _default_secret() -> str:
    # Stable by default so tokens survive API restarts in dev; override in prod.
    return os.getenv("UI_TOKEN_SECRET") or os.getenv("UI_BASIC_AUTH_PASS") or "dev-secret-change-me"


@dataclass(frozen=True)
class TokenClaims:
    sub: str
    iat: int
    exp: int

    def to_dict(self) -> Dict[str, Any]:
        return {"sub": self.sub, "iat": self.iat, "exp": self.exp}


def create_token(*, username: str, ttl_seconds: int = 12 * 60 * 60, secret: Optional[str] = None) -> str:
    now = int(time.time())
    claims = TokenClaims(sub=username, iat=now, exp=now + int(ttl_seconds))
    payload = json.dumps(claims.to_dict(), separators=(",", ":"), sort_keys=True).encode("utf-8")
    secret_val = secret or _default_secret()
    sig = _sign(payload, secret=secret_val)
    return f"v1.{_b64url_encode(payload)}.{sig}"


def verify_token(*, token: str, secret: Optional[str] = None, now: Optional[int] = None) -> Optional[TokenClaims]:
    try:
        parts = token.split(".")
        if len(parts) != 3 or parts[0] != "v1":
            return None
        payload_b64 = parts[1]
        sig = parts[2]
        payload = _b64url_decode(payload_b64)
        secret_val = secret or _default_secret()
        expected = _sign(payload, secret=secret_val)
        if not secrets.compare_digest(sig, expected):
            return None
        data = json.loads(payload.decode("utf-8"))
        sub = str(data.get("sub") or "")
        iat = int(data.get("iat"))
        exp = int(data.get("exp"))
        now_i = int(time.time()) if now is None else int(now)
        if not sub or exp <= now_i:
            return None
        return TokenClaims(sub=sub, iat=iat, exp=exp)
    except Exception:
        return None


def check_login(*, username: str, password: str) -> bool:
    cfg_user = os.getenv("UI_BASIC_AUTH_USER", "user001")
    cfg_pass = os.getenv("UI_BASIC_AUTH_PASS", "trader@123")
    return secrets.compare_digest(username, cfg_user) and secrets.compare_digest(password, cfg_pass)

