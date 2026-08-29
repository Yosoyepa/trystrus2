from src.api.main import app

__all__ = ["app", "main"]


def main() -> None:
    """Run the thin local entrypoint."""

    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    main()
