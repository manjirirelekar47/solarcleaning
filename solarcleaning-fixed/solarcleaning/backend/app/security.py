"""X-API-Key protection for actions that move hardware."""

import secrets

from fastapi import Header, HTTPException

from .config import settings


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """FastAPI dependency. Fails closed: no server key configured means no access."""
    expected = settings.api_key
    if not expected:
        raise HTTPException(status_code=503, detail="API_KEY is not configured on the server")
    if not x_api_key or not secrets.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=401, detail="Missing or invalid X-API-Key")
