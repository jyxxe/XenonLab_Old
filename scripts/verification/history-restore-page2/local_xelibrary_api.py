import os
from pathlib import Path

import uvicorn

from xeonolab.common import Settings
from xeonolab.library import create_app

settings = Settings(
    Path(os.environ["ROUND51_DATA_DIR"]),
    mode="development",
    dev_tokens={
        "round57-local-admin": {
            "sub": "round57-local-admin",
            "scope": "xelibrary:read xelibrary:write xelibrary:admin xelibrary:query",
            "agent_kind": "human",
        }
    },
    rate_limit_enabled=False,
)
uvicorn.run(create_app(settings), host="127.0.0.1", port=int(os.environ["ROUND51_API_PORT"]))
