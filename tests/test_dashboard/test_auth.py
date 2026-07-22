import pytest
from fastapi import HTTPException

from config.settings import Settings
from dashboard.auth import require_auth


@pytest.mark.asyncio
async def test_rejects_when_no_token_configured():
    settings = Settings(_env_file=None, dashboard_auth_token=None)
    verify = require_auth(settings)

    with pytest.raises(HTTPException) as exc_info:
        await verify(authorization="Bearer anything")
    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_rejects_missing_header():
    settings = Settings(_env_file=None, dashboard_auth_token="secret")
    verify = require_auth(settings)

    with pytest.raises(HTTPException) as exc_info:
        await verify(authorization=None)
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_rejects_wrong_token():
    settings = Settings(_env_file=None, dashboard_auth_token="secret")
    verify = require_auth(settings)

    with pytest.raises(HTTPException) as exc_info:
        await verify(authorization="Bearer wrong")
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_accepts_correct_token():
    settings = Settings(_env_file=None, dashboard_auth_token="secret")
    verify = require_auth(settings)

    await verify(authorization="Bearer secret")  # must not raise
