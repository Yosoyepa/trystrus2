"""HTTP router placeholder completed by the application layer."""

from fastapi import APIRouter

router = APIRouter()
_service: object | None = None


def set_service(service: object) -> None:
    global _service
    _service = service


def get_service() -> object | None:
    return _service
