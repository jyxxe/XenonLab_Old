import os
from pathlib import Path

import uvicorn

from xeonolab.common import Settings
from xeonolab.services import create_app

settings = Settings(
    Path(os.environ["ROUND32_DATA_DIR"]),
    mode="development",
    dev_tokens={
        "round32-local-synthetic-token": {
            "sub": "round32-synthetic-admin",
            "scope": "desktop:read desktop:admin xelibrary:read",
            "agent_kind": "human",
        }
    },
    rate_limit_enabled=False,
)
uvicorn.run(create_app("desktop-web", settings), host="127.0.0.1", port=int(os.environ["ROUND32_API_PORT"]))
