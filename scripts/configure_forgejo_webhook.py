"""Configure the signed fixed-destination repository webhook."""

import json
import ssl
from pathlib import Path

import httpx

root = Path("runtime/deploy-secrets")
users = json.loads((root / "forgejo-users.json").read_text())
url = "http://192.168.0.101:8081/api/v1/webhooks/forgejo"
body = {
    "type": "forgejo",
    "active": True,
    "events": ["issues"],
    "config": {
        "url": url,
        "content_type": "json",
        "secret": (root / "forgejo-webhook-secret").read_text().strip(),
    },
}
context = ssl.create_default_context(cafile=root / "lan-root-ca.crt")
with httpx.Client(
    base_url="https://192.168.0.101:9445/api/v1",
    verify=context,
    trust_env=False,
    auth=("xenon-admin", users["xenon-admin"]),
    timeout=60,
) as client:
    path = "/repos/xenon-admin/XeonoLab/hooks"
    result = client.get(path)
    result.raise_for_status()
    old = next((item for item in result.json() if item["config"].get("url") == url), None)
    result = client.patch(path + "/" + str(old["id"]), json=body) if old else client.post(path, json=body)
    if not result.is_success:
        raise SystemExit("Webhook configuration failed with HTTP " + str(result.status_code))
print("Signed repository Issue webhook configured")
