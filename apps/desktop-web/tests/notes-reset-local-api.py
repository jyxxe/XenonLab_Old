"""Owned loopback Desktop API using isolated SQLite and a synthetic development identity."""

import argparse
from pathlib import Path

import uvicorn

from xeonolab.common import Settings
from xeonolab.services import create_app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--port", required=True, type=int)
    args = parser.parse_args()
    settings = Settings(
        Path(args.data_dir),
        mode="development",
        dev_tokens={
            "issue242-synthetic-token": {
                "sub": "issue242-synthetic-human",
                "scope": "desktop:read desktop:admin",
                "agent_kind": "human",
            }
        },
        rate_limit_enabled=False,
    )
    uvicorn.run(create_app("desktop-web", settings), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
