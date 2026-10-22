import os
from pathlib import Path

import uvicorn

from xeonolab.common import Settings
from xeonolab.library import create_app

settings = Settings(
    Path(os.environ["CREATE_PATH_DATA_DIR"]),
    mode="development",
    dev_tokens={
        "create-path-local-admin": {
            "sub": "create-path-local-admin",
            "scope": "xelibrary:read xelibrary:write xelibrary:admin xelibrary:query xelibrary:theorem:read",
            "agent_kind": "human",
        }
    },
    rate_limit_enabled=False,
)
uvicorn.run(create_app(settings), host="127.0.0.1", port=int(os.environ["CREATE_PATH_API_PORT"]))
