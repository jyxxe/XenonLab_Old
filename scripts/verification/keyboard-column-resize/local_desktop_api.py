import os
from pathlib import Path

import uvicorn

from xeonolab.common import Settings
from xeonolab.services import create_app

settings = Settings(
    Path(os.environ["RESIZE_DESKTOP_DATA_DIR"]),
    mode="development",
    dev_tokens={
        "resize-local-admin": {
            "sub": "resize-local-admin",
            "scope": "desktop:read desktop:admin xelibrary:read xelibrary:write xelibrary:admin xelibrary:query",
            "agent_kind": "human",
        }
    },
    rate_limit_enabled=False,
)
uvicorn.run(create_app("desktop-web", settings), host="127.0.0.1", port=int(os.environ["RESIZE_DESKTOP_API_PORT"]))
