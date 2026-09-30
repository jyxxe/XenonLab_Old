"""Start every service in a real HTTP process and verify common endpoints."""

import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import httpx

from xeonolab.services import SERVICES

with tempfile.TemporaryDirectory() as temporary:
    for service in SERVICES:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        env = {**os.environ, "SERVICE": service, "DATA_DIR": temporary + "/" + service}
        env.pop("D100M_MODEL_PATH", None)
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "xeonolab.services:create_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            for attempt in range(40):
                try:
                    result = httpx.get(f"http://127.0.0.1:{port}/health/live", timeout=1, trust_env=False)
                    if result.status_code == 200:
                        break
                except httpx.HTTPError:
                    pass
                if process.poll() is not None:
                    raise RuntimeError(f"{service} failed to start")
                time.sleep(0.1)
            else:
                raise RuntimeError(f"{service} startup timeout")
            assert httpx.get(f"http://127.0.0.1:{port}/version", trust_env=False).json()["service"] == service
            assert httpx.get(f"http://127.0.0.1:{port}/openapi.json", trust_env=False).status_code == 200
            print(f"PASS {service}")
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
