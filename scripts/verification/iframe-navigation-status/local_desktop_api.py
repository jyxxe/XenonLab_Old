import os
from pathlib import Path

import uvicorn

from xeonolab.common import Settings
from xeonolab.services import create_app

settings = Settings(
    Path(os.environ["ROUND31_DATA_DIR"]),
    mode="development",
    dev_tokens={
        "round31-local-synthetic-token": {
            "sub": "round31-synthetic-admin",
            "scope": "desktop:read desktop:admin",
            "agent_kind": "human",
        }
    },
    rate_limit_enabled=False,
)
uvicorn.run(create_app("desktop-web", settings), host="127.0.0.1", port=int(os.environ["ROUND31_API_PORT"]))
