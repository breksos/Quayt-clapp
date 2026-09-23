"""Development entry point. Production process management is release-owned."""

import uvicorn


def main() -> None:
    uvicorn.run("quayt_service.app:create_app", factory=True, host="127.0.0.1", port=8080)


if __name__ == "__main__":
    main()
