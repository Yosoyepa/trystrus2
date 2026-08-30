"""Local HTTP surface of the bridge (contract: aval/contracts/rappi-bridge.yaml).

Binds 127.0.0.1 by default; intended clients are the kernel and the platform
front (Config Rappi panel). Error mapping: guard rejections 409 with a
`reason`, Rappi pre-capture rejections 402, kill-switch/DRY_RUN mismatches
423, click ambiguity 502.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import BridgeConfig
from .errors import BridgeError
from .login import LoginFlow
from .rappi import LazyRappiClient
from .service import BridgeService, PlaceOrderRequest
from .state import BridgeState
from .token import fetch_kernel_keys


def create_app(
    config: BridgeConfig | None = None,
    *,
    service: BridgeService | None = None,
    login_flow: LoginFlow | None = None,
) -> FastAPI:
    config = config or BridgeConfig()
    if service is None:
        client: Any = LazyRappiClient(config)  # starts with no session (idle)
        state = BridgeState(config.state_db_path)
        service = BridgeService(config, client, state, keys=lambda: fetch_kernel_keys(config))
    login_flow = login_flow or LoginFlow(config)

    app = FastAPI(title="Aval Rappi Bridge", version="0.1.0", docs_url=None)
    app.state.service = service
    app.state.login_flow = login_flow

    app.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origin_list,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
    )

    @app.exception_handler(BridgeError)
    async def _bridge_error(_request: Any, exc: BridgeError) -> JSONResponse:
        print(f"BRIDGE ERROR [{exc.http_status}]: {exc.reason} - {exc} (detail: {exc.detail})")
        return JSONResponse(
            status_code=exc.http_status,
            content={"reason": exc.reason, "detail": exc.detail, "message": str(exc)},
        )

    def _guard_token(authorization: str | None) -> None:
        if config.local_token and authorization != f"Bearer {config.local_token}":
            raise HTTPException(status_code=401, detail="bad bridge token")

    @app.get("/healthz")
    def healthz(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        _guard_token(authorization)
        return {
            "ok": True,
            "dry_run": config.dry_run,
            "cap_cop": str(config.cap),
        }

    # -- session / Config Rappi ---------------------------------------------

    @app.get("/v1/rappi/session/status")
    def session_status(authorization: str | None = Header(default=None)) -> Any:
        _guard_token(authorization)
        return login_flow.status()

    @app.post("/v1/rappi/session/login")
    def session_login(authorization: str | None = Header(default=None)) -> Any:
        _guard_token(authorization)
        return login_flow.start()

    @app.post("/v1/rappi/session/manual")
    def session_manual(
        body: dict[str, Any],
        authorization: str | None = Header(default=None),
    ) -> Any:
        """Plan B: paste the ft. token by hand (DevTools → Authorization)."""
        _guard_token(authorization)
        token = str(body.get("token", ""))
        device_id = body.get("device_id")
        return login_flow.connect_with_token(token, device_id=str(device_id) if device_id else None)

    @app.delete("/v1/rappi/session")
    def session_disconnect(
        authorization: str | None = Header(default=None),
    ) -> Any:
        _guard_token(authorization)
        return login_flow.disconnect()

    @app.get("/v1/rappi/payment/methods")
    def payment_methods(authorization: str | None = Header(default=None)) -> Any:
        _guard_token(authorization)
        return {"methods": service.payment_methods()}

    @app.get("/v1/rappi/session/preflight")
    def preflight(authorization: str | None = Header(default=None)) -> Any:
        _guard_token(authorization)
        return service.preflight()

    # -- commerce -------------------------------------------------------------

    @app.get("/v1/rappi/search")
    def search(
        q: str,
        authorization: str | None = Header(default=None),
    ) -> Any:
        _guard_token(authorization)
        return {"query": q, "results": service.search(q)}

    @app.post("/v1/rappi/cart/add")
    def cart_add(body: dict[str, Any], authorization: str | None = Header(default=None)) -> Any:
        """Replace the cart with exactly the approved product.

        The kernel's capture flow calls this right before quoting: the cart
        binding (cart_hash) is only trustworthy if the cart can hold nothing
        but what this endpoint put there.
        """
        _guard_token(authorization)
        return service.add_to_cart(
            store_type=str(body.get("store_type", "restaurant")),
            store_id=str(body.get("store_id", "")),
            product_id=str(body.get("product_id", "")),
            name=str(body.get("name", "")),
            quantity=int(body.get("quantity", 1)),
            price=int(body.get("price", 0)),
        )

    @app.post("/v1/rappi/quote")
    def quote(
        authorization: str | None = Header(default=None),
        store_type: str = "restaurant",
        require_clean_cart: bool = True,
    ) -> Any:
        _guard_token(authorization)
        # require_clean_cart=False is for the capture flow, which owns the
        # cart: it just replaced its contents with the approved product.
        return service.quote(store_type, require_clean_cart=require_clean_cart).as_dict()

    @app.post("/v1/rappi/place_order")
    def place_order(
        body: dict[str, Any],
        authorization: str | None = Header(default=None),
        idempotency_key: str | None = Header(default=None),
    ) -> Any:
        _guard_token(authorization)
        if not idempotency_key:
            raise HTTPException(status_code=400, detail="Idempotency-Key header is required")
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
