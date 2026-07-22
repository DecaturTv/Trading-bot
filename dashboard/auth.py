from fastapi import Header, HTTPException, status

from config.settings import Settings


def require_auth(settings: Settings):
    """Bearer-token dependency factory — fail-closed: no token configured
    means no access, not open access."""

    async def _verify(authorization: str | None = Header(default=None)) -> None:
        if not settings.dashboard_auth_token:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="dashboard auth token not configured"
            )
        if authorization != f"Bearer {settings.dashboard_auth_token}":
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid or missing bearer token")

    return _verify
