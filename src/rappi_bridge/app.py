"""Local HTTP surface of the bridge (contract: aval/contracts/rappi-bridge.yaml).

Binds 127.0.0.1 by default; the kernel is the only intended client. Error
mapping: guard rejections 409 with a `reason`, Rappi pre-capture rejections
402, kill-switch/DRY_RUN mismatches 423, click ambiguity 502.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse

from .config import BridgeConfig
from .errors import BridgeError
from .rappi import RappiClient, load_session
from .service import BridgeService, PlaceOrderRequest
from .state import BridgeState
from .token import fetch_kernel_keys


def create_app(
    config: BridgeConfig | None = None,
    *,
    service: BridgeService | None = None,
) -> FastAPI:
    config = config or BridgeConfig()
    if service is None:
        client = RappiClient(
            load_session(config.session_file),
            base_url=config.rappi_base_url,
            timeout_s=config.http_timeout_s,
        )
        state = BridgeState(config.state_db_path)
        service = BridgeService(config, client, state, keys=lambda: fetch_kernel_keys(config))

    app = FastAPI(title="Aval Rappi Bridge", version="0.1.0", docs_url=None)
    app.state.service = service

    @app.exception_handler(BridgeError)
    async def _bridge_error(
        _request: Any, exc: BridgeError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content={"reason": exc.reason, "detail": exc.detail, "message": str(exc)},
        )

    def _guard_token(authorization: str | None) -> None:
        if config.local_token and authorization != f"Bearer {config.local_token}":
            raise HTTPException(status_code=401, detail="bad bridge token")

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {
            "ok": True,
            "dry_run": config.dry_run,
            "cap_cop": str(config.cap),
        }

    @app.get("/v1/rappi/session/preflight")
    def preflight(authorization: str | None = Header(default=None)) -> Any:
        _guard_token(authorization)
        return service.preflight()

    @app.post("/v1/rappi/quote")
    def quote(
        authorization: str | None = Header(default=None),
        store_type: str = "restaurant",
    ) -> Any:
        _guard_token(authorization)
        return service.quote(store_type).as_dict()

    @app.post("/v1/rappi/place_order")
    def place_order(
        body: dict[str, Any],
        authorization: str | None = Header(default=None),
        idempotency_key: str | None = Header(default=None),
    ) -> Any:
        _guard_token(authorization)
        if not idempotency_key:
            raise HTTPException(
                status_code=400, detail="Idempotency-Key header is required"
            )
        request = PlaceOrderRequest(
            idem_key=idempotency_key,
            purchase_id=str(body.get("purchase_id", "")),
            amount=str(body.get("amount", "")),
            cart_hash=str(body.get("cart_hash", "")),
            capture_token=str(body.get("capture_token", "")),
            expected_address_id=body.get("expected_address_id"),
            store_type=str(body.get("store_type", "restaurant")),
        )
        return service.place_order(request)

    @app.get("/v1/rappi/orders/{idem_key}")
    def order_status(idem_key: str, authorization: str | None = Header(default=None)) -> Any:
        _guard_token(authorization)
        row = service.order_status(idem_key)
        if row is None:
            raise HTTPException(status_code=404, detail="unknown idem_key")
        return row

    return app


def main() -> None:  # pragma: no cover — operator entrypoint
    import uvicorn

    config = BridgeConfig()
    uvicorn.run(
        create_app(config),
        host=config.bind,
        port=config.port,
        log_level="info",
    )


if __name__ == "__main__":  # pragma: no cover
    main()
