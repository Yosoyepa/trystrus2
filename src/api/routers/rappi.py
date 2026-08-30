"""Rappi Bridge proxy router.

Proxies `/v1/rappi/*` requests received by the Kernel Gateway to the
underlying Rappi Bridge microservice configured via `TT_RAPPI_BRIDGE_URL`.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Request, Response, status

router = APIRouter(prefix="/v1/rappi", tags=["rappi"])


def get_bridge_url() -> str:
    url = (
        os.environ.get("TT_RAPPI_BRIDGE_URL")
        or os.environ.get("RAPPI_BRIDGE_URL")
        or "http://localhost:8000"
    ).rstrip("/")
    return url


@router.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
    include_in_schema=False,
)
async def proxy_rappi(request: Request, path: str = "") -> Response:
    """Forward incoming /v1/rappi/* request to the Rappi Bridge."""
    bridge_url = get_bridge_url()
    target_url = f"{bridge_url}/v1/rappi/{path}" if path else f"{bridge_url}/v1/rappi"

    query_params = dict(request.query_params)

    headers = {
        k: v
        for k, v in request.headers.items()
        if k.lower() not in ("host", "content-length", "connection")
    }

    body = await request.body()

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.request(
                method=request.method,
                url=target_url,
                params=query_params,
                headers=headers,
                content=body,
            )

            resp_headers = {
                k: v
                for k, v in resp.headers.items()
                if k.lower() not in ("content-encoding", "transfer-encoding", "connection", "content-length")
            }

            return Response(
                content=resp.content,
                status_code=resp.status_code,
                headers=resp_headers,
                media_type=resp.headers.get("content-type"),
            )
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Rappi Bridge is unreachable at {bridge_url}: {exc}",
        ) from exc


@router.api_route(
    "",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
    include_in_schema=False,
)
async def proxy_rappi_root(request: Request) -> Response:
    """Forward /v1/rappi root path to the bridge."""
    return await proxy_rappi(request, path="")
