"""Read-only inventory. Credentials supplied only through the environment."""

import json
import os
import ssl
import urllib.request

url = os.getenv("PVE_URL", "https://192.168.0.10:8006")
context = ssl.create_default_context(cafile=os.getenv("PVE_CA_FILE"))
if os.getenv("PVE_INSECURE") == "1":
    context = ssl._create_unverified_context()
request = urllib.request.Request(
    url + "/api2/json/cluster/resources",
    headers={
        "Authorization": "PVEAPIToken=" + os.environ["PVE_TOKEN_ID"] + "=" + os.environ["PVE_TOKEN_SECRET"]
    },
)
with urllib.request.urlopen(request, context=context, timeout=15) as result:
    print(json.dumps(json.load(result)["data"], indent=2))
