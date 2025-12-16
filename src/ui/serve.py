"""Convenience launcher for the dashboard API.

This avoids remembering long uvicorn flags (and can force a stable WS backend).

Dev:
  python -m src.ui.serve --reload

Prod (Railway):
  python -m src.ui.serve --host 0.0.0.0 --port $PORT
"""

from __future__ import annotations

import argparse
import os

import uvicorn


def main() -> None:
    p = argparse.ArgumentParser(description="Serve UI API (FastAPI)")
    p.add_argument("--host", default=os.getenv("HOST", "127.0.0.1"))
    p.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")))
    p.add_argument("--reload", action="store_true")
    p.add_argument("--env-file", default=os.getenv("ENV_FILE", ".env"))
    args = p.parse_args()

    uvicorn.run(
        "src.ui.api:app",
        host=str(args.host),
        port=int(args.port),
        reload=bool(args.reload),
        env_file=str(args.env_file) if args.env_file else None,
        ws="wsproto",  # stable across environments; falls back to polling in UI if WS isn't available.
        log_level=os.getenv("LOG_LEVEL", "info"),
    )


if __name__ == "__main__":
    main()

