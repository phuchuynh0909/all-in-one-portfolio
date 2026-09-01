"""Proxy for the MBS broker's token refresh, used by the Chat Agents page.

This is NOT application authentication. It used to live at
``/auth/refresh-token``, one path segment away from ``/auth/login``, carrying
its own unrelated ``access_token``/``refresh_token`` vocabulary — an easy thing
to mistake for the app's own session refresh. It moved here to remove that
ambiguity; the request handling is unchanged.
"""
from typing import Optional

import requests
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field


REFRESH_URL = "https://accts.mbs.com.vn/webuaa/refreshToken"

router = APIRouter(prefix="/broker", tags=["broker"])


class RefreshTokenRequest(BaseModel):
    access_token: str = Field(..., min_length=1)
    refresh_token: str = Field(..., min_length=1)
    master_account: Optional[str] = None
    code_verifier: Optional[str] = None
    device_id: Optional[str] = None
    x_channel: Optional[str] = "S24"
    x_client_device_id: Optional[str] = None
    x_client_request_id: Optional[str] = None
    x_master_account: Optional[str] = None
    x_version: Optional[str] = "v1.2.84"


@router.post("/refresh-token")
def refresh_token(request: RefreshTokenRequest) -> dict:
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Content-Type": "application/x-www-form-urlencoded",
        "Authorization": f"Bearer {request.access_token}",
    }

    if request.x_channel:
        headers["x-channel"] = request.x_channel
    if request.x_client_device_id:
        headers["x-client-device-id"] = request.x_client_device_id
    if request.x_client_request_id:
        headers["x-client-request-id"] = request.x_client_request_id
    if request.x_master_account:
        headers["x-master-account"] = request.x_master_account
    if request.x_version:
        headers["x-version"] = request.x_version

    form = {"refresh_token": request.refresh_token}
    if request.master_account:
        form["master_account"] = request.master_account
    if request.code_verifier:
        form["code_verifier"] = request.code_verifier
    if request.device_id:
        form["device_id"] = request.device_id

    try:
        response = requests.post(REFRESH_URL, headers=headers, data=form, timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        status_code = getattr(exc.response, "status_code", 502)
        detail = getattr(exc.response, "text", str(exc))
        raise HTTPException(status_code=status_code, detail=detail)
