from __future__ import annotations

import os

from fastapi import HTTPException, Request


def verify_api_key(request: Request) -> None:
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        raise HTTPException(
            status_code=401,
            detail={"code": "unauthorized", "message": "Authorization header is missing", "status": 401},
        )
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail={"code": "unauthorized", "message": "Invalid authorization format. Use Bearer token", "status": 401},
        )

    expected_token = os.getenv("ENTRY_API_KEY", "").strip()
    token = auth_header[7:].strip()
    if not expected_token or token != expected_token:
        raise HTTPException(
            status_code=401,
            detail={"code": "unauthorized", "message": "Access token is invalid", "status": 401},
        )
