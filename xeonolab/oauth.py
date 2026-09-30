"""Server-side service credentials; access tokens remain outside model inputs."""

import os
import ssl
import threading
import time
from pathlib import Path

import httpx

from .common import APIError

_cache = {}
_lock = threading.Lock()


def service_token(prefix):
    if os.getenv(prefix + "_SERVICE_TOKEN"):
        return os.environ[prefix + "_SERVICE_TOKEN"]
    with _lock:
        old = _cache.get(prefix)
        if old and old[1] > time.time() + 30:
            return old[0]
        try:
            secret = Path(os.environ[prefix + "_CLIENT_SECRET_FILE"]).read_text().strip()
            context = ssl.create_default_context(cafile=os.getenv("OIDC_CA_FILE"))
            with httpx.Client(timeout=15, trust_env=False, verify=context) as client:
                result = client.post(
                    os.environ["OIDC_TOKEN_URL"],
                    data={
                        "grant_type": "client_credentials",
                        "client_id": os.environ[prefix + "_CLIENT_ID"],
                        "client_secret": secret,
                    },
                )
            if result.status_code != 200:
                raise ValueError("token exchange failed")
            body = result.json()
            _cache[prefix] = (body["access_token"], time.time() + body["expires_in"])
            return body["access_token"]
        except (KeyError, OSError, ValueError, httpx.HTTPError):
            raise APIError(
                503, "SERVICE_AUTH_UNAVAILABLE", "service account authentication unavailable"
            ) from None
