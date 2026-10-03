"""Real owned loopback Desktop API; synthetic apps and development identity only."""

import argparse
from pathlib import Path

import uvicorn
from fastapi.testclient import TestClient

from xeonolab.common import Settings
from xeonolab.services import create_app

TOKEN = "issue244-synthetic-token"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    settings = Settings(
        Path(args.data_dir),
        mode="development",
        dev_tokens={TOKEN: {"sub": "issue244-synthetic-human", "scope": "desktop:read desktop:admin", "agent_kind": "human"}},
        rate_limit_enabled=False,
    )
    app = create_app("desktop-web", settings)
    with TestClient(app) as client:
        for name in ("alpha", "beta"):
            result = client.post(
                "/api/v1/apps",
                headers={"Authorization": f"Bearer {TOKEN}"},
                json={"id": f"synthetic-{name}", "title": f"Synthetic {name.title()}", "origin": f"https://{name}.example.test", "entry": "/", "icon": "◇", "default_size": [1000, 700], "singleton": True, "permissions": ["desktop:read", "desktop:admin"] if name == "alpha" else [], "sandbox": {"allow_scripts": True, "allow_forms": True, "allow_downloads": False}},
            )
            assert result.status_code == 201, result.text
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
