"""Physical-loopback XeLibrary service for temporary synthetic selection data."""

import argparse
from pathlib import Path

import uvicorn

from xeonolab.common import Settings
from xeonolab.services import create_app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    settings = Settings(
        Path(args.data_dir),
        "development",
        rate_limit_enabled=False,
        dev_tokens={
            "issue236-synthetic-token": {
                "sub": "issue236-synthetic-user",
                "scope": "xelibrary:read xelibrary:write xelibrary:query xelibrary:attach",
                "agent_kind": "test",
            }
        },
    )
    uvicorn.run(create_app("xelibrary", settings), host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
