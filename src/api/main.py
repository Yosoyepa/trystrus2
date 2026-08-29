"""FastAPI application factory."""

from fastapi import FastAPI

from .config import Settings


def create_app(settings: Settings | None = None, service: object | None = None) -> FastAPI:
    """Create an app with injectable settings and service for tests."""

    from .router import router, set_service

    app = FastAPI(title="Aval Trust API", version="1.1.0")
    app.state.settings = settings or Settings()
    if service is not None:
        app.state.service = service
        set_service(service)
    app.include_router(router)

    @app.get("/healthz", tags=["health"])
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
