import hashlib
import json
import os
from pathlib import Path

import uvicorn

from xeonolab import desktop
from xeonolab.common import Settings

print(json.dumps({"module_path": desktop.__file__, "sha256": hashlib.sha256(Path(desktop.__file__).read_bytes()).hexdigest()}), flush=True)

settings = Settings(
    Path(os.environ["APP_AUDIT_DATA_DIR"]),
    mode="development",
    dev_tokens={
        "r35-admin-a-token": {"sub": "r35-admin-a", "scope": "desktop:read desktop:admin", "agent_kind": "human"},
        "r35-admin-b-token": {"sub": "r35-admin-b", "scope": "desktop:read desktop:admin", "agent_kind": "human"},
        "r35-admin-c-token": {"sub": "9a03d4f1-3357-4d72-afdb-a906070eea10", "scope": "desktop:read desktop:admin", "agent_kind": "human"},
    },
    rate_limit_enabled=False,
)
uvicorn.run(desktop.create_app(settings), host="127.0.0.1", port=int(os.environ["APP_AUDIT_API_PORT"]), log_level="warning")
